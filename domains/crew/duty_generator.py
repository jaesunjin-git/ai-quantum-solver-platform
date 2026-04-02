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

from core.config import settings
from engine.column_generator import (
    BaseColumnConfig,
    BaseColumnGenerator,
    FeasibleColumn,
    SegmentType,
    TaskItem,
    _BeamState,
    is_depot_compatible,
)

logger = logging.getLogger(__name__)


# ── Crew 전용 설정 ───────────────────────────────────────────

@dataclass
class CrewDutyConfig(BaseColumnConfig):
    """승무원 스케줄링 전용 설정 (BaseColumnConfig 확장)"""

    # 야간 분류 기준
    night_threshold: int = 1020          # 17:00 이후 출발 → 야간
    day_start_earliest: int = 380        # 06:20 — 주간 최소 출고 시각

    # 자정 넘김 판단 기준 (gap 분류 + morning chain)
    # dep < 이 값 AND dep < arr → 다음날로 해석
    # None이면 day_start_earliest 사용
    midnight_crossing_threshold: Optional[int] = None

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
    evening_margin_minutes: int = 60     # night_threshold 전 마진 (_classify_gaps 수면 판정용)
    evening_search_margin: int = 120     # evening chain 탐색 범위 (night_threshold 전)
    rest_gap_margin_minutes: int = 30    # 수면 gap 판단 마진
    overnight_max_chain_ratio: float = 0.5  # overnight chain 최대 길이 비율 (max_tasks 대비)

    # 야간/overnight cost 가중치
    # (overnight_active_multiplier 제거 — driving은 주간/야간 동일 hard constraint)
    night_idle_cost_weight: float = 0.015     # 야간 유휴 비용 계수
    night_overhead_cost_weight: float = 0.005 # 야간 오버헤드 비용 계수
    night_short_penalty_ratio: float = 0.5   # 야간 short penalty 비율 (base의 50%)

    # depot 인접역 접미사 (예: "기지" → "대저기지" ↔ "대저")
    depot_suffixes: List[str] = None  # type: ignore

    def __post_init__(self):
        super().__post_init__()
        if self.depot_suffixes is None:
            self.depot_suffixes = []  # YAML에서 설정, 미설정 시 매칭 비활성화

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
    def from_params(cls, params: Dict, domain: str = settings.DEFAULT_DOMAIN) -> "CrewDutyConfig":
        """3계층 설정 로딩:
        1순위: params (사용자 운영 제약) — YAML param_field_mapping 기반 자동 매핑
        2순위: YAML config (엔진 튜닝)
        3순위: dataclass 기본값
        """
        from engine.config_loader import (
            load_yaml_into_dataclass, get_generator_yaml_paths, apply_param_mapping
        )

        cfg = cls()  # 3순위: dataclass 기본값

        # 2순위: YAML config (범용 + 도메인별)
        yaml_paths = get_generator_yaml_paths(domain)
        load_yaml_into_dataclass(cfg, *yaml_paths)

        # 1순위: params → config 자동 매핑 (YAML param_field_mapping 기반)
        applied = apply_param_mapping(cfg, params, domain)
        logger.info(f"CrewDutyConfig: {applied} params applied from mapping")

        # base setup/teardown은 주간 기준
        cfg.setup_time = cfg.setup_time_day
        cfg.teardown_time = cfg.teardown_time_day

        # block_combine derive
        if cfg.block_combine_max_gap == 0:
            cfg.block_combine_max_gap = cfg.max_idle_time
        if cfg.block_combine_top_k == 0:
            cfg.block_combine_top_k = int(cfg.max_columns_target * 0.3)

        # domain 저장 (feasibility pipeline YAML 로딩용)
        cfg._domain = domain

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

    def _extend_state(self, state, next_task):
        """crew 전용: segment 전환 판별.
        자정 넘김(수면 gap) 연결 시 segment를 OVERNIGHT으로 전환.
        Engine은 segment label만 보고 depot rule을 적용."""
        new_state = super()._extend_state(state, next_task)
        if new_state is None:
            return None

        # segment 전환: 저녁→새벽 자정 넘김이면 overnight
        cfg = self._crew_config
        mc_threshold = (
            cfg.midnight_crossing_threshold
            or cfg.overnight_morning_end
            or cfg.day_start_earliest
        )
        if (state.segment_type == SegmentType.DAYTIME
                and next_task.dep_time < mc_threshold
                and state.last_arr_time >= cfg.night_threshold - cfg.evening_margin_minutes):
            new_state.segment_type = SegmentType.OVERNIGHT

        return new_state

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

    def _classify_duty_type(self, column: FeasibleColumn) -> tuple:
        """duty type 분류. (duty_type, has_early, has_evening) 반환.
        column을 수정하지 않음."""
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
            return "overnight", has_early, has_evening
        elif is_night:
            return "night", has_early, has_evening
        else:
            return "day", has_early, has_evening

    def _check_domain_feasibility(self, column: FeasibleColumn) -> bool:
        """crew scheduling 도메인 규칙 검증.
        주의: column.column_type을 태깅함 (feasibility 실패 시에도).

        pre-tagged type(morning_only, evening_only)은 분류를 건너뛰고
        해당 type에 맞는 검증만 수행."""
        cfg = self._crew_config

        # morning_only/evening_only는 _post_generate에서 pre-tagged
        if column.column_type == "morning_only":
            return self._check_morning_only_feasibility(column, cfg)

        duty_type, has_early, has_evening = self._classify_duty_type(column)
        column.column_type = duty_type

        if duty_type == "overnight":
            return self._check_overnight_feasibility(column, cfg, self._task_map)
        elif duty_type == "night":
            return self._check_night_feasibility(column, cfg, has_early, has_evening)
        else:
            return self._check_day_feasibility(column, cfg, has_early)

    def _finalize_column(self, column: FeasibleColumn) -> FeasibleColumn:
        """feasibility 통과 후 도메인별 시간 보정 + cost 재계산"""
        if column.column_type in ("night", "overnight", "morning_only"):
            self._apply_night_corrections(column)
        return column

    def _check_morning_only_feasibility(self, column, cfg) -> bool:
        """새벽 전용 column feasibility.
        overnight의 morning part를 독립 duty로 사용.
        driving + span만 체크 (day_start_earliest 제한 적용 안함)."""
        return column.active_minutes <= cfg.max_active_time

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

        eff_end = end + cfg.day_minutes if end < start else end
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

        eff_end = end + cfg.day_minutes if end < start else end
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
        """overnight + morning-only + evening-only 생성.

        overnight: 저녁+수면+새벽 결합 column
        morning-only: 새벽 trip만 독립 column (coupling 해소)
        evening-only: 저녁 trip만 독립 column (coupling 해소)

        morning-only/evening-only가 있으면 solver가 overnight 대신
        개별 column으로 커버 가능 → exact cover 유연성 확보."""
        cfg = self._crew_config

        evening_chains = self._build_evening_chains()
        morning_chains = self._build_morning_chains()

        count = 0

        # 1) overnight (기존)
        if evening_chains and morning_chains:
            logger.info(f"Overnight: {len(evening_chains)} evening chains "
                         f"× {len(morning_chains)} morning chains")
            count += self._combine_overnight_chains(
                columns, evening_chains, morning_chains, next_id
            )

        # 2) morning-only column 생성 제거
        # 새벽 trip은 반드시 overnight duty의 일부로만 커버.
        # 독립 morning_only duty는 주간/야간 어디에도 해당하지 않는
        # 유령 duty를 생성하고 night_crew_count 의미를 왜곡함.

        # 3) evening-only column (coupling 해소)
        if evening_chains:
            evening_only = self._build_evening_only_columns(
                columns, evening_chains, next_id + count
            )
            count += evening_only

        return count

    def _build_evening_chains(self) -> List[List[TaskItem]]:
        """저녁 trip chain 구축"""
        cfg = self._crew_config
        evening_trips = sorted(
            [t for t in self.tasks if t.dep_time >= cfg.night_threshold - cfg.evening_search_margin],
            key=lambda t: t.dep_time
        )
        if not evening_trips:
            return []
        return self._build_chains(evening_trips, max_len=int(cfg.max_tasks * cfg.overnight_max_chain_ratio))

    def _build_morning_chains(self) -> List[List[TaskItem]]:
        """아침 trip chain 구축 (overnight의 아침 부분).
        midnight_crossing_threshold 기준으로 새벽 trip만 대상.
        이 범위를 넘는 trip은 day column에서 커버."""
        mc_threshold = (
            self._crew_config.midnight_crossing_threshold
            or self._crew_config.overnight_morning_end
            or self._crew_config.day_start_earliest
        )
        morning_trips = sorted(
            [t for t in self.tasks if t.dep_time < mc_threshold],
            key=lambda t: t.dep_time
        )
        cfg = self._crew_config
        if not morning_trips:
            return []
        return self._build_chains(morning_trips, max_len=int(cfg.max_tasks * cfg.overnight_max_chain_ratio))

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

                # 거점: overnight은 "출근 거점 = 퇴근 거점"만 강제.
                # 중간 경유/숙박 장소는 자유 (타 거점 OK).
                # 첫 trip(저녁 시작)의 거점 ∩ 마지막 trip(새벽 종료)의 거점
                if cfg.depot_policy_active:
                    ev_first_depots = ev_chain[0].allowed_depots
                    mo_last_depots = mo_chain[-1].allowed_depots
                    overnight_home = is_depot_compatible(ev_first_depots, mo_last_depots)
                    if not overnight_home and (ev_first_depots and mo_last_depots):
                        reject_reasons["depot_start_end_mismatch"] += 1
                        continue

                # 수면 gap 체크 (config 기반 — 하드코딩 제거)
                effective_mo_dep = mo_first.dep_time + cfg.day_minutes
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

                # overnight의 거점: 첫 trip ∩ 마지막 trip (출근=퇴근)
                overnight_depots = frozenset()
                if cfg.depot_policy_active:
                    overnight_depots = is_depot_compatible(
                        ev_chain[0].allowed_depots, mo_chain[-1].allowed_depots
                    )

                state = _BeamState(
                    trips=combined_ids,
                    last_arr_time=mo_chain[-1].arr_time,
                    last_end_location=mo_chain[-1].end_location,
                    total_driving=total_active,
                    first_dep_time=ev_chain[0].dep_time,
                    current_depots=overnight_depots,
                    segment_type=SegmentType.OVERNIGHT,
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

    def _build_morning_only_columns(
        self,
        columns: List[FeasibleColumn],
        morning_chains: List[List[TaskItem]],
        next_id: int,
    ) -> int:
        """새벽 trip만으로 구성된 독립 column 생성.
        overnight의 morning part를 별도 duty로 사용 가능 → coupling 해소.

        _try_build_column()은 column_type을 기반으로 day feasibility를 적용하므로,
        morning-only는 직접 구축하여 night feasibility만 적용."""
        cfg = self._crew_config
        count = 0

        for chain in morning_chains:
            if not chain:
                continue

            total_driving = sum(t.duration for t in chain)
            if total_driving > cfg.max_active_time:
                continue

            first_dep = chain[0].dep_time
            last_arr = chain[-1].arr_time
            prep = cfg.setup_time_night
            cleanup = cfg.teardown_time_night

            start_time = first_dep - prep
            end_time = last_arr + cleanup
            span = end_time - start_time if end_time > start_time else (end_time + cfg.day_minutes) - start_time

            # gap 분류
            trip_ids = [t.id for t in chain]
            regular_gap, inactive_gap = self._classify_gaps(trip_ids)
            pause = min(regular_gap, cfg.min_pause_time)
            idle = span - total_driving - prep - cleanup - pause - inactive_gap
            idle = max(0, idle)

            if idle > cfg.max_idle_time:
                continue

            # cost (야간 가중치)
            tc = len(chain)
            short_penalty = max(0, cfg.max_tasks - tc) * cfg.short_column_cost_weight
            cost = (1.0
                    + idle * cfg.night_idle_cost_weight
                    + (span - total_driving) * cfg.night_overhead_cost_weight
                    + short_penalty * cfg.night_short_penalty_ratio)

            col = FeasibleColumn(
                id=next_id + count,
                trips=trip_ids,
                column_type="morning_only",
                first_trip_dep=first_dep,
                last_trip_arr=last_arr,
                start_time=start_time,
                end_time=end_time,
                active_minutes=total_driving,
                span_minutes=span,
                elapsed_minutes=span - inactive_gap,
                idle_minutes=idle,
                pause_minutes=pause,
                inactive_minutes=inactive_gap,
                cost=cost,
                source="morning_only",
            )
            columns.append(col)
            count += 1

        logger.info(f"Morning-only: {count} columns from {len(morning_chains)} chains")
        return count

    def _build_evening_only_columns(
        self,
        columns: List[FeasibleColumn],
        evening_chains: List[List[TaskItem]],
        next_id: int,
    ) -> int:
        """저녁 trip만으로 구성된 독립 column 생성.
        overnight의 evening part를 별도 duty로 사용 가능 → coupling 해소.

        evening chain은 night feasibility로 검증 (night_threshold 이후 출발)."""
        cfg = self._crew_config
        count = 0

        for chain in evening_chains:
            if not chain:
                continue

            total_driving = sum(t.duration for t in chain)
            if total_driving > cfg.max_active_time:
                continue

            # evening chain은 _try_build_column 통과 가능 (night로 분류됨)
            chain_depots = self._resolve_chain_depots(chain) if cfg.depot_policy_active else frozenset()
            state = _BeamState(
                trips=[t.id for t in chain],
                last_arr_time=chain[-1].arr_time,
                last_end_location=chain[-1].end_location,
                total_driving=total_driving,
                first_dep_time=chain[0].dep_time,
                current_depots=chain_depots,
            )
            col = self._try_build_column(state, next_id + count)
            if col:
                col.source = "evening_only"
                columns.append(col)
                count += 1

        logger.info(f"Evening-only: {count} columns from {len(evening_chains)} chains")
        return count

    # ── 거점(depot) 헬퍼 ──────────────────────────────────────

    def _resolve_chain_depots(self, chain: List[TaskItem]) -> frozenset:
        """chain 내 모든 task의 allowed_depots 교집합 계산.
        wildcard(빈 set) task는 무시. 전부 wildcard면 빈 set 반환."""
        result: Optional[frozenset] = None
        for task in chain:
            if not task.allowed_depots:
                continue  # wildcard — 무시
            if result is None:
                result = task.allowed_depots
            else:
                result = result & task.allowed_depots
                if not result:
                    return frozenset()  # 교집합 없음
        return result or frozenset()

    # ── Greedy chain 구축 (overnight용) ───────────────────────

    def _build_chains(self, tasks_subset: List[TaskItem],
                       max_len: Optional[int] = None) -> List[List[TaskItem]]:
        """task subset에서 greedy forward chain 구축.

        중복 chain을 의도적으로 유지 — 동일 chain이라도 다른 상대방
        (evening/morning)과 조합되면 서로 다른 overnight column이 됨.
        chain 단계에서 dedup하면 조합 다양성이 소실됨."""
        cfg = self._crew_config
        if max_len is None:
            max_len = int(self._crew_config.max_tasks * self._crew_config.overnight_max_chain_ratio)

        chains: List[List[TaskItem]] = []

        for start in tasks_subset:
            chain = [start]
            chain_ids = {start.id}
            current = start

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

            chains.append(chain)

        return chains

    # ── gap 분류 override: 수면 gap을 inactive로 ─────────────

    def _classify_gaps(self, task_ids: list) -> tuple:
        """crew: 수면 gap(긴 gap)을 inactive로 분류.

        자정 넘김 판단: dep < arr AND dep < midnight_crossing_threshold.
        이 2조건 AND가 중요 — dep < arr만 사용하면 주간 duty에서
        시간순 역전이 아닌 경우도 자정 넘김으로 오판할 수 있다."""
        if len(task_ids) <= 1:
            return 0, 0

        cfg = self._crew_config
        # 자정 넘김 판단 기준: config → overnight_morning_end → day_start_earliest
        mc_threshold = (
            cfg.midnight_crossing_threshold
            or cfg.overnight_morning_end
            or cfg.day_start_earliest
        )
        regular_total = 0
        inactive_total = 0

        for i in range(len(task_ids) - 1):
            curr = self._task_map[task_ids[i]]
            next_t = self._task_map[task_ids[i + 1]]

            dep = next_t.dep_time
            # 자정 넘김: dep가 arr보다 작고, dep가 이른 아침 범위(< threshold)이면
            # 다음날로 해석. 두 조건 AND로 오판 방지.
            if dep < curr.arr_time and dep < mc_threshold:
                dep += cfg.day_minutes

            gap = dep - curr.arr_time
            if gap <= 0:
                continue

            # 수면 gap 판정: 저녁 도착 후 충분히 긴 gap이면 수면
            is_rest_gap = (
                gap >= cfg.min_sleep_minutes + cfg.rest_gap_margin_minutes
                and curr.arr_time >= cfg.night_threshold - cfg.evening_margin_minutes
                and next_t.dep_time < mc_threshold
            )

            if is_rest_gap:
                # gap = 중간입고정리 + 수면 + 중간출고준비
                overhead = cfg.teardown_time_night + cfg.setup_time_night
                actual_sleep = max(0, gap - overhead)
                inactive_total += actual_sleep
                regular_total += overhead
            else:
                regular_total += gap

        return regular_total, inactive_total

    # ── _find_next_tasks override: 야간 연결 ─────────────────

    def _find_next_tasks(self, state: _BeamState) -> List[TaskItem]:
        """crew 전용: base + 야간 자정 넘김 연결 허용.

        beam search에서 저녁 trip 이후 새벽 trip을 직접 연결하여
        overnight column을 생성. _post_generate()의 overnight와 별도로
        beam search의 다양한 경로를 통해 다양한 overnight 조합 확보.
        → 이것이 Set Partitioning exact cover의 핵심 다양성 원천."""
        candidates = super()._find_next_tasks(state)

        cfg = self._crew_config
        task_set = set(state.trips)
        mc_threshold = (
            cfg.midnight_crossing_threshold
            or cfg.overnight_morning_end
            or cfg.day_start_earliest
        )

        # 저녁 시간대에서만 자정 넘김 연결 시도
        if state.last_arr_time >= cfg.night_threshold - cfg.evening_margin_minutes:
            candidate_ids = {c.id for c in candidates}
            reachable = self._reachable_locations(state.last_end_location)
            for loc in reachable:
                for t in self._location_departures.get(loc, []):
                    if t.id in task_set or t.id in candidate_ids:
                        continue
                    if t.dep_time >= mc_threshold:
                        continue

                    # 자정 넘김: 새벽 dep + 1440 → 야간 arr 기준 gap 계산
                    effective_dep = t.dep_time + cfg.day_minutes
                    gap = effective_dep - state.last_arr_time
                    if (cfg.min_sleep_minutes <= gap
                            <= cfg.min_sleep_minutes + cfg.max_sleep_gap_extra):
                        if state.total_driving + t.duration <= self._get_max_active_time(state):
                            # overnight 연결(자정 넘김)에서는 depot 필터 미적용.
                            # 숙박 장소는 타 거점 OK — "출근=퇴근" 제약은 column 레벨에서 처리.
                            candidates.append(t)
                            candidate_ids.add(t.id)

        return candidates


# ── 하위 호환 alias ──────────────────────────────────────────
DutyGenerator = CrewDutyGenerator
GeneratorConfig = CrewDutyConfig
FeasibleDuty = FeasibleColumn
