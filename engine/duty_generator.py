"""
duty_generator.py ──────────────────────────────────────────
Feasible Duty 생성기 (Set Partitioning용).

승무원 스케줄링의 핵심: solver가 시간 제약을 풀지 않음.
대신 이 Generator가 모든 시간 검증이 완료된 feasible duty를 미리 생성하고,
solver는 "어떤 duty를 선택할지"만 결정 (Set Partitioning).

생성 알고리즘: Beam Search
  - 각 trip을 시작점으로 탐색
  - 다음 가능 trip을 연결하며 duty 확장
  - 각 depth에서 상위 beam_width개만 유지
  - feasibility 검증: driving/work/wait/break/sleep 전수 체크
  - dominance 제거: 같은 trip set → 더 나쁜 duty 제거

도메인 규칙:
  - prep/cleanup: duty 시작/종료 시 1회 (reporting layer)
  - break: duty 중 비운전 시간에서 확보
  - sleep: 야간 duty만 (숙박조)
  - 대기시간 = span - driving - prep - cleanup - break - sleep
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Feasible Duty 데이터 모델 ────────────────────────────────

@dataclass
class FeasibleDuty:
    """검증 완료된 하나의 duty 패턴"""
    id: int
    trips: List[int]            # trip_id 목록 (시간순)
    is_night: bool

    # 시간 정보 (분)
    first_trip_dep: int         # 첫 trip 출발 시각
    last_trip_arr: int          # 마지막 trip 도착 시각
    start_time: int             # actual duty 시작 (first_trip_dep - prep)
    end_time: int               # actual duty 종료 (last_trip_arr + cleanup)

    # 시간 분해
    driving_minutes: int        # 총 운전시간
    span_minutes: int           # end - start (또는 effective span for 야간)
    work_minutes: int           # span - sleep (야간) 또는 span (주간)
    wait_minutes: int           # span - driving - prep - cleanup - break - sleep
    break_minutes: int          # 비운전 시간 중 휴식
    sleep_minutes: int          # 야간만

    # 비용 (SP objective용)
    cost: float = 0.0
    source: str = "beam"           # "beam" | "greedy" | "overnight" | "fallback"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "trips": self.trips,
            "is_night": self.is_night,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "driving_minutes": self.driving_minutes,
            "span_minutes": self.span_minutes,
            "work_minutes": self.work_minutes,
            "wait_minutes": self.wait_minutes,
            "break_minutes": self.break_minutes,
            "sleep_minutes": self.sleep_minutes,
            "cost": round(self.cost, 2),
        }


# ── Generator 설정 ──────────────────────────────────────────

@dataclass
class GeneratorConfig:
    """Duty 생성 규칙 (constraints.yaml 기반)"""
    # 운전
    max_driving_minutes: int = 360
    avg_driving_target_minutes: int = 300

    # 근무
    max_work_minutes: int = 660          # 주간
    max_work_minutes_night: int = 660    # 야간 (수면 제외)

    # 대기
    max_wait_minutes: int = 300

    # 준비/정리 (reporting layer이지만 span 계산에 필요)
    prep_minutes_day: int = 60
    cleanup_minutes_day: int = 40
    prep_minutes_night: int = 50
    cleanup_minutes_night: int = 30

    # 휴식
    min_break_minutes: int = 30

    # 야간
    min_night_sleep_minutes: int = 240
    night_threshold: int = 1020          # 17:00 이후 출발 → 야간

    # 주간 시작 제한
    day_duty_start_earliest: int = 380   # 06:20

    # 연결 규칙
    max_gap_minutes: int = 60            # trip 간 최대 gap
    max_trips_per_duty: int = 10         # 320 trips / 45 duties ≈ 7.1 → 10으로 여유

    # Beam Search
    beam_width: int = 50
    max_duties_target: int = 20000

    @classmethod
    def from_params(cls, params: Dict) -> GeneratorConfig:
        """DataBinder bound_data['parameters']에서 설정 로딩"""
        cfg = cls()
        for attr in [
            'max_driving_minutes', 'max_work_minutes', 'max_wait_minutes',
            'min_break_minutes', 'min_night_sleep_minutes', 'night_threshold',
            'day_duty_start_earliest', 'max_trips_per_crew',
        ]:
            val = params.get(attr)
            if val is not None and isinstance(val, (int, float)):
                setattr(cfg, attr, int(val))

        # prep/cleanup
        cfg.prep_minutes_day = int(params.get('preparation_minutes_departure',
                                   params.get('preparation_minutes', 60)))
        cfg.cleanup_minutes_day = int(params.get('cleanup_minutes_arrival',
                                      params.get('cleanup_minutes', 40)))
        cfg.prep_minutes_night = int(params.get('preparation_minutes_night', 50))
        cfg.cleanup_minutes_night = int(params.get('cleanup_minutes_night', 30))

        if 'max_trips_per_crew' in params:
            cfg.max_trips_per_duty = int(params['max_trips_per_crew'])

        return cfg


# ── Trip 데이터 ──────────────────────────────────────────────

@dataclass
class TripInfo:
    """정규화된 trip 정보"""
    id: int
    dep_time: int       # 출발 시각 (분)
    arr_time: int       # 도착 시각 (분)
    duration: int       # 운행 시간 (분)
    dep_station: str
    arr_station: str
    direction: str


# ── Beam Search State ────────────────────────────────────────

@dataclass
class _BeamState:
    """Beam Search 탐색 상태"""
    trips: List[int]            # 현재까지 선택된 trip id
    last_arr_time: int          # 마지막 trip 도착 시각
    last_arr_station: str       # 마지막 trip 도착역
    total_driving: int          # 누적 운전시간
    first_dep_time: int         # 첫 trip 출발 시각
    score: float = 0.0          # 정렬 기준 (driving efficiency 등)


# ── Duty Generator ───────────────────────────────────────────

class DutyGenerator:
    """
    Feasible Duty 생성기 (Beam Search 기반).

    Usage:
        gen = DutyGenerator(trips, config)
        duties = gen.generate()
    """

    def __init__(self, trips: List[TripInfo], config: GeneratorConfig):
        self.trips = sorted(trips, key=lambda t: t.dep_time)
        self.config = config
        self._trip_map = {t.id: t for t in self.trips}

        # 역별 출발 trip 인덱스 (빠른 연결 검색)
        self._station_departures: Dict[str, List[TripInfo]] = {}
        for t in self.trips:
            self._station_departures.setdefault(t.dep_station, []).append(t)
        for k in self._station_departures:
            self._station_departures[k].sort(key=lambda t: t.dep_time)

    def generate(self) -> List[FeasibleDuty]:
        """전체 duty 생성 (beam search + pruning + dominance 제거)"""
        t0 = time.time()
        cfg = self.config

        all_duties: List[FeasibleDuty] = []
        duty_id = 0

        # ── 시간대별 beam search (전체 시간대 커버 보장) ──
        # 전체 trip을 시간대 그룹으로 분할하여 각 그룹별 독립 beam 실행.
        # 이렇게 하면 오후/저녁 trip도 beam에서 탈락하지 않음.
        time_groups = self._split_by_time_group(self.trips)
        logger.info(f"DutyGenerator: {len(time_groups)} time groups for beam search")

        for group_trips in time_groups:
            group_beam_duties = self._run_beam_for_group(group_trips, duty_id, cfg)
            all_duties.extend(group_beam_duties)
            duty_id += len(group_beam_duties)

            if len(all_duties) >= cfg.max_duties_target:
                break

        # ── (레거시 fallback: 위 그룹 beam에서 놓친 trip용) ──
        covered_by_beam = set()
        for d in all_duties:
            covered_by_beam.update(d.trips)
        uncovered_trips = [t for t in self.trips if t.id not in covered_by_beam]

        for start_trip in uncovered_trips:
            initial = _BeamState(
                trips=[start_trip.id],
                last_arr_time=start_trip.arr_time,
                last_arr_station=start_trip.arr_station,
                total_driving=start_trip.duration,
                first_dep_time=start_trip.dep_time,
                score=start_trip.duration,  # driving efficiency
            )

            # 시작 trip 자체가 1-trip duty
            duty = self._try_build_duty(initial, duty_id)
            if duty:
                all_duties.append(duty)
                duty_id += 1

            # Beam Search: 확장
            beam = [initial]
            for depth in range(cfg.max_trips_per_duty - 1):
                if not beam:
                    break

                next_beam: List[_BeamState] = []
                for state in beam:
                    # 다음 가능 trip 탐색
                    candidates = self._find_next_trips(state)
                    for next_trip in candidates:
                        new_state = self._extend_state(state, next_trip)
                        if new_state is None:
                            continue  # 조기 pruning

                        # 확장된 상태도 duty로 생성
                        duty = self._try_build_duty(new_state, duty_id)
                        if duty:
                            all_duties.append(duty)
                            duty_id += 1

                        next_beam.append(new_state)

                # Beam 제한: length diversity 유지 + score 기반
                beam = self._select_diverse_beam(next_beam, cfg.beam_width)

                # 전체 duty 수 제한
                if len(all_duties) >= cfg.max_duties_target:
                    logger.info(f"DutyGenerator: max target reached ({cfg.max_duties_target})")
                    break

            if len(all_duties) >= cfg.max_duties_target:
                break

        # ── 야간(숙박조) duty 별도 생성 패스 ──
        overnight_count = self._generate_overnight_duties(all_duties, duty_id)
        duty_id += overnight_count

        elapsed = time.time() - t0

        # ── 순서 변경: coverage 확보 먼저 → dominance 나중 (#6) ──
        # Coverage 검증 + uncovered trip fallback (필수)
        covered = set()
        for d in all_duties:
            covered.update(d.trips)
        all_trip_ids = {t.id for t in self.trips}
        uncovered = all_trip_ids - covered

        # ── 2차 패스: single coverage trip을 multi-trip duty에 포함 ──
        # beam search가 도달하지 못한 trip들에 대해
        # 역방향(이전 trip) + 순방향(다음 trip) greedy 확장
        single_trips = {
            tid for tid, cnt in
            {t: sum(1 for d in all_duties if t in d.trips) for t in all_trip_ids}.items()
            if cnt <= 1
        }
        if single_trips:
            extra = self._build_duties_for_single_trips(single_trips, all_duties, duty_id)
            duty_id += len(extra)
            all_duties.extend(extra)
            covered.update(tid for d in extra for tid in d.trips)

        # 미커버 trip → single-trip duty 강제 추가
        if uncovered:
            for tid in sorted(uncovered):
                trip = self._trip_map.get(tid)
                if trip:
                    single_state = _BeamState(
                        trips=[tid],
                        last_arr_time=trip.arr_time,
                        last_arr_station=trip.arr_station,
                        total_driving=trip.duration,
                        first_dep_time=trip.dep_time,
                    )
                    duty = self._try_build_duty(single_state, duty_id)
                    if duty:
                        all_duties.append(duty)
                        covered.add(tid)
                        duty_id += 1
                    else:
                        # feasibility 실패해도 강제 생성 (SP에서 커버리지 보장)
                        duty = self._build_forced_single_duty(trip, duty_id)
                        all_duties.append(duty)
                        covered.add(tid)
                        duty_id += 1

            still_uncovered = all_trip_ids - covered
            if still_uncovered:
                logger.error(
                    f"DutyGenerator: {len(still_uncovered)} trips STILL uncovered after fallback!"
                )
            else:
                logger.info(
                    f"DutyGenerator: {len(uncovered)} uncovered trips resolved via single-trip fallback"
                )

        # Dominance 제거 (coverage 확보 후)
        before_dom = len(all_duties)
        all_duties = self._remove_dominated(all_duties)

        # Coverage density 진단
        from collections import Counter as _Counter
        _trip_duty_cnt = _Counter()
        for d in all_duties:
            for tid in d.trips:
                _trip_duty_cnt[tid] += 1
        _density_dist = _Counter(_trip_duty_cnt.values())
        _source_dist = _Counter(d.source for d in all_duties)
        _avg_trips = sum(len(d.trips) for d in all_duties) / max(len(all_duties), 1)

        logger.info(
            f"DutyGenerator: {len(all_duties)} duties generated "
            f"({before_dom} before dominance, {elapsed:.1f}s, "
            f"coverage: {len(covered)}/{len(all_trip_ids)} trips, "
            f"avg_trips/duty: {_avg_trips:.1f}, "
            f"source: {dict(_source_dist)}, "
            f"coverage_density: {dict(sorted(_density_dist.items()))}"
        )

        return all_duties

    # ── Beam diversity 유지 ─────────────────────────────────

    @staticmethod
    def _select_diverse_beam(candidates: List["_BeamState"], beam_width: int) -> List["_BeamState"]:
        """length bucket별 top-k로 beam diversity 유지"""
        if len(candidates) <= beam_width:
            return candidates

        # length별 그룹화
        by_length: Dict[int, List["_BeamState"]] = {}
        for s in candidates:
            length = len(s.trips)
            by_length.setdefault(length, []).append(s)

        # 각 그룹 score 정렬
        for k in by_length:
            by_length[k].sort(key=lambda s: s.score, reverse=True)

        # round-robin으로 각 length에서 균등 추출
        result: List["_BeamState"] = []
        per_bucket = max(beam_width // max(len(by_length), 1), 5)

        for length in sorted(by_length.keys()):
            result.extend(by_length[length][:per_bucket])

        # 남은 슬롯은 전체 score 기준 fill
        if len(result) < beam_width:
            used = set(id(s) for s in result)
            remaining = [s for s in candidates if id(s) not in used]
            remaining.sort(key=lambda s: s.score, reverse=True)
            result.extend(remaining[:beam_width - len(result)])

        return result[:beam_width]

    # ── 시간대별 그룹 분할 ─────────────────────────────────

    def _split_by_time_group(self, trips: List[TripInfo], group_minutes: int = 120) -> List[List[TripInfo]]:
        """trip을 시간대 그룹으로 분할 (각 그룹 독립 beam search)"""
        if not trips:
            return []

        sorted_trips = sorted(trips, key=lambda t: t.dep_time)
        groups: List[List[TripInfo]] = []
        current_group: List[TripInfo] = [sorted_trips[0]]

        for t in sorted_trips[1:]:
            if t.dep_time - current_group[0].dep_time > group_minutes:
                groups.append(current_group)
                current_group = [t]
            else:
                current_group.append(t)

        if current_group:
            groups.append(current_group)

        return groups

    def _run_beam_for_group(
        self, group_trips: List[TripInfo], start_duty_id: int, cfg: "GeneratorConfig"
    ) -> List[FeasibleDuty]:
        """한 시간대 그룹에서 beam search 실행"""
        duties: List[FeasibleDuty] = []
        duty_id = start_duty_id

        for start_trip in group_trips:
            initial = _BeamState(
                trips=[start_trip.id],
                last_arr_time=start_trip.arr_time,
                last_arr_station=start_trip.arr_station,
                total_driving=start_trip.duration,
                first_dep_time=start_trip.dep_time,
                score=start_trip.duration,
            )

            duty = self._try_build_duty(initial, duty_id)
            if duty:
                duties.append(duty)
                duty_id += 1

            beam = [initial]
            for depth in range(cfg.max_trips_per_duty - 1):
                if not beam:
                    break

                next_beam: List[_BeamState] = []
                for state in beam:
                    candidates = self._find_next_trips(state)
                    for next_trip in candidates:
                        new_state = self._extend_state(state, next_trip)
                        if new_state is None:
                            continue

                        duty = self._try_build_duty(new_state, duty_id)
                        if duty:
                            duties.append(duty)
                            duty_id += 1

                        next_beam.append(new_state)

                next_beam.sort(key=lambda s: s.score, reverse=True)
                beam = next_beam[:cfg.beam_width]

        return duties

    # ── 다음 trip 탐색 ────────────────────────────────────

    def _find_next_trips(self, state: _BeamState) -> List[TripInfo]:
        """현재 상태에서 연결 가능한 다음 trip 목록"""
        cfg = self.config
        candidates = []
        trip_set = set(state.trips)

        # 같은 역 또는 인접 역에서 출발하는 trip 중 도착 후 가능한 것
        # (대저↔대저기지 등 depot 인접 역 연결 허용)
        search_stations = {state.last_arr_station}
        # 인접 역 추가: "기지" 접미사 매칭 (대저→대저기지, 대저기지→대저)
        base = state.last_arr_station.replace('기지', '').strip()
        for st in self._station_departures:
            st_base = st.replace('기지', '').strip()
            if st_base == base and st != state.last_arr_station:
                search_stations.add(st)

        station_trips = []
        for st in search_stations:
            station_trips.extend(self._station_departures.get(st, []))
        station_trips.sort(key=lambda t: t.dep_time)

        for t in station_trips:
            if t.id in trip_set:
                continue

            # 시간 순서 체크 (단, 자정 넘김 야간 연결은 예외)
            if t.dep_time < state.last_arr_time:
                # 야간 연결 가능성: 저녁 도착 후 다음날 새벽 출발
                if not (state.last_arr_time >= cfg.night_threshold - 60 and t.dep_time < 480):
                    continue

            gap = t.dep_time - state.last_arr_time

            # 야간 자정 넘김: gap이 음수 → 다음날로 해석
            if gap < 0 and state.last_arr_time >= cfg.night_threshold - 60 and t.dep_time < 480:
                effective_dep = t.dep_time + 1440
                night_gap = effective_dep - state.last_arr_time
                if (night_gap >= cfg.min_night_sleep_minutes and
                    night_gap <= cfg.min_night_sleep_minutes + 180):
                    if state.total_driving + t.duration <= cfg.max_driving_minutes:
                        candidates.append(t)
                continue

            # 일반 gap 제한
            if gap <= cfg.max_gap_minutes:
                if state.total_driving + t.duration <= cfg.max_driving_minutes:
                    candidates.append(t)
                continue

            # 야간 gap: 수면시간을 포함한 긴 gap 허용 (숙박조 패턴)
            # 저녁 trip 도착 후 수면 → 새벽 trip 출발
            # 새벽 trip은 dep_time < 480(08:00)이지만, 실제로는 "다음날"
            if (state.last_arr_time >= cfg.night_threshold - 60 and  # 저녁 도착 (~16:00+)
                t.dep_time < 480):  # 새벽 출발 (08:00 이전)
                # 다음날 새벽으로 해석: effective_dep = dep + 1440
                effective_dep = t.dep_time + 1440
                night_gap = effective_dep - state.last_arr_time
                if (night_gap >= cfg.min_night_sleep_minutes and  # 수면시간 확보
                    night_gap <= cfg.min_night_sleep_minutes + 180):  # 수면 + 여유 3시간
                    if state.total_driving + t.duration <= cfg.max_driving_minutes:
                        candidates.append(t)

        return candidates

    # ── 상태 확장 + 조기 pruning ──────────────────────────

    def _extend_state(self, state: _BeamState, next_trip: TripInfo) -> Optional[_BeamState]:
        """상태 확장. feasibility 가능성이 없으면 None 반환 (조기 pruning)"""
        cfg = self.config

        new_driving = state.total_driving + next_trip.duration
        new_trips = state.trips + [next_trip.id]

        # 조기 pruning: driving 90% 초과
        if new_driving > cfg.max_driving_minutes * 0.95:
            if new_driving > cfg.max_driving_minutes:
                return None

        # 조기 pruning: span 추정 (자정 넘김 보정)
        span_estimate = next_trip.arr_time - state.first_dep_time
        if span_estimate < 0:
            span_estimate += 1440  # 야간 자정 넘김

        if span_estimate > cfg.max_work_minutes + cfg.min_night_sleep_minutes:
            return None  # 야간이어도 너무 긴 span

        return _BeamState(
            trips=new_trips,
            last_arr_time=next_trip.arr_time,
            last_arr_station=next_trip.arr_station,
            total_driving=new_driving,
            first_dep_time=state.first_dep_time,
            # multi-objective score: trip 수 우선 + driving 효율
            # 긴 duty를 살려두는 bias (greedy 의존도 감소)
            score=len(new_trips) * 50 + new_driving - 0.3 * span_estimate,
        )

    # ── Duty 생성 + 전수 검증 ────────────────────────────

    def _try_build_duty(self, state: _BeamState, duty_id: int) -> Optional[FeasibleDuty]:
        """상태에서 FeasibleDuty 생성. feasibility 실패 시 None."""
        cfg = self.config

        first_dep = state.first_dep_time
        last_arr = state.last_arr_time
        driving = state.total_driving

        # ── duty 타입 판정: reject하지 않고 tagging ──
        # 주간 시작 제한보다 이른 trip → night candidate로 태깅 (reject 아님)
        cross_midnight = last_arr < first_dep
        is_night = first_dep >= cfg.night_threshold or cross_midnight

        # prep/cleanup (임시 - 아래에서 최종 결정)
        if is_night:
            prep = cfg.prep_minutes_night
            cleanup = cfg.cleanup_minutes_night
            sleep = cfg.min_night_sleep_minutes
        else:
            prep = cfg.prep_minutes_day
            cleanup = cfg.cleanup_minutes_day
            sleep = 0

        start_time = first_dep - prep
        end_time = last_arr + cleanup

        # 주간 시작 제한 체크 → 실패하면 night로 재분류 (reject 아님)
        if not is_night and start_time < cfg.day_duty_start_earliest - prep:
            # night candidate로 전환
            is_night = True
            prep = cfg.prep_minutes_night
            cleanup = cfg.cleanup_minutes_night
            sleep = cfg.min_night_sleep_minutes
            start_time = first_dep - prep
            end_time = last_arr + cleanup

        # effective span (야간: 자정 넘김)
        if is_night and last_arr < first_dep:
            effective_end = end_time + 1440
        elif is_night and end_time < start_time:
            effective_end = end_time + 1440
        else:
            effective_end = end_time

        span = effective_end - start_time

        # 근무시간 검증
        work = span - sleep
        if not is_night:
            if work > cfg.max_work_minutes:
                return None
        else:
            if work > cfg.max_work_minutes_night:
                return None

        # break 계산: trip 간 실제 gap에서 break 확보 가능 여부 (#1)
        total_gap = self._calculate_total_gap(state.trips)
        break_minutes = min(total_gap, cfg.min_break_minutes)
        # 주의: 중간 상태(2~3 trip)에서 gap 부족할 수 있으나,
        # trip 추가하면 gap 증가 → 최종 duty에서만 엄격 체크.
        # _try_build_duty는 "완성된 duty 후보"이므로 여기서 체크 OK.

        # 대기시간 검증 (순수 대기 = span - driving - prep - cleanup - break - sleep)
        wait = span - driving - prep - cleanup - break_minutes - sleep
        if wait < 0:
            wait = 0  # 빡빡한 스케줄 → 대기 없음
        if wait > cfg.max_wait_minutes:
            return None

        # driving 검증
        if driving > cfg.max_driving_minutes:
            return None

        # 비용 계산
        cost = 1.0 + wait * 0.01 + (span - driving) * 0.005

        return FeasibleDuty(
            id=duty_id,
            trips=list(state.trips),
            is_night=is_night,
            first_trip_dep=first_dep,
            last_trip_arr=last_arr,
            start_time=start_time,
            end_time=end_time,
            driving_minutes=driving,
            span_minutes=span,
            work_minutes=work,
            wait_minutes=wait,
            break_minutes=break_minutes,
            sleep_minutes=sleep,
            cost=cost,
        )

    # ── Single trip → multi-trip duty 구축 (2차 패스) ────

    def _build_duties_for_single_trips(
        self, single_trips: set, existing_duties: List[FeasibleDuty], start_id: int
    ) -> List[FeasibleDuty]:
        """single coverage trip에서 greedy로 multi-trip duty 구축 (역방향 + 순방향)"""
        cfg = self.config
        new_duties: List[FeasibleDuty] = []
        duty_id = start_id

        # 역별 도착 trip 인덱스 (역방향 탐색용)
        station_arrivals: Dict[str, List[TripInfo]] = {}
        for t in self.trips:
            station_arrivals.setdefault(t.arr_station, []).append(t)
        for k in station_arrivals:
            station_arrivals[k].sort(key=lambda t: t.arr_time)

        for tid in sorted(single_trips):
            trip = self._trip_map.get(tid)
            if trip is None:
                continue

            # greedy: 이 trip을 포함하는 duty 구축
            # 역방향으로 이전 trip 수집
            chain = [trip]
            current = trip

            # backward: 이전 trip 최대한 추가
            for _ in range(cfg.max_trips_per_duty - 1):
                # current.dep_station에 도착하는 trip 중 가장 가까운 것
                search_stations = {current.dep_station}
                base = current.dep_station.replace('기지', '').strip()
                for st in station_arrivals:
                    if st.replace('기지', '').strip() == base:
                        search_stations.add(st)

                best_prev = None
                best_gap = float('inf')
                for st in search_stations:
                    for pt in reversed(station_arrivals.get(st, [])):
                        if pt.id in {t.id for t in chain}:
                            continue
                        gap = current.dep_time - pt.arr_time
                        if 0 <= gap <= cfg.max_gap_minutes and gap < best_gap:
                            best_prev = pt
                            best_gap = gap
                if best_prev:
                    chain.insert(0, best_prev)
                    current = best_prev
                else:
                    break

            # forward: 다음 trip 추가
            current = chain[-1]
            for _ in range(cfg.max_trips_per_duty - len(chain)):
                search_stations = {current.arr_station}
                base = current.arr_station.replace('기지', '').strip()
                for st in self._station_departures:
                    if st.replace('기지', '').strip() == base:
                        search_stations.add(st)

                best_next = None
                best_gap = float('inf')
                for st in search_stations:
                    for nt in self._station_departures.get(st, []):
                        if nt.id in {t.id for t in chain}:
                            continue
                        if nt.dep_time < current.arr_time:
                            continue
                        gap = nt.dep_time - current.arr_time
                        if gap <= cfg.max_gap_minutes and gap < best_gap:
                            best_next = nt
                            best_gap = gap
                if best_next:
                    chain.append(best_next)
                    current = best_next
                else:
                    break

            # chain에서 target trip을 포함하는 max_trips_per_duty 길이 윈도우 생성
            if len(chain) >= 2:
                target_idx = next(i for i, t in enumerate(chain) if t.id == tid)
                # 다양한 윈도우 시작점 시도
                for win_start in range(max(0, target_idx - cfg.max_trips_per_duty + 1),
                                       min(len(chain), target_idx + 1)):
                    win_end = min(win_start + cfg.max_trips_per_duty, len(chain))
                    window = chain[win_start:win_end]
                    if len(window) < 2:
                        continue

                    state = _BeamState(
                        trips=[t.id for t in window],
                        last_arr_time=window[-1].arr_time,
                        last_arr_station=window[-1].arr_station,
                        total_driving=sum(t.duration for t in window),
                        first_dep_time=window[0].dep_time,
                    )
                    duty = self._try_build_duty(state, duty_id)
                    if duty:
                        duty.source = "greedy"
                        duty.cost *= 1.5  # greedy 페널티: SP가 beam duty 선호
                        new_duties.append(duty)
                        duty_id += 1
                        break  # 이 trip에 대해 1개 duty 생성이면 충분

        logger.info(f"Single trip 2nd pass: {len(new_duties)} new duties from {len(single_trips)} single trips")
        return new_duties

    # ── 야간(숙박조) duty 별도 생성 ─────────────────────

    def _generate_overnight_duties(self, all_duties: List[FeasibleDuty], start_id: int) -> int:
        """저녁 multi-trip + 수면 + 새벽 multi-trip 조합을 생성"""
        cfg = self.config
        count = 0

        # 저녁 beam duty (마지막 trip arr >= night_threshold - 60)
        evening_beam = [d for d in all_duties if d.source == "beam" and
                        d.last_trip_arr >= cfg.night_threshold - 60]
        # 새벽 beam duty (첫 trip dep < 480)
        morning_beam = [d for d in all_duties if d.source == "beam" and
                        d.first_trip_dep < 480]

        # fallback: beam duty 없으면 single trip
        if not evening_beam:
            evening_beam_trips = [t for t in self.trips if t.arr_time >= cfg.night_threshold - 60]
            for t in evening_beam_trips:
                evening_beam.append(FeasibleDuty(
                    id=-1, trips=[t.id], is_night=False,
                    first_trip_dep=t.dep_time, last_trip_arr=t.arr_time,
                    start_time=t.dep_time, end_time=t.arr_time,
                    driving_minutes=t.duration, span_minutes=t.duration,
                    work_minutes=t.duration, wait_minutes=0,
                    break_minutes=0, sleep_minutes=0,
                ))
        if not morning_beam:
            morning_beam_trips = [t for t in self.trips if t.dep_time < 480]
            for t in morning_beam_trips:
                morning_beam.append(FeasibleDuty(
                    id=-1, trips=[t.id], is_night=False,
                    first_trip_dep=t.dep_time, last_trip_arr=t.arr_time,
                    start_time=t.dep_time, end_time=t.arr_time,
                    driving_minutes=t.duration, span_minutes=t.duration,
                    work_minutes=t.duration, wait_minutes=0,
                    break_minutes=0, sleep_minutes=0,
                ))

        if not evening_beam or not morning_beam:
            return 0

        # 저녁 duty + 새벽 duty 결합
        for ev_duty in evening_beam[:30]:  # 상위 30개만 (조합 폭발 방지)
            for mo_duty in morning_beam[:30]:
                # 역 매칭
                ev_last = self._trip_map[ev_duty.trips[-1]]
                mo_first = self._trip_map[mo_duty.trips[0]]
                ev_base = ev_last.arr_station.replace('기지', '').strip()
                mo_base = mo_first.dep_station.replace('기지', '').strip()
                if ev_base != mo_base and ev_last.arr_station != mo_first.dep_station:
                    continue

                # gap 체크 (수면시간)
                effective_mo_dep = mo_first.dep_time + 1440
                night_gap = effective_mo_dep - ev_last.arr_time
                if night_gap < cfg.min_night_sleep_minutes:
                    continue
                if night_gap > cfg.min_night_sleep_minutes + 180:
                    continue

                # trip 수 제한
                combined_trips = ev_duty.trips + mo_duty.trips
                if len(combined_trips) > cfg.max_trips_per_duty:
                    continue

                total_driving = ev_duty.driving_minutes + mo_duty.driving_minutes
                if total_driving > cfg.max_driving_minutes:
                    continue

                state = _BeamState(
                    trips=combined_trips,
                    last_arr_time=mo_duty.last_trip_arr,
                    last_arr_station=mo_first.arr_station if mo_duty.trips else "",
                    total_driving=total_driving,
                    first_dep_time=ev_duty.first_trip_dep,
                )
                duty = self._try_build_duty(state, start_id + count)
                if duty:
                    duty.source = "overnight"
                    all_duties.append(duty)
                    count += 1

        logger.info(f"Overnight duties: {count} generated from {len(evening_beam)} evening × {len(morning_beam)} morning beam duties")
        return count

    # ── Gap 기반 break 계산 ─────────────────────────────

    def _calculate_total_gap(self, trip_ids: List[int]) -> int:
        """trip 간 총 gap (비운행 시간) 계산"""
        if len(trip_ids) <= 1:
            return 0

        total_gap = 0
        for i in range(len(trip_ids) - 1):
            curr = self._trip_map[trip_ids[i]]
            next_t = self._trip_map[trip_ids[i + 1]]

            # 다음 trip dep이 현재 trip arr보다 작으면 자정 넘김
            dep = next_t.dep_time
            if dep < curr.arr_time and dep < 480:
                dep += 1440

            gap = dep - curr.arr_time
            if gap > 0:
                total_gap += gap

        return total_gap

    # ── Dominance 제거 (Pareto) ──────────────────────────

    def _remove_dominated(self, duties: List[FeasibleDuty]) -> List[FeasibleDuty]:
        """Pareto dominance: 같은 trip set에서 모든 metrics가 나쁜 duty 제거"""
        # trip set → duty 목록
        by_trips: Dict[Tuple[int, ...], List[FeasibleDuty]] = {}
        for d in duties:
            key = tuple(sorted(d.trips))
            by_trips.setdefault(key, []).append(d)

        result = []
        for key, group in by_trips.items():
            if len(group) == 1:
                result.append(group[0])
                continue

            # Pareto: d1 dominates d2 if d1 <= d2 on ALL metrics
            non_dominated = []
            for d in group:
                dominated = False
                for other in group:
                    if other is d:
                        continue
                    if (other.work_minutes <= d.work_minutes and
                        other.wait_minutes <= d.wait_minutes and
                        other.driving_minutes >= d.driving_minutes and
                        other.cost <= d.cost and
                        (other.work_minutes < d.work_minutes or
                         other.wait_minutes < d.wait_minutes or
                         other.driving_minutes > d.driving_minutes or
                         other.cost < d.cost)):
                        dominated = True
                        break
                if not dominated:
                    non_dominated.append(d)

            result.extend(non_dominated)

        return result

    # ── 강제 single-trip duty (Coverage 보장용) ──────────

    def _build_forced_single_duty(self, trip: TripInfo, duty_id: int) -> FeasibleDuty:
        """feasibility 검증 없이 단일 trip duty 강제 생성 (coverage 보장)"""
        cfg = self.config
        is_night = trip.dep_time >= cfg.night_threshold or trip.dep_time < 480

        if is_night:
            prep = cfg.prep_minutes_night
            cleanup = cfg.cleanup_minutes_night
        else:
            prep = cfg.prep_minutes_day
            cleanup = cfg.cleanup_minutes_day

        start_time = trip.dep_time - prep
        end_time = trip.arr_time + cleanup
        span = end_time - start_time if end_time > start_time else (end_time + 1440) - start_time

        return FeasibleDuty(
            id=duty_id,
            trips=[trip.id],
            is_night=is_night,
            first_trip_dep=trip.dep_time,
            last_trip_arr=trip.arr_time,
            start_time=start_time,
            end_time=end_time,
            driving_minutes=trip.duration,
            span_minutes=span,
            work_minutes=span,
            wait_minutes=max(0, span - trip.duration - prep - cleanup - cfg.min_break_minutes),
            break_minutes=cfg.min_break_minutes,
            sleep_minutes=0,
            cost=10.0,  # 높은 비용 → solver가 가급적 피하도록
            source="fallback",
        )


# ── Helper: trips.csv에서 TripInfo 로딩 ──────────────────────

def load_trips_from_csv(csv_path: str) -> List[TripInfo]:
    """정규화된 trips.csv에서 TripInfo 목록 로딩"""
    import csv

    trips = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            trips.append(TripInfo(
                id=int(row['trip_id']),
                dep_time=int(row['trip_dep_time']),
                arr_time=int(row['trip_arr_time']),
                duration=int(row['trip_duration']),
                dep_station=row.get('dep_station', ''),
                arr_station=row.get('arr_station', ''),
                direction=row.get('direction', ''),
            ))

    return trips
