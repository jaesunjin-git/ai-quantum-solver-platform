"""
domains/crew/duty_generator.py ────────────────────────────────
승무원 스케줄링 전용 Duty Generator.

BaseColumnGenerator를 상속하여 crew scheduling 도메인 로직 추가:
  - 주간/야간/숙박조(overnight) 분류
  - depot 인접역 매칭 (기지 ↔ 역)
  - 숙박조: 저녁 trip + 수면 + 새벽 trip 조합
  - 새벽 trip은 overnight duty에서만 사용
  - 새벽 chain 종료 제한 (config.overnight_morning_end)
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from engine.column_generator import (
    BaseColumnConfig,
    BaseColumnGenerator,
    FeasibleColumn,
    TaskItem,
    _BeamState,
)

logger = logging.getLogger(__name__)


# ── Crew 전용 설정 ───────────────────────────────────────────

@dataclass
class CrewDutyConfig(BaseColumnConfig):
    """승무원 스케줄링 전용 설정 (BaseColumnConfig 확장)"""

    # 야간 분류 기준
    night_threshold: int = 1020          # 17:00 이후 출발 → 야간
    day_start_earliest: int = 380        # 06:20 — 주간 최소 출고 시각

    # 숙박조
    overnight_morning_end: Optional[int] = None  # params에서 로딩, 미설정 시 미적용
    min_sleep_minutes: int = 240         # 최소 수면시간
    max_sleep_gap_extra: int = 360       # min_sleep + 이 값까지 수면 gap 허용

    # 주간 종료 제한
    day_end_latest: int = 1380           # 23:00 — 주간 최대 퇴근 시각

    # 주간/야간별 준비·정리 시간
    setup_time_day: int = 60             # 주간 출고 준비 (depot 출발)
    setup_time_relay: int = 40           # 주간 승계/편승 준비 (최소 prep)
    teardown_time_day: int = 40          # 주간 입고 정리
    setup_time_night: int = 50           # 야간 익일출고 준비
    teardown_time_night: int = 30        # 야간 당일입고 정리

    # 야간 최대 근무시간 (수면 제외)
    max_span_time_night: int = 660

    # 추가 파라미터 (params에서 로딩)
    max_total_stay_minutes: Optional[int] = None  # 회사 체류시간 상한
    min_night_rest_total: Optional[int] = None    # 야간 총 휴식 (수면+입출고 포함)
    recognized_wait_minutes: Optional[int] = None # 인정 대기시간
    post_trip_training_minutes: Optional[int] = None  # 강차 후 교육시간

    # 야간 연결 마진
    evening_margin_minutes: int = 60     # night_threshold 전 마진
    rest_gap_margin_minutes: int = 30    # 수면 gap 판단 마진

    # 야간/overnight cost 가중치
    # (overnight_active_multiplier 제거 — driving은 주간/야간 동일 hard constraint)
    night_idle_cost_weight: float = 0.015     # 야간 유휴 비용 계수
    night_overhead_cost_weight: float = 0.005 # 야간 오버헤드 비용 계수
    night_short_penalty_ratio: float = 0.5   # 야간 short penalty 비율 (base의 50%)

    # depot 인접역 접미사 (예: "기지" → "대저기지" ↔ "대저")
    depot_suffixes: List[str] = None  # type: ignore

    def __post_init__(self):
        if self.depot_suffixes is None:
            self.depot_suffixes = ["기지"]

    # params에서 로딩을 허용하는 필드 (운영 제약)
    # 튜닝 파라미터(night_idle_cost_weight 등)는 YAML에서만 설정 가능
    _PARAMS_LOADABLE = {
        *BaseColumnConfig._PARAMS_LOADABLE,
        'night_threshold', 'day_start_earliest', 'day_end_latest',
        'min_sleep_minutes', 'max_sleep_gap_extra', 'overnight_morning_end',
        'setup_time_day', 'setup_time_relay', 'teardown_time_day',
        'setup_time_night', 'teardown_time_night', 'max_span_time_night',
        'min_night_rest_total', 'max_total_stay_minutes',
        'recognized_wait_minutes', 'post_trip_training_minutes',
    }

    @classmethod
    def from_params(cls, params: Dict, domain: str = "railway") -> "CrewDutyConfig":
        """3계층 설정 로딩:
        1순위: params (사용자 운영 제약)
        2순위: YAML config (엔진 튜닝)
        3순위: dataclass 기본값
        """
        cfg = cls()  # 3순위: dataclass 기본값

        # 2순위: YAML config (범용 + 도메인별)
        from engine.config_loader import load_yaml_into_dataclass, get_generator_yaml_paths
        yaml_paths = get_generator_yaml_paths(domain)
        load_yaml_into_dataclass(cfg, *yaml_paths)

        # 1순위: 사용자 운영 제약 (키 이름 매핑)
        _base_mapping = {
            'max_driving_minutes': 'max_active_time',
            'max_work_minutes': 'max_span_time',
            'max_wait_minutes': 'max_idle_time',
            'min_break_minutes': 'min_pause_time',
        }
        for param_key, attr in _base_mapping.items():
            val = params.get(param_key)
            if val is not None and isinstance(val, (int, float)):
                setattr(cfg, attr, int(val))

        # crew 전용 매핑
        _crew_mapping = {
            'night_threshold': 'night_threshold',
            'night_duty_start_earliest': 'night_threshold',  # 별칭
            'day_duty_start_earliest': 'day_start_earliest',
            'day_duty_end_latest': 'day_end_latest',
            'min_night_sleep_minutes': 'min_sleep_minutes',
            'min_night_rest_total_minutes': 'min_night_rest_total',
            'overnight_morning_end': 'overnight_morning_end',
            'max_sleep_gap_extra': 'max_sleep_gap_extra',
            'max_total_stay_minutes': 'max_total_stay_minutes',
            'recognized_wait_minutes': 'recognized_wait_minutes',
            'post_trip_training_minutes': 'post_trip_training_minutes',
        }
        for param_key, attr in _crew_mapping.items():
            val = params.get(param_key)
            if val is not None and isinstance(val, (int, float)):
                setattr(cfg, attr, int(val))

        # 주간 준비/정리 — params에 값이 있고 None이 아닐 때만 덮어씀
        def _safe_int(val):
            """None/빈값 안전 변환"""
            if val is None:
                return None
            try:
                return int(val)
            except (ValueError, TypeError):
                return None

        prep_dep = _safe_int(params.get('preparation_minutes_departure'))
        prep_gen = _safe_int(params.get('preparation_minutes'))
        if prep_dep is not None:
            cfg.setup_time_day = prep_dep
        elif prep_gen is not None:
            cfg.setup_time_day = prep_gen

        prep_relay = _safe_int(params.get('preparation_minutes_relay'))
        if prep_relay is not None:
            cfg.setup_time_relay = prep_relay

        cleanup_arr = _safe_int(params.get('cleanup_minutes_arrival'))
        cleanup_gen = _safe_int(params.get('cleanup_minutes'))
        if cleanup_arr is not None:
            cfg.teardown_time_day = cleanup_arr
        elif cleanup_gen is not None:
            cfg.teardown_time_day = cleanup_gen

        # 야간 준비/정리
        prep_night = _safe_int(params.get('preparation_minutes_night'))
        if prep_night is not None:
            cfg.setup_time_night = prep_night
        cleanup_night = _safe_int(params.get('cleanup_minutes_night'))
        if cleanup_night is not None:
            cfg.teardown_time_night = cleanup_night

        # 야간 최대 근무 — 야간 전용 키 우선, fallback은 주간과 동일
        night_work = _safe_int(params.get('max_work_minutes_night'))
        if night_work is not None:
            cfg.max_span_time_night = night_work
        else:
            day_work = _safe_int(params.get('max_work_minutes'))
            if day_work is not None:
                cfg.max_span_time_night = day_work

        # base setup/teardown은 주간 기준 (기본값)
        cfg.setup_time = cfg.setup_time_day
        cfg.teardown_time = cfg.teardown_time_day

        # max_tasks
        if 'max_trips_per_crew' in params:
            cfg.max_tasks = int(params['max_trips_per_crew'])

        return cfg


# ── Crew Duty Generator ──────────────────────────────────────

class CrewDutyGenerator(BaseColumnGenerator):
    """
    승무원 스케줄링 전용 Column Generator.

    BaseColumnGenerator를 확장하여:
      - _eligible_tasks(): 주간 시작 가능 trip만 (새벽 trip 제외)
      - _can_connect(): depot 인접역 매칭
      - _check_domain_feasibility(): 주간/야간 분류 + 수면 + 새벽 제한
      - _finalize_column(): 야간/overnight 시간 보정
      - _post_generate(): overnight duty 별도 생성
    """

    @property
    def _crew_config(self) -> CrewDutyConfig:
        """config를 CrewDutyConfig으로 캐스팅"""
        return self.config  # type: ignore

    # ── 탐색 대상: 주간 eligible trip만 ───────────────────────

    def _eligible_tasks(self) -> List[TaskItem]:
        """주간 beam search 대상: 새벽 trip 제외 (dep >= day_start_earliest)"""
        cfg = self._crew_config
        eligible = [t for t in self.tasks if t.dep_time >= cfg.day_start_earliest]
        logger.info(f"Phase 1: {len(eligible)}/{len(self.tasks)} day-eligible trips")
        return eligible

    # ── prep/cleanup hook: 최소 prep으로 탐색 공간 확장 ──────

    def _get_prep_cleanup(self, state) -> tuple:
        """relay prep(40) 기준 — 가능한 duty를 넓게 생성"""
        cfg = self._crew_config
        return cfg.setup_time_relay, cfg.teardown_time_day

    def _get_full_prep(self) -> int:
        """cost 보정용 depot prep(60)"""
        return self._crew_config.setup_time_day

    def _can_combine(self, block_a, block_b, gap) -> bool:
        """crew: 위치 매칭 추가 — block_a 종료역 = block_b 시작역"""
        if not super()._can_combine(block_a, block_b, gap):
            return False
        # block_a의 마지막 trip 도착역 → block_b의 첫 trip 출발역
        last_task_a = self._task_map.get(block_a.trips[-1]) if block_a.trips else None
        first_task_b = self._task_map.get(block_b.trips[0]) if block_b.trips else None
        if not last_task_a or not first_task_b:
            return False
        return self._can_connect(last_task_a.end_location, first_task_b.start_location)

    def _get_max_active_time(self, state) -> int:
        """최대 활동시간(driving)은 주간/야간/overnight 구분 없이 동일.
        max_driving_minutes=360은 hard constraint — overnight에서도 완화 불가.
        overnight에서 완화되는 것은 span(수면 포함)이지 driving이 아님."""
        return self._crew_config.max_active_time

    def _get_morning_cutoff(self) -> int:
        """morning cutoff: overnight 아침 trip 범위 결정.
        1순위: params에서 명시적으로 설정된 overnight_morning_end
        2순위: 데이터 기반 — 이른 아침 trip의 max(arr_time)
        3순위: day_start_earliest (fallback)
        """
        cfg = self._crew_config
        if cfg.overnight_morning_end is not None:
            return cfg.overnight_morning_end

        # 데이터 기반: 이른 아침 trip(night_threshold 이전 + day_start 근처)의 도착 시각
        # "이른 아침" = night_threshold 이전이면서 주간 운행 시간대의 초반
        early_trips = [
            t for t in self.tasks
            if t.dep_time < cfg.night_threshold
            and t.dep_time < cfg.day_start_earliest + cfg.max_idle_time
        ]
        if early_trips:
            return max(t.arr_time for t in early_trips)

        return cfg.day_start_earliest

    # ── depot 인접역 매칭 ─────────────────────────────────────

    def _can_connect(self, from_location: str, to_location: str) -> bool:
        """depot 인접역 연결 허용 (예: 대저기지 → 대저)"""
        if from_location == to_location:
            return True

        cfg = self._crew_config
        from_base = from_location
        to_base = to_location
        for suffix in cfg.depot_suffixes:
            from_base = from_base.replace(suffix, '').strip()
            to_base = to_base.replace(suffix, '').strip()

        return from_base == to_base and from_base != ''

    # ── 도메인 feasibility: 주간/야간/overnight ───────────────

    def _classify_duty_type(self, column: FeasibleColumn) -> str:
        """duty type 분류 (순수 함수 — column 수정 없음)"""
        cfg = self._crew_config
        task_map = self._task_map

        first_dep = column.first_trip_dep
        last_arr = column.last_trip_arr

        cross_midnight = last_arr < first_dep
        has_early = any(task_map[tid].dep_time < cfg.day_start_earliest
                        for tid in column.trips)
        has_evening = any(task_map[tid].dep_time >= cfg.night_threshold
                          for tid in column.trips)

        is_overnight = has_early and has_evening
        exceeds_day_end = (last_arr + cfg.teardown_time_day > cfg.day_end_latest)
        is_night = cross_midnight or is_overnight or exceeds_day_end

        if is_overnight:
            return "overnight"
        elif is_night:
            return "night"
        else:
            return "day"

    def _check_domain_feasibility(self, column: FeasibleColumn) -> bool:
        """crew scheduling 도메인 규칙 검증 (column 수정 없음 — 순수 검증)"""
        cfg = self._crew_config
        task_map = self._task_map
        duty_type = self._classify_duty_type(column)

        # column_type 태깅 (분류 결과)
        column.column_type = duty_type

        if duty_type == "overnight":
            return self._check_overnight_feasibility(column, cfg, task_map)
        elif duty_type == "night":
            has_early = any(task_map[tid].dep_time < cfg.day_start_earliest
                            for tid in column.trips)
            has_evening = any(task_map[tid].dep_time >= cfg.night_threshold
                              for tid in column.trips)
            return self._check_night_feasibility(column, cfg, has_early, has_evening)
        else:
            has_early = any(task_map[tid].dep_time < cfg.day_start_earliest
                            for tid in column.trips)
            return self._check_day_feasibility(column, cfg, has_early)

    def _finalize_column(self, column: FeasibleColumn) -> FeasibleColumn:
        """feasibility 통과 후 도메인별 시간 보정 + cost 재계산"""
        if column.column_type in ("night", "overnight"):
            self._apply_night_corrections(column)
        return column

    def _check_day_feasibility(self, column, cfg, has_early) -> bool:
        """주간 duty feasibility (순수 검증)"""
        if has_early:
            return False

        duty_start = column.first_trip_dep - cfg.setup_time_relay
        if duty_start < cfg.day_start_earliest:
            return False

        duty_end = column.last_trip_arr + cfg.teardown_time_day
        if duty_end > cfg.day_end_latest:
            return False

        return True

    def _check_night_feasibility(self, column, cfg, has_early, has_evening) -> bool:
        """야간 duty feasibility (순수 검증)"""
        if has_early and not has_evening:
            return False
        return self._check_night_time_feasibility(column, cfg)

    def _check_overnight_feasibility(self, column, cfg, task_map) -> bool:
        """숙박조(overnight) feasibility (순수 검증)"""
        if cfg.overnight_morning_end is not None:
            morning_arrs = [
                task_map[tid].arr_time for tid in column.trips
                if task_map[tid].dep_time < cfg.night_threshold
            ]
            if morning_arrs and max(morning_arrs) > cfg.overnight_morning_end:
                return False

        # overnight span 체크: span - sleep ≤ max_span_time_night
        # (수면 제외 실근무시간이 제한 이내인지)
        return self._check_night_time_feasibility(column, cfg)

    def _check_night_time_feasibility(self, column, cfg) -> bool:
        """야간/overnight 공통: 수면 제외 실근무 span ≤ max_span_time_night (column 수정 없음)"""
        first_dep = column.first_trip_dep
        last_arr = column.last_trip_arr

        start = first_dep - cfg.setup_time_night
        end = last_arr + cfg.teardown_time_night

        eff_end = end + 1440 if end < start else end
        span = eff_end - start

        # 수면 제외 실근무 span
        effective_work = span - (column.inactive_minutes or cfg.min_sleep_minutes)
        return effective_work <= cfg.max_span_time_night

    def _apply_night_corrections(self, column) -> None:
        """야간/overnight column 시간 보정 + cost 재계산 (_finalize_column에서 호출)"""
        cfg = self._crew_config

        setup = cfg.setup_time_night
        teardown = cfg.teardown_time_night

        start = column.first_trip_dep - setup
        end = column.last_trip_arr + teardown

        eff_end = end + 1440 if end < start else end
        span = eff_end - start

        # 수면시간: _classify_gaps()에서 계산된 실제 inactive gap 사용
        # (min_sleep_minutes는 최솟값 fallback)
        _, actual_sleep = self._classify_gaps(column.trips)
        sleep = max(actual_sleep, cfg.min_sleep_minutes) if actual_sleep > 0 else 0
        work = span - sleep

        column.start_time = start
        column.end_time = end
        column.span_minutes = span
        column.elapsed_minutes = work
        column.inactive_minutes = sleep

        pause = column.pause_minutes
        idle = span - column.active_minutes - setup - teardown - pause - sleep
        column.idle_minutes = max(0, idle)

        # cost: 야간 가중치
        tc = len(column.trips)
        short_penalty = max(0, cfg.max_tasks - tc) * cfg.short_column_cost_weight
        column.cost = (1.0
                       + column.idle_minutes * cfg.night_idle_cost_weight
                       + (span - column.active_minutes) * cfg.night_overhead_cost_weight
                       + short_penalty * cfg.night_short_penalty_ratio)

    # ── Phase 2: Overnight duty 생성 ──────────────────────────

    def _post_generate(self, columns: List[FeasibleColumn], next_id: int) -> int:
        """overnight duty 생성: 저녁 chain + 수면 + 새벽 chain"""
        cfg = self._crew_config

        evening_chains = self._build_evening_chains()
        morning_chains = self._build_morning_chains()

        if not evening_chains or not morning_chains:
            logger.info(f"Overnight: skipped (evening={len(evening_chains)}, "
                         f"morning={len(morning_chains)})")
            return 0

        logger.info(f"Overnight: {len(evening_chains)} evening chains "
                     f"× {len(morning_chains)} morning chains")

        return self._combine_overnight_chains(
            columns, evening_chains, morning_chains, next_id
        )

    def _build_evening_chains(self) -> List[List[TaskItem]]:
        """저녁 trip chain 구축"""
        cfg = self._crew_config
        evening_trips = sorted(
            [t for t in self.tasks if t.dep_time >= cfg.night_threshold - 120],
            key=lambda t: t.dep_time
        )
        if not evening_trips:
            return []
        return self._build_chains(evening_trips, max_len=cfg.max_tasks // 2)

    def _build_morning_chains(self) -> List[List[TaskItem]]:
        """아침 trip chain 구축 (overnight의 아침 부분).
        별도 cutoff 없이 night_threshold 이전 전체 trip 대상.
        feasibility(driving, span)가 _combine_overnight_chains에서 자연스럽게 제한."""
        morning_trips = sorted(
            [t for t in self.tasks if t.dep_time < self._crew_config.night_threshold],
            key=lambda t: t.dep_time
        )
        cfg = self._crew_config
        if not morning_trips:
            return []
        return self._build_chains(morning_trips, max_len=cfg.max_tasks // 2)

    def _combine_overnight_chains(
        self,
        columns: List[FeasibleColumn],
        evening_chains: List[List[TaskItem]],
        morning_chains: List[List[TaskItem]],
        next_id: int,
    ) -> int:
        """저녁+새벽 chain 조합 → overnight duty 생성"""
        cfg = self._crew_config
        count = 0
        reject_reasons: Counter = Counter()
        total_combos = 0

        for ev_chain in evening_chains:
            ev_last = ev_chain[-1]
            for mo_chain in morning_chains:
                mo_first = mo_chain[0]
                total_combos += 1

                # 위치 매칭
                if not self._can_connect(ev_last.end_location, mo_first.start_location):
                    reject_reasons["location_mismatch"] += 1
                    continue

                # 수면 gap 체크 (config 기반 — 하드코딩 제거)
                effective_mo_dep = mo_first.dep_time + 1440
                gap = effective_mo_dep - ev_last.arr_time
                if gap < cfg.min_sleep_minutes:
                    reject_reasons["sleep_gap_too_short"] += 1
                    continue
                if gap > cfg.min_sleep_minutes + cfg.max_sleep_gap_extra:
                    reject_reasons["sleep_gap_too_long"] += 1
                    continue

                # 결합
                combined_ids = [t.id for t in ev_chain] + [t.id for t in mo_chain]
                if len(combined_ids) > cfg.max_tasks:
                    reject_reasons["max_tasks_exceeded"] += 1
                    continue

                total_active = sum(t.duration for t in ev_chain) + \
                               sum(t.duration for t in mo_chain)
                # driving 상한은 주간/야간 동일 — hard constraint
                if total_active > cfg.max_active_time:
                    reject_reasons["max_active_exceeded"] += 1
                    continue

                state = _BeamState(
                    trips=combined_ids,
                    last_arr_time=mo_chain[-1].arr_time,
                    last_end_location=mo_chain[-1].end_location,
                    total_driving=total_active,
                    first_dep_time=ev_chain[0].dep_time,
                )
                col = self._try_build_column(state, next_id + count)
                if col:
                    col.source = "overnight"
                    columns.append(col)
                    count += 1
                else:
                    reject_reasons["build_failed"] += 1

        rate = count / max(total_combos, 1)
        logger.info(
            f"Overnight: {count} duties from {total_combos} combos "
            f"(rate={rate:.3f})"
        )
        if reject_reasons:
            logger.info(f"Overnight reject reasons: {dict(reject_reasons)}")
        return count

    # ── Greedy chain 구축 (overnight용) ───────────────────────

    def _build_chains(self, tasks_subset: List[TaskItem],
                       max_len: int = 5) -> List[List[TaskItem]]:
        """task subset에서 greedy forward chain 구축 (중복 시그니처 제거)"""
        cfg = self._crew_config
        chains: List[List[TaskItem]] = []
        seen_signatures: set = set()

        for start in tasks_subset:
            chain = [start]
            current = start
            chain_ids = {start.id}

            for _ in range(max_len - 1):
                reachable = self._reachable_locations(current.end_location)
                nearest = None
                for loc in reachable:
                    for nt in self._location_departures.get(loc, []):
                        if nt.id in chain_ids:
                            continue
                        if nt.dep_time < current.arr_time:
                            continue
                        gap = nt.dep_time - current.arr_time
                        if gap <= cfg.max_gap:
                            nearest = nt
                            break
                    if nearest:
                        break

                if nearest:
                    chain.append(nearest)
                    chain_ids.add(nearest.id)
                    current = nearest
                else:
                    break

            # 중복 chain 제거
            sig = tuple(t.id for t in chain)
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                chains.append(chain)

        return chains

    # ── gap 분류 override: 수면 gap을 inactive로 ─────────────

    def _classify_gaps(self, task_ids: list) -> tuple:
        """crew: 수면 gap(긴 gap)을 inactive로 분류"""
        if len(task_ids) <= 1:
            return 0, 0

        cfg = self._crew_config
        morning_cutoff = self._get_morning_cutoff()
        regular_total = 0
        inactive_total = 0

        for i in range(len(task_ids) - 1):
            curr = self._task_map[task_ids[i]]
            next_t = self._task_map[task_ids[i + 1]]

            dep = next_t.dep_time
            # 자정 넘김: config 기반 (하드코딩 480 제거)
            if dep < curr.arr_time and dep < morning_cutoff:
                dep += 1440

            gap = dep - curr.arr_time
            if gap <= 0:
                continue

            # 수면 gap 판정
            is_rest_gap = (
                gap >= cfg.min_sleep_minutes + cfg.rest_gap_margin_minutes
                and curr.arr_time >= cfg.night_threshold - cfg.evening_margin_minutes
                and next_t.dep_time < morning_cutoff
            )

            if is_rest_gap:
                # gap = 중간입고정리 + 수면 + 중간출고준비
                # 이 overhead는 수면 전후의 "중간" 입출고 (duty 양 끝의 setup/teardown과 별도)
                # _apply_night_corrections()의 setup/teardown은 duty 시작/종료 1회
                # 여기의 overhead는 수면 구간 전후 → 이중 차감 아님
                overhead = cfg.teardown_time_night + cfg.setup_time_night
                actual_sleep = max(0, gap - overhead)
                inactive_total += actual_sleep
                regular_total += overhead  # 중간 입출고는 근무시간으로 분류
            else:
                regular_total += gap

        return regular_total, inactive_total

    # ── _find_next_tasks override: 야간 연결 ─────────────────

    # _find_next_tasks: base 그대로 사용 (override 없음)
    # overnight 연결은 _post_generate()에서 전담 — beam search에서 이중 생성 방지


# ── 하위 호환 alias ──────────────────────────────────────────
DutyGenerator = CrewDutyGenerator
GeneratorConfig = CrewDutyConfig
FeasibleDuty = FeasibleColumn
