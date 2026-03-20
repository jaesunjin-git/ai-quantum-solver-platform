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
        """crew scheduling 도메인 규칙 검증"""
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
        is_night = cross_midnight or is_overnight

        # ── column_type 설정 ──
        if is_overnight:
            column.column_type = "overnight"
        elif is_night:
            column.column_type = "night"
        else:
            column.column_type = "day"

        # ── 주간 duty: 새벽 trip 포함 불가 ──
        if not is_night:
            if has_early:
                return False

        # ── 야간 duty: 새벽만 있고 저녁 없으면 reject (overnight만 허용) ──
        if is_night and has_early and not has_evening:
            return False

        # ── overnight: 새벽 chain 종료 제한 ──
        if is_overnight:
            morning_arrs = [
                task_map[tid].arr_time for tid in column.trips
                if task_map[tid].dep_time < cfg.night_threshold
            ]
            if morning_arrs and max(morning_arrs) > cfg.overnight_morning_end:
                return False

        # ── 야간 시간 계산 보정 ──
        if is_night:
            # prep/cleanup 야간용
            setup = cfg.setup_time_night
            teardown = cfg.teardown_time_night
            sleep = cfg.min_sleep_minutes

            start = first_dep - setup
            end = last_arr + teardown

            # effective span (자정 넘김)
            if end < start:
                eff_end = end + 1440
            else:
                eff_end = end

            span = eff_end - start
            work = span - sleep

            if work > cfg.max_span_time_night:
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
            if idle < 0:
                idle = 0
            column.idle_minutes = idle

            # cost 재계산
            column.cost = 1.0 + idle * 0.01 + (span - column.active_minutes) * 0.005

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
        # 새벽 trip (dep < day_start_earliest)
        morning_trips = sorted(
            [t for t in self.tasks if t.dep_time < cfg.day_start_earliest],
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

        # 조합
        for ev_chain in evening_chains:
            ev_last = ev_chain[-1]
            for mo_chain in morning_chains:
                mo_first = mo_chain[0]

                # 위치 매칭
                if not self._can_connect(ev_last.end_location, mo_first.start_location):
                    continue

                # 수면 gap 체크
                effective_mo_dep = mo_first.dep_time + 1440
                gap = effective_mo_dep - ev_last.arr_time
                if gap < cfg.min_sleep_minutes:
                    continue
                if gap > cfg.min_sleep_minutes + 180:
                    continue

                # 결합
                combined_ids = [t.id for t in ev_chain] + [t.id for t in mo_chain]
                if len(combined_ids) > cfg.max_tasks:
                    continue

                total_active = sum(t.duration for t in ev_chain) + \
                               sum(t.duration for t in mo_chain)
                if total_active > cfg.max_active_time:
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

        logger.info(f"Overnight: {count} duties generated")
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
                    if t.dep_time >= 480:  # 08:00 이후는 새벽 아님
                        continue

                    effective_dep = t.dep_time + 1440
                    gap = effective_dep - state.last_arr_time
                    if (cfg.min_sleep_minutes <= gap
                            <= cfg.min_sleep_minutes + 180):
                        if state.total_driving + t.duration <= cfg.max_active_time:
                            candidates.append(t)

        return candidates


# ── 하위 호환 alias ──────────────────────────────────────────
# 기존 engine/duty_generator.py를 사용하던 코드 호환
DutyGenerator = CrewDutyGenerator
GeneratorConfig = CrewDutyConfig
FeasibleDuty = FeasibleColumn
