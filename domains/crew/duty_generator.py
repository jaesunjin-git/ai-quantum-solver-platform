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
    overnight_morning_end: int = 480     # 08:00 — 숙박조 새벽 운행 최대 시각
    min_sleep_minutes: int = 240         # 최소 수면시간

    # 주간/야간별 준비·정리 시간
    setup_time_day: int = 60             # 주간 출고 준비
    teardown_time_day: int = 40          # 주간 입고 정리
    setup_time_night: int = 50           # 야간 익일출고 준비
    teardown_time_night: int = 30        # 야간 당일입고 정리

    # 야간 최대 근무시간 (수면 제외)
    max_span_time_night: int = 660

    # 숙박조(overnight) 최대 실근무시간 (수면 제외)
    # overnight은 저녁+새벽이므로 주간/야간보다 여유. None이면 max_span_time_night 사용.
    overnight_max_effective_span: Optional[int] = 720  # 12시간

    # depot 인접역 접미사 (예: "기지" → "대저기지" ↔ "대저")
    depot_suffixes: List[str] = None  # type: ignore

    def __post_init__(self):
        if self.depot_suffixes is None:
            self.depot_suffixes = ["기지"]

    @classmethod
    def from_params(cls, params: Dict) -> "CrewDutyConfig":
        """DataBinder bound_data['parameters']에서 crew 전용 설정 로딩"""
        cfg = cls()

        # base 공통 매핑
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
            'day_duty_start_earliest': 'day_start_earliest',
            'min_night_sleep_minutes': 'min_sleep_minutes',
        }
        for param_key, attr in _crew_mapping.items():
            val = params.get(param_key)
            if val is not None and isinstance(val, (int, float)):
                setattr(cfg, attr, int(val))

        # 주간 준비/정리
        cfg.setup_time_day = int(params.get('preparation_minutes_departure',
                                             params.get('preparation_minutes', 60)))
        cfg.teardown_time_day = int(params.get('cleanup_minutes_arrival',
                                                params.get('cleanup_minutes', 40)))
        # 야간 준비/정리
        cfg.setup_time_night = int(params.get('preparation_minutes_night', 50))
        cfg.teardown_time_night = int(params.get('cleanup_minutes_night', 30))

        # 야간 최대 근무
        cfg.max_span_time_night = int(params.get('max_work_minutes', 660))

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

    def _check_domain_feasibility(self, column: FeasibleColumn) -> bool:
        """crew scheduling 도메인 규칙 검증 (주간/야간/overnight 분기)"""
        cfg = self._crew_config
        task_map = self._task_map

        # ── 주간/야간 분류 ──
        first_dep = column.first_trip_dep
        last_arr = column.last_trip_arr

        cross_midnight = last_arr < first_dep
        has_early = any(task_map[tid].dep_time < cfg.day_start_earliest
                        for tid in column.trips)
        has_evening = any(task_map[tid].dep_time >= cfg.night_threshold
                          for tid in column.trips)

        is_overnight = has_early and has_evening
        # 야간: 자정 넘김 OR overnight만
        # 주간 근무자는 day_duty_end_latest(23:00)까지 근무 가능하므로
        # 저녁 시간대(17:00+) 출발만으로는 night 판정 안 함
        is_night = cross_midnight or is_overnight

        # ── column_type 설정 ──
        if is_overnight:
            column.column_type = "overnight"
        elif is_night:
            column.column_type = "night"
        else:
            column.column_type = "day"

        # ── 분기 검증 ──
        if is_overnight:
            return self._check_overnight_feasibility(column, cfg, task_map)
        elif is_night:
            return self._check_night_feasibility(column, cfg, task_map, has_early, has_evening)
        else:
            return self._check_day_feasibility(column, cfg, task_map, has_early)

    def _check_day_feasibility(self, column, cfg, task_map, has_early) -> bool:
        """주간 duty feasibility"""
        if has_early:
            return False  # 새벽 trip은 주간 duty 불가

        # 주간 출근 시각 제한: duty_start(= first_dep - prep) >= day_start_earliest
        # prep은 주간 기준 (setup_time_day)
        duty_start = column.first_trip_dep - cfg.setup_time_day
        if duty_start < cfg.day_start_earliest:
            return False

        return True

    def _check_night_feasibility(self, column, cfg, task_map, has_early, has_evening) -> bool:
        """야간 duty feasibility (overnight이 아닌 단순 야간)"""
        # 새벽만 있고 저녁 없으면 reject (overnight만 허용)
        if has_early and not has_evening:
            return False
        return self._apply_night_time_correction(column, cfg)

    def _check_overnight_feasibility(self, column, cfg, task_map) -> bool:
        """숙박조(overnight) feasibility — 저녁 + 수면 + 새벽"""
        # overnight_morning_end: optional cap (None이면 비활성)
        if cfg.overnight_morning_end is not None:
            morning_arrs = [
                task_map[tid].arr_time for tid in column.trips
                if task_map[tid].dep_time < cfg.night_threshold
            ]
            if morning_arrs and max(morning_arrs) > cfg.overnight_morning_end:
                return False

        # overnight용 max_span 적용 (일반 야간보다 여유)
        max_span = cfg.overnight_max_effective_span or cfg.max_span_time_night
        ok = self._apply_night_time_correction(column, cfg, max_effective_span=max_span)
        return ok

    def _apply_night_time_correction(self, column, cfg,
                                      max_effective_span: Optional[int] = None) -> bool:
        """야간/overnight 공통: 시간 보정 + 실근무시간 검증"""
        first_dep = column.first_trip_dep
        last_arr = column.last_trip_arr

        setup = cfg.setup_time_night
        teardown = cfg.teardown_time_night
        sleep = cfg.min_sleep_minutes

        start = first_dep - setup
        end = last_arr + teardown

        # effective span (자정 넘김)
        eff_end = end + 1440 if end < start else end
        span = eff_end - start
        work = span - sleep

        limit = max_effective_span if max_effective_span is not None else cfg.max_span_time_night
        if work > limit:
            return False

        # column 시간 보정
        column.start_time = start
        column.end_time = end
        column.span_minutes = span
        column.elapsed_minutes = work
        column.inactive_minutes = sleep

        # idle 재계산
        pause = column.pause_minutes
        idle = span - column.active_minutes - setup - teardown - pause - sleep
        column.idle_minutes = max(0, idle)

        # cost 재계산
        column.cost = 1.0 + column.idle_minutes * 0.01 + (span - column.active_minutes) * 0.005

        return True

    # ── Phase 2: Overnight duty 생성 ──────────────────────────

    def _post_generate(self, columns: List[FeasibleColumn], next_id: int) -> int:
        """overnight duty 생성: 저녁 chain + 수면 + 새벽 chain"""
        cfg = self._crew_config
        count = 0

        # 저녁 trip (dep >= night_threshold - 120)
        evening_trips = sorted(
            [t for t in self.tasks if t.dep_time >= cfg.night_threshold - 120],
            key=lambda t: t.dep_time
        )
        # 새벽~이른 아침 trip (overnight 새벽 chain 후보)
        # day_start_earliest까지만이 아니라 overnight_morning_end까지 포함
        # 숙박조는 06:20 이후에도 1~2 trip 더 운행 가능
        morning_cutoff = cfg.overnight_morning_end or cfg.day_start_earliest
        morning_trips = sorted(
            [t for t in self.tasks if t.dep_time < morning_cutoff],
            key=lambda t: t.dep_time
        )

        if not evening_trips or not morning_trips:
            logger.info(f"Overnight: skipped (evening={len(evening_trips)}, "
                         f"morning={len(morning_trips)})")
            return 0

        # chain 구축
        evening_chains = self._build_chains(evening_trips,
                                             max_len=cfg.max_tasks // 2)
        morning_chains = self._build_chains(morning_trips,
                                             max_len=cfg.max_tasks // 2)

        logger.info(f"Overnight: {len(evening_chains)} evening chains "
                     f"× {len(morning_chains)} morning chains")

        # 조합 + reject reason 계측
        from collections import Counter as _Counter
        reject_reasons = _Counter()
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

                # 수면 gap 체크
                effective_mo_dep = mo_first.dep_time + 1440
                gap = effective_mo_dep - ev_last.arr_time
                if gap < cfg.min_sleep_minutes:
                    reject_reasons["sleep_gap_too_short"] += 1
                    continue
                # 수면 gap 상한: min_sleep + 360분 (6시간 여유)
                # 실제 제한은 max_span_time_night이 담당
                if gap > cfg.min_sleep_minutes + 360:
                    reject_reasons["sleep_gap_too_long"] += 1
                    continue

                # 결합
                combined_ids = [t.id for t in ev_chain] + [t.id for t in mo_chain]
                if len(combined_ids) > cfg.max_tasks:
                    reject_reasons["max_tasks_exceeded"] += 1
                    continue

                total_active = sum(t.duration for t in ev_chain) + \
                               sum(t.duration for t in mo_chain)
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
                    # 원인 세분화: base 실패 vs domain 실패
                    # base _try_build_column 내부에서 어디서 실패하는지
                    # span/idle/active 수동 계산으로 판별
                    first_dep = ev_chain[0].dep_time
                    last_arr = mo_chain[-1].arr_time
                    prep = cfg.setup_time
                    cleanup = cfg.teardown_time
                    end = last_arr + cleanup
                    start = first_dep - prep
                    eff_end = end + 1440 if end < start else end
                    span = eff_end - start

                    if span > cfg.max_span_time * 1.5:
                        reject_reasons["base_span_too_long"] += 1
                    elif total_active > cfg.max_active_time:
                        reject_reasons["base_active_exceeded"] += 1
                    else:
                        reject_reasons["domain_feasibility_failed"] += 1

        logger.info(f"Overnight: {count} duties generated from {total_combos} combos")
        if reject_reasons:
            logger.info(f"Overnight reject reasons: {dict(reject_reasons)}")
        return count

    # ── Greedy chain 구축 (overnight용) ───────────────────────

    def _build_chains(self, tasks_subset: List[TaskItem],
                       max_len: int = 5) -> List[List[TaskItem]]:
        """task subset에서 greedy forward chain 구축"""
        cfg = self._crew_config
        chains: List[List[TaskItem]] = []

        for start in tasks_subset:
            chain = [start]
            current = start

            for _ in range(max_len - 1):
                reachable = self._reachable_locations(current.end_location)
                best = None
                for loc in reachable:
                    for nt in self._location_departures.get(loc, []):
                        if nt.id in {t.id for t in chain}:
                            continue
                        if nt.dep_time < current.arr_time:
                            continue
                        gap = nt.dep_time - current.arr_time
                        if gap <= cfg.max_gap:
                            best = nt
                            break
                    if best:
                        break

                if best:
                    chain.append(best)
                    current = best
                else:
                    break

            chains.append(chain)

        return chains

    # ── gap 분류 override: 수면 gap을 inactive로 ─────────────

    def _classify_gaps(self, task_ids: list) -> tuple:
        """crew: 수면 gap(긴 gap)을 inactive로 분류"""
        if len(task_ids) <= 1:
            return 0, 0

        cfg = self._crew_config
        regular_total = 0
        inactive_total = 0

        for i in range(len(task_ids) - 1):
            curr = self._task_map[task_ids[i]]
            next_t = self._task_map[task_ids[i + 1]]

            dep = next_t.dep_time
            if dep < curr.arr_time and dep < 480:
                dep += 1440  # 자정 넘김

            gap = dep - curr.arr_time
            if gap <= 0:
                continue

            # 수면 gap 판정: gap >= min_sleep_minutes이고
            # 저녁→새벽 전환 (current.arr >= night_threshold-60, next.dep < day_start)
            morning_cutoff = cfg.overnight_morning_end or cfg.day_start_earliest
            is_rest_gap = (
                gap >= cfg.min_sleep_minutes
                and curr.arr_time >= cfg.night_threshold - 60
                and next_t.dep_time < morning_cutoff
            )

            if is_rest_gap:
                inactive_total += gap
            else:
                regular_total += gap

        return regular_total, inactive_total

    # ── _find_next_tasks override: 야간 연결 ─────────────────

    def _find_next_tasks(self, state: _BeamState) -> List[TaskItem]:
        """crew 전용: base + 야간 자정 넘김 연결 허용"""
        # base 연결 (주간)
        candidates = super()._find_next_tasks(state)

        # 야간 연결: 저녁 도착 후 → 다음날 새벽 출발 (수면 gap)
        cfg = self._crew_config
        task_set = set(state.trips)

        if state.last_arr_time >= cfg.night_threshold - 60:
            reachable = self._reachable_locations(state.last_end_location)
            for loc in reachable:
                for t in self._location_departures.get(loc, []):
                    if t.id in task_set:
                        continue
                    morning_cutoff = cfg.overnight_morning_end or 480
                    if t.dep_time >= morning_cutoff:
                        continue

                    effective_dep = t.dep_time + 1440
                    gap = effective_dep - state.last_arr_time
                    if (cfg.min_sleep_minutes <= gap
                            <= cfg.min_sleep_minutes + 360):
                        if state.total_driving + t.duration <= cfg.max_active_time:
                            candidates.append(t)

        return candidates


# ── 하위 호환 alias ──────────────────────────────────────────
# 기존 engine/duty_generator.py를 사용하던 코드 호환
DutyGenerator = CrewDutyGenerator
GeneratorConfig = CrewDutyConfig
FeasibleDuty = FeasibleColumn
