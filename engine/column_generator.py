"""
column_generator.py ──────────────────────────────────────────
범용 Column Generator (Set Partitioning용).

도메인에 무관한 "시간 순서 기반 작업 시퀀스 생성기".
Beam Search로 feasible column(작업 묶음)을 생성하고,
solver는 "어떤 column을 선택할지"만 결정.

도메인별 확장: BaseColumnGenerator를 상속하여
_eligible_tasks(), _find_next_tasks(), _try_build_column() 등을 override.

용어:
  - column: solver가 선택하는 하나의 작업 묶음
  - task: 하나의 작업 단위
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Column 데이터 모델 (도메인 무관) ─────────────────────────

@dataclass
class FeasibleColumn:
    """검증 완료된 하나의 column (작업 묶음)"""
    id: int
    trips: List[int]                # task_id 목록 (시간순)
    column_type: str = "default"    # 도메인별 분류

    # 시간 정보 (분)
    first_trip_dep: int = 0         # 첫 task 시작 시각
    last_trip_arr: int = 0          # 마지막 task 종료 시각
    start_time: int = 0             # column 시작 (first_dep - prep)
    end_time: int = 0               # column 종료 (last_arr + cleanup)

    # 시간 분해 (분 단위)
    active_minutes: int = 0         # 총 활동시간 (task duration 합계)
    span_minutes: int = 0           # 경과시간 (end - start)
    elapsed_minutes: int = 0        # 실제 가용시간 (span - 비활동)
    idle_minutes: int = 0           # 유휴 대기시간
    pause_minutes: int = 0          # 휴식시간
    inactive_minutes: int = 0       # 비활동 시간 (장시간 column에서 사용)

    # 하위 호환 property (crew domain 등에서 사용)
    @property
    def driving_minutes(self) -> int:
        return self.active_minutes

    @property
    def work_minutes(self) -> int:
        return self.elapsed_minutes

    @property
    def wait_minutes(self) -> int:
        return self.idle_minutes

    @property
    def break_minutes(self) -> int:
        return self.pause_minutes

    @property
    def sleep_minutes(self) -> int:
        return self.inactive_minutes

    # 비용 (SP objective용)
    cost: float = 0.0
    source: str = "beam"            # "beam" | "greedy" | "fallback"

    def to_dict(self) -> dict:
        """직렬화"""
        return {
            "id": self.id,
            "trips": self.trips,
            "column_type": self.column_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "driving_minutes": self.driving_minutes,
            "span_minutes": self.span_minutes,
            "work_minutes": self.work_minutes,
            "wait_minutes": self.wait_minutes,
            "break_minutes": self.break_minutes,
            "sleep_minutes": self.sleep_minutes,
            "cost": round(self.cost, 2),
            "source": self.source,
        }


# 하위 호환 alias
FeasibleDuty = FeasibleColumn


# ── Generator 설정 (도메인 무관) ──────────────────────────────

@dataclass
class BaseColumnConfig:
    """Column 생성 규칙 (도메인 공통) — 시퀀스 제약 관점"""
    # 활동시간: task 수행에 직접 사용되는 시간 합계
    max_active_time: int = 360
    avg_active_target: int = 300

    # 총 경과시간: column 시작~종료 전체 span
    max_span_time: int = 660

    # 유휴시간: span 중 활동/준비/정리/휴식 외 시간
    max_idle_time: int = 300

    # 준비/정리 (column 시작/종료 시 1회)
    setup_time: int = 60
    teardown_time: int = 40

    # 최소 휴식시간
    min_pause_time: int = 30

    # 연결 규칙
    max_gap: int = 60
    max_tasks: int = 10

    # Beam Search
    beam_width: int = 50
    max_columns_target: int = 100000
    time_group_minutes: int = 120   # 시간대 그룹 너비
    span_estimate_multiplier: float = 1.5  # 조기 pruning 배율

    # Beam Score 가중치
    task_count_score_weight: int = 100   # trip 수 보너스 (score 단위)
    depth_bonus_score: int = 50          # min_column_depth 달성 보너스

    # Cost 가중치
    idle_cost_weight: float = 0.02       # 대기시간 비용 계수
    overhead_cost_weight: float = 0.01   # (span - driving) 비용 계수
    short_column_cost_weight: float = 0.05  # 짧은 column 페널티 계수
    depth_penalty_cost_weight: float = 0.3  # min_depth 미달 페널티 계수
    greedy_cost_multiplier: float = 1.5  # greedy fallback column 비용 배율
    fallback_cost: float = 10.0          # single-task fallback column 비용

    # Block Combine (block-gap-block 구조)
    block_combine_enabled: bool = False   # YAML에서 활성화
    block_combine_max_gap: int = 0        # 0=max_idle_time에서 자동 도출
    block_combine_per_block: int = 5      # block당 최대 결합 수
    block_combine_score_penalty: float = 0.7  # score의 wait penalty 계수
    block_combine_top_k: int = 0          # 0=max_columns_target*0.3에서 자동 도출
    block_combine_max_active_ratio: float = 0.5  # block active ≤ max_active * ratio
    block_combine_min_trips: int = 2      # 최소 trip 수
    block_combine_exclude_types: List = None  # 결합 제외 column_type (YAML에서 설정)

    # Diversity
    min_per_diversity_bucket: int = 5     # beam diversity 최소 bucket 크기
    min_task_coverage: int = 10           # diversity cap 후 최소 task coverage 보장

    # Single task fallback
    single_task_max_windows: int = 1      # single task 2nd pass에서 시도할 window 수
    fallback_include_pause: bool = True   # fallback column에 min_pause 포함 여부

    # 시간 상수
    day_minutes: int = 1440               # 1일 = 1440분 (26시간제 등 대비)

    # Adaptive CG
    acg_scale: float = 1.0          # 에스컬레이션 배율
    min_column_depth: int = 0       # 최소 task 수 (0=제한 없음, hint에서 설정)
    seed_trips: List = None         # bottleneck trip seed (ACG diversity용)
    pair_frequency: Dict = None     # trip-pair 빈도 (diversity penalty용)
    pair_frequency_max: int = 1     # 정규화용 최대 빈도
    diversity_weight: float = 100.0 # 정규화 후 penalty 가중치

    @property
    def effective_beam_width(self) -> int:
        return int(self.beam_width * self.acg_scale)

    @property
    def effective_max_columns(self) -> int:
        return int(self.max_columns_target * self.acg_scale)

    # params에서 로딩을 허용하는 필드 (운영 제약)
    # 튜닝 파라미터(idle_cost_weight 등)는 YAML에서만 설정 가능
    _PARAMS_LOADABLE = {
        'max_active_time', 'avg_active_target', 'max_span_time',
        'max_idle_time', 'setup_time', 'teardown_time',
        'min_pause_time', 'max_gap', 'max_tasks',
        'beam_width', 'max_columns_target',
    }

    @classmethod
    def from_params(cls, params: Dict, domain: str = None) -> "BaseColumnConfig":
        """3계층 설정 로딩:
        1순위: params (사용자 운영 제약, DataBinder 경유)
        2순위: YAML config (엔진 튜닝, 코드 변경 없이 수정 가능)
        3순위: dataclass 기본값 (최후 fallback)
        """
        cfg = cls()  # 3순위: dataclass 기본값

        # 2순위: YAML config (범용 + 도메인별)
        from engine.config_loader import load_yaml_into_dataclass, get_generator_yaml_paths
        yaml_paths = get_generator_yaml_paths(domain)
        load_yaml_into_dataclass(cfg, *yaml_paths)

        # 1순위: params → config 자동 매핑 (YAML param_field_mapping 기반)
        from engine.config_loader import apply_param_mapping
        apply_param_mapping(cfg, params, domain)

        # block_combine derive: 0이면 기존 params에서 자동 도출
        if cfg.block_combine_max_gap == 0:
            cfg.block_combine_max_gap = cfg.max_idle_time
        if cfg.block_combine_top_k == 0:
            cfg.block_combine_top_k = int(cfg.max_columns_target * 0.3)

        return cfg


# ── Task 데이터 ──────────────────────────────────────────────

@dataclass
class TaskItem:
    """정규화된 task 정보 (도메인 무관)"""
    id: int
    dep_time: int            # 시작 시각 (분)
    arr_time: int            # 종료 시각 (분)
    duration: int            # 소요 시간 (분)
    start_location: str      # 시작 위치
    end_location: str        # 종료 위치
    direction: str = ""      # 방향 (선택)

    # 하위 호환 property
    @property
    def dep_station(self) -> str:
        return self.start_location

    @property
    def arr_station(self) -> str:
        return self.end_location


# 하위 호환 alias
TripInfo = TaskItem


# ── Beam Search State ────────────────────────────────────────

@dataclass
class _BeamState:
    """Beam Search 탐색 상태"""
    trips: List[int]            # 현재까지 선택된 task id
    last_arr_time: int          # 마지막 task 종료 시각
    last_end_location: str      # 마지막 task 종료 위치
    total_driving: int          # 누적 작업시간
    first_dep_time: int         # 첫 task 시작 시각
    score: float = 0.0          # 정렬 기준


# ── Column Generator (도메인 무관 Base) ──────────────────────

class BaseColumnGenerator:
    """
    범용 Column Generator (Beam Search 기반).

    도메인별 확장 시 override할 메서드:
      - _eligible_tasks(): 탐색 대상 task 필터링
      - _find_next_tasks(): 다음 연결 가능 task 탐색 (역/위치 매칭 확장)
      - _try_build_column(): feasibility 검증 + 도메인별 분류
      - _post_generate(): 추가 column 생성 (예: 야간 조합)

    Usage:
        gen = BaseColumnGenerator(tasks, config)
        columns = gen.generate()
    """

    def __init__(self, tasks: List[TaskItem], config: BaseColumnConfig):
        self.tasks = sorted(tasks, key=lambda t: t.dep_time)
        self.config = config
        self._task_map = {t.id: t for t in self.tasks}

        # 위치별 출발 task 인덱스 (빠른 연결 검색)
        self._location_departures: Dict[str, List[TaskItem]] = {}
        for t in self.tasks:
            self._location_departures.setdefault(t.start_location, []).append(t)
        for k in self._location_departures:
            self._location_departures[k].sort(key=lambda t: t.dep_time)

    # ── 탐색 대상 필터링 훅 (override 가능) ──────────────────

    def _eligible_tasks(self) -> List[TaskItem]:
        """beam search 대상 task 목록. 도메인에서 override하여 필터링."""
        return self.tasks

    # ── 추가 column 생성 훅 (override 가능) ───────────────────

    def _post_generate(self, columns: List[FeasibleColumn], next_id: int) -> int:
        """beam 이후 추가 column 생성 (예: 야간 조합). 생성된 수 반환."""
        return 0

    # ── 메인 생성 ─────────────────────────────────────────────

    def generate(self) -> List[FeasibleColumn]:
        """column 생성 (beam search + fallback + dominance + diversity cap)"""
        t0 = time.time()
        cfg = self.config

        all_columns: List[FeasibleColumn] = []
        col_id = 0

        # ═════════════════════════════════════════════════════
        # Phase 1: Beam search (시간대별 그룹)
        # ═════════════════════════════════════════════════════
        eligible = self._eligible_tasks()
        time_groups = self._split_by_time_group(eligible, cfg.time_group_minutes)
        logger.info(f"Phase 1: {len(time_groups)} time groups "
                     f"({len(eligible)}/{len(self.tasks)} eligible tasks)")

        for group_tasks in time_groups:
            group_columns = self._run_beam_for_group(group_tasks, col_id, cfg)
            all_columns.extend(group_columns)
            col_id += len(group_columns)

        phase1_count = len(all_columns)
        logger.info(f"Phase 1 complete: {phase1_count} beam columns")

        # ═════════════════════════════════════════════════════
        # Phase 2: 도메인 확장 훅 (base: no-op)
        # ═════════════════════════════════════════════════════
        extra_count = self._post_generate(all_columns, col_id)
        col_id += extra_count
        if extra_count > 0:
            logger.info(f"Phase 2 complete: {extra_count} extra columns")

        # ═════════════════════════════════════════════════════
        # Phase B: Block Combine (block-gap-block 구조)
        # ═════════════════════════════════════════════════════
        if cfg.block_combine_enabled:
            combine_count = self._combine_blocks(all_columns, col_id)
            col_id += combine_count
            if combine_count > 0:
                logger.info(f"Phase B complete: {combine_count} block-combined columns")

        # ═════════════════════════════════════════════════════
        # Phase 2.5: Seed-based diversification (ACG용)
        # ═════════════════════════════════════════════════════
        if cfg.seed_trips:
            seed_columns = []
            for seed_tid in cfg.seed_trips:
                seed_task = self._task_map.get(seed_tid)
                if not seed_task:
                    continue
                # 이 trip을 시작점으로 beam search 실행
                seed_group = [seed_task]
                seed_cols = self._run_beam_for_group(seed_group, col_id, cfg)
                seed_columns.extend(seed_cols)
                col_id += len(seed_cols)

            if seed_columns:
                all_columns.extend(seed_columns)
                logger.info(
                    f"Phase 2.5: {len(seed_columns)} seed-diversified columns "
                    f"from {len(cfg.seed_trips)} bottleneck trips"
                )

        # ═════════════════════════════════════════════════════
        # Phase 3: Greedy fallback (미커버 task)
        # ═════════════════════════════════════════════════════
        covered = set()
        for c in all_columns:
            covered.update(c.trips)
        all_task_ids = {t.id for t in self.tasks}
        uncovered = all_task_ids - covered

        # 2차 패스: single coverage task → multi-task column
        _task_cov = Counter()
        for c in all_columns:
            for tid in c.trips:
                _task_cov[tid] += 1
        single_tasks = {tid for tid, cnt in _task_cov.items() if cnt <= 1}
        if single_tasks:
            extra = self._build_columns_for_single_tasks(single_tasks, all_columns, col_id)
            col_id += len(extra)
            all_columns.extend(extra)
            covered.update(tid for c in extra for tid in c.trips)

        # 미커버 → single-task column 강제 추가
        if uncovered:
            for tid in sorted(uncovered):
                task = self._task_map.get(tid)
                if task:
                    single_state = _BeamState(
                        trips=[tid],
                        last_arr_time=task.arr_time,
                        last_end_location=task.end_location,
                        total_driving=task.duration,
                        first_dep_time=task.dep_time,
                    )
                    col = self._try_build_column(single_state, col_id)
                    if col:
                        all_columns.append(col)
                        covered.add(tid)
                        col_id += 1
                    else:
                        col = self._build_forced_single_column(task, col_id)
                        all_columns.append(col)
                        covered.add(tid)
                        col_id += 1

            still_uncovered = all_task_ids - covered
            if still_uncovered:
                logger.error(f"Generator: {len(still_uncovered)} tasks STILL uncovered!")
            else:
                logger.info(f"Generator: {len(uncovered)} uncovered tasks resolved via fallback")

        # ═════════════════════════════════════════════════════
        # Dominance 제거 (coverage 확보 후)
        # ═════════════════════════════════════════════════════
        before_dom = len(all_columns)
        all_columns = self._remove_dominated(all_columns)

        # ═════════════════════════════════════════════════════
        # Diversity-aware cap
        # ═════════════════════════════════════════════════════
        _max_cap = cfg.effective_max_columns
        if len(all_columns) > _max_cap:
            all_columns = self._diversity_cap(all_columns, _max_cap)

        elapsed = time.time() - t0

        # ── 진단 로그 ──
        _trip_cnt = Counter()
        for c in all_columns:
            for tid in c.trips:
                _trip_cnt[tid] += 1
        _density = Counter(_trip_cnt.values())
        _source = Counter(c.source for c in all_columns)
        _avg = sum(len(c.trips) for c in all_columns) / max(len(all_columns), 1)

        logger.info(
            f"Generator: {len(all_columns)} columns "
            f"({before_dom} before dominance, {elapsed:.1f}s, "
            f"coverage: {len(covered)}/{len(all_task_ids)} tasks, "
            f"avg_tasks/column: {_avg:.1f}, "
            f"source: {dict(_source)}, "
            f"coverage_density: {dict(sorted(_density.items()))}"
        )

        return all_columns

    # ── Beam diversity 유지 ──────────────────────────────────

    def _select_diverse_beam(self, candidates: List[_BeamState], beam_width: int) -> List[_BeamState]:
        """길이 bucket별 top-k로 beam diversity 유지"""
        if len(candidates) <= beam_width:
            return candidates

        by_length: Dict[int, List[_BeamState]] = {}
        for s in candidates:
            by_length.setdefault(len(s.trips), []).append(s)
        for k in by_length:
            by_length[k].sort(key=lambda s: s.score, reverse=True)

        result: List[_BeamState] = []
        per_bucket = max(beam_width // max(len(by_length), 1), self.config.min_per_diversity_bucket)
        for length in sorted(by_length.keys()):
            result.extend(by_length[length][:per_bucket])

        if len(result) < beam_width:
            used = set(id(s) for s in result)
            remaining = [s for s in candidates if id(s) not in used]
            remaining.sort(key=lambda s: s.score, reverse=True)
            result.extend(remaining[:beam_width - len(result)])

        return result[:beam_width]

    # ── Diversity-aware cap ───────────────────────────────────

    def _diversity_cap(self, columns: List[FeasibleColumn], target: int) -> List[FeasibleColumn]:
        """시간대 × task 수 × column_type bucket별 균등 추출"""
        if len(columns) <= target:
            return columns

        buckets: Dict[tuple, List[FeasibleColumn]] = {}
        for c in columns:
            hour = c.first_trip_dep // self.config.time_group_minutes
            tcount = min(len(c.trips), self.config.max_tasks)
            key = (hour, tcount, c.column_type)
            buckets.setdefault(key, []).append(c)

        for k in buckets:
            buckets[k].sort(key=lambda c: c.cost)

        result: List[FeasibleColumn] = []
        per_bucket = max(target // max(len(buckets), 1), 1)
        for key in sorted(buckets.keys()):
            result.extend(buckets[key][:per_bucket])

        if len(result) < target:
            used_ids = {c.id for c in result}
            remaining = [c for c in columns if c.id not in used_ids]
            remaining.sort(key=lambda c: c.cost)
            result.extend(remaining[:target - len(result)])

        logger.info(f"Diversity cap: {len(columns)} → {len(result)} ({len(buckets)} buckets)")

        # ── coverage 보호: under-covered task의 column을 추가 보장 ──
        result_ids = {c.id for c in result}
        task_cov = Counter()
        for c in result:
            for tid in c.trips:
                task_cov[tid] += 1

        all_tids = {t.id for t in self.tasks}
        min_cov = getattr(self.config, 'min_task_coverage', 10)
        under = {tid for tid in all_tids if task_cov.get(tid, 0) < min_cov}

        if under:
            candidates = [c for c in columns if c.id not in result_ids]
            added = 0
            while under and candidates:
                best = max(candidates, key=lambda c: sum(1 for tid in c.trips if tid in under))
                if sum(1 for tid in best.trips if tid in under) == 0:
                    break
                result.append(best)
                result_ids.add(best.id)
                for tid in best.trips:
                    task_cov[tid] += 1
                    if task_cov[tid] >= min_cov:
                        under.discard(tid)
                candidates.remove(best)
                added += 1

            if added > 0:
                logger.info(f"Coverage repair: +{added} columns, "
                             f"remaining under-covered: {len(under)}")

        return result

    # ── 시간대별 그룹 분할 ────────────────────────────────────

    def _split_by_time_group(self, tasks: List[TaskItem],
                              group_minutes: int = 120) -> List[List[TaskItem]]:
        """task를 시간대 그룹으로 분할 (각 그룹 독립 beam search)"""
        if not tasks:
            return []

        sorted_tasks = sorted(tasks, key=lambda t: t.dep_time)
        groups: List[List[TaskItem]] = []
        current: List[TaskItem] = [sorted_tasks[0]]

        for t in sorted_tasks[1:]:
            if t.dep_time - current[0].dep_time > group_minutes:
                groups.append(current)
                current = [t]
            else:
                current.append(t)
        if current:
            groups.append(current)

        return groups

    # ── 그룹별 beam search ────────────────────────────────────

    def _run_beam_for_group(
        self, group_tasks: List[TaskItem], start_id: int, cfg: BaseColumnConfig
    ) -> List[FeasibleColumn]:
        """한 시간대 그룹에서 beam search 실행"""
        columns: List[FeasibleColumn] = []
        col_id = start_id

        for start_task in group_tasks:
            initial = _BeamState(
                trips=[start_task.id],
                last_arr_time=start_task.arr_time,
                last_end_location=start_task.end_location,
                total_driving=start_task.duration,
                first_dep_time=start_task.dep_time,
                score=start_task.duration,
            )

            # depth 1 column: min_column_depth <= 1일 때만 (과도 생성 방지)
            if cfg.min_column_depth <= 1:
                col = self._try_build_column(initial, col_id)
                if col:
                    columns.append(col)
                    col_id += 1

            beam = [initial]
            for depth in range(cfg.max_tasks - 1):
                if not beam:
                    break

                next_beam: List[_BeamState] = []
                for state in beam:
                    candidates = self._find_next_tasks(state)
                    for next_task in candidates:
                        new_state = self._extend_state(state, next_task)
                        if new_state is None:
                            continue

                        col = self._try_build_column(new_state, col_id)
                        if col:
                            columns.append(col)
                            col_id += 1

                        next_beam.append(new_state)

                beam = self._select_diverse_beam(next_beam, cfg.effective_beam_width)

        return columns

    # ── 다음 task 탐색 (override 가능) ────────────────────────

    def _can_connect(self, from_location: str, to_location: str) -> bool:
        """두 위치 간 연결 가능 여부 (기본: 동일 위치만). 도메인에서 override 가능."""
        return from_location == to_location

    def _reachable_locations(self, from_location: str) -> List[str]:
        """도달 가능한 출발 위치 목록. _can_connect 기반."""
        return [loc for loc in self._location_departures
                if self._can_connect(from_location, loc)]

    def _find_next_tasks(self, state: _BeamState) -> List[TaskItem]:
        """연결 가능한 다음 task 목록 (기본: 같은 위치 출발만)"""
        cfg = self.config
        candidates = []
        task_set = set(state.trips)

        # 연결 가능한 위치에서 출발하는 task (#4: heapq.merge로 정렬 최적화)
        reachable = self._reachable_locations(state.last_end_location)
        if len(reachable) == 1:
            location_tasks = self._location_departures.get(reachable[0], [])
        else:
            from heapq import merge as _merge
            iters = [self._location_departures.get(loc, []) for loc in reachable]
            location_tasks = list(_merge(*iters, key=lambda t: t.dep_time))

        for t in location_tasks:
            if t.id in task_set:
                continue
            if t.dep_time < state.last_arr_time:
                continue

            gap = t.dep_time - state.last_arr_time
            if gap <= cfg.max_gap:
                if state.total_driving + t.duration <= cfg.max_active_time:
                    candidates.append(t)

        return candidates

    # ── 상태 확장 + 조기 pruning ──────────────────────────────

    def _extend_state(self, state: _BeamState, next_task: TaskItem) -> Optional[_BeamState]:
        """상태 확장. feasibility 가능성 없으면 None (조기 pruning)."""
        cfg = self.config

        new_driving = state.total_driving + next_task.duration
        new_tasks = state.trips + [next_task.id]

        if new_driving > cfg.max_active_time:
            return None

        span_estimate = next_task.arr_time - state.first_dep_time
        if span_estimate < 0:
            span_estimate += cfg.day_minutes

        if span_estimate > cfg.max_span_time * cfg.span_estimate_multiplier:
            return None

        # beam score = objective proxy (#1: ObjectiveBuilder와 동일 방향)
        idle_est = max(0, span_estimate - new_driving)

        # ACG hint: 더 긴 column에 보너스
        depth_bonus = 0
        if cfg.min_column_depth > 0 and len(new_tasks) >= cfg.min_column_depth:
            depth_bonus = cfg.depth_bonus_score

        # Pair-frequency penalty: 기존 pool에서 흔한 trip 조합에 감점
        # 정규화: raw count를 max frequency로 나눠 0~1 범위로 만든 후 weight 적용
        # → trip 추가 보너스(100)와 균형 유지, column 길이를 줄이지 않음
        pair_penalty = 0
        if cfg.pair_frequency:
            pf = cfg.pair_frequency
            pf_max = max(cfg.pair_frequency_max, 1)
            w = cfg.diversity_weight
            raw_penalty = 0
            for existing_tid in state.trips:
                pair_key = (min(existing_tid, next_task.id),
                            max(existing_tid, next_task.id))
                raw_penalty += pf.get(pair_key, 0)
            # 정규화: trip 수로 나눠 평균 penalty, max로 나눠 0~1
            if state.trips:
                normalized = (raw_penalty / len(state.trips)) / pf_max
                pair_penalty = w * normalized  # 0 ~ diversity_weight

        return _BeamState(
            trips=new_tasks,
            last_arr_time=next_task.arr_time,
            last_end_location=next_task.end_location,
            total_driving=new_driving,
            first_dep_time=state.first_dep_time,
            score=cfg.task_count_score_weight * len(new_tasks) - idle_est + depth_bonus - pair_penalty,
        )

    # ── Column 생성 + feasibility 검증 (override 가능) ────────

    def _check_domain_feasibility(self, column: FeasibleColumn) -> bool:
        """도메인별 추가 feasibility 검증 (base: 항상 통과). override 가능.
        주의: 이 메서드는 column을 수정하지 않아야 함. 수정은 _finalize_column에서."""
        return True

    def _finalize_column(self, column: FeasibleColumn) -> FeasibleColumn:
        """feasibility 통과 후 도메인별 보정 적용 (base: 패스스루). override 가능."""
        return column

    def _get_prep_cleanup(self, state: _BeamState) -> tuple:
        """(prep, cleanup) 반환. 도메인에서 override하여 최소 prep 사용 가능."""
        return self.config.setup_time, self.config.teardown_time

    def _get_full_prep(self) -> int:
        """cost 보정용 최대 prep. 도메인에서 override."""
        return self.config.setup_time

    def _get_max_active_time(self, state: _BeamState) -> int:
        """최대 활동시간. 도메인에서 override (예: overnight 1.5배)."""
        return self.config.max_active_time

    def _try_build_column(self, state: _BeamState, col_id: int) -> Optional[FeasibleColumn]:
        """상태에서 FeasibleColumn 생성. feasibility 실패 시 None."""
        cfg = self.config

        first_dep = state.first_dep_time
        last_arr = state.last_arr_time
        driving = state.total_driving

        # prep/cleanup: hook으로 도메인별 최소값 사용 (탐색 공간 확장)
        prep, cleanup = self._get_prep_cleanup(state)

        start_time = first_dep - prep
        end_time = last_arr + cleanup

        # span (자정 넘김 보정)
        if end_time < start_time:
            span = (end_time + cfg.day_minutes) - start_time
        else:
            span = end_time - start_time

        work = span

        # break + inactive(수면 등) 계산: task 간 gap 분류 기반
        regular_gap, inactive_gap = self._classify_gaps(state.trips)
        break_minutes = min(regular_gap, cfg.min_pause_time)

        # 대기시간 = span - driving - full_prep - cleanup - break - inactive
        # wait는 full prep(depot) 기준으로 계산 (relay로 생성해도 wait는 보수적)
        full_prep = self._get_full_prep()
        wait = span - driving - full_prep - cleanup - break_minutes - inactive_gap
        if wait < 0:
            wait = 0
        if wait > cfg.max_idle_time:
            return None

        if driving > self._get_max_active_time(state):
            return None
        # span 체크: 비활동(수면 등)을 제외한 실근무 span ≤ max_span_time
        # overnight duty는 수면시간이 span에 포함되지만 근무가 아님
        effective_work = work - inactive_gap
        if effective_work > cfg.max_span_time:
            return None

        # cost: full prep 기준 보정 + short column penalty + discrimination 강화
        full_prep = self._get_full_prep()
        span_for_cost = span + (full_prep - prep)
        tc = len(state.trips)
        short_penalty = max(0, cfg.max_tasks - tc) * cfg.short_column_cost_weight

        # ACG hint: min_column_depth 미만이면 추가 페널티 (reject 대신 — coverage 보호)
        depth_penalty = 0.0
        if cfg.min_column_depth > 0 and tc < cfg.min_column_depth:
            depth_penalty = (cfg.min_column_depth - tc) * cfg.depth_penalty_cost_weight

        cost = (1.0
                + wait * cfg.idle_cost_weight
                + (span_for_cost - driving) * cfg.overhead_cost_weight
                + short_penalty
                + depth_penalty)

        column = FeasibleColumn(
            id=col_id,
            trips=list(state.trips),
            column_type="default",
            first_trip_dep=first_dep,
            last_trip_arr=last_arr,
            start_time=start_time,
            end_time=end_time,
            active_minutes=driving,
            span_minutes=span,
            elapsed_minutes=work - inactive_gap,
            idle_minutes=wait,
            pause_minutes=break_minutes,
            inactive_minutes=inactive_gap,
            cost=cost,
        )

        # 도메인별 추가 검증 (column 수정 금지 — 순수 검증)
        if not self._check_domain_feasibility(column):
            return None

        # 도메인별 보정 적용 (검증 통과 후)
        column = self._finalize_column(column)

        return column

    # ── Single task → multi-task column (2차 패스) ─────────────

    def _build_columns_for_single_tasks(
        self, single_tasks: set, existing: List[FeasibleColumn], start_id: int
    ) -> List[FeasibleColumn]:
        """single coverage task에서 greedy로 multi-task column 구축"""
        cfg = self.config
        new_columns: List[FeasibleColumn] = []
        col_id = start_id

        # 위치별 도착 task 인덱스 (역방향 탐색용)
        location_arrivals: Dict[str, List[TaskItem]] = {}
        for t in self.tasks:
            location_arrivals.setdefault(t.end_location, []).append(t)
        for k in location_arrivals:
            location_arrivals[k].sort(key=lambda t: t.arr_time)

        for tid in sorted(single_tasks):
            task = self._task_map.get(tid)
            if task is None:
                continue

            chain = [task]
            chain_ids = {task.id}
            current = task

            # backward: 이전 task
            for _ in range(cfg.max_tasks - 1):
                arrivals = location_arrivals.get(current.start_location, [])
                best_prev = None
                best_gap = float('inf')
                for pt in reversed(arrivals):
                    if pt.id in chain_ids:
                        continue
                    gap = current.dep_time - pt.arr_time
                    if 0 <= gap <= cfg.max_gap and gap < best_gap:
                        best_prev = pt
                        best_gap = gap
                if best_prev:
                    chain.insert(0, best_prev)
                    chain_ids.add(best_prev.id)
                    current = best_prev
                else:
                    break

            # forward: 다음 task
            current = chain[-1]
            for _ in range(cfg.max_tasks - len(chain)):
                departures = self._location_departures.get(current.end_location, [])
                best_next = None
                best_gap = float('inf')
                for nt in departures:
                    if nt.id in chain_ids:
                        continue
                    if nt.dep_time < current.arr_time:
                        continue
                    gap = nt.dep_time - current.arr_time
                    if gap <= cfg.max_gap and gap < best_gap:
                        best_next = nt
                        best_gap = gap
                if best_next:
                    chain.append(best_next)
                    chain_ids.add(best_next.id)
                    current = best_next
                else:
                    break

            # chain에서 target task를 포함하는 윈도우 생성
            if len(chain) >= 2:
                target_idx = next(i for i, t in enumerate(chain) if t.id == tid)
                _win_count = 0
                for win_start in range(max(0, target_idx - cfg.max_tasks + 1),
                                       min(len(chain), target_idx + 1)):
                    win_end = min(win_start + cfg.max_tasks, len(chain))
                    window = chain[win_start:win_end]
                    if len(window) < 2:
                        continue

                    state = _BeamState(
                        trips=[t.id for t in window],
                        last_arr_time=window[-1].arr_time,
                        last_end_location=window[-1].end_location,
                        total_driving=sum(t.duration for t in window),
                        first_dep_time=window[0].dep_time,
                    )
                    col = self._try_build_column(state, col_id)
                    if col:
                        col.source = "greedy"
                        col.cost *= cfg.greedy_cost_multiplier
                        new_columns.append(col)
                        col_id += 1
                        _win_count += 1
                        if _win_count >= cfg.single_task_max_windows:
                            break

        logger.info(f"Single task 2nd pass: {len(new_columns)} new columns "
                     f"from {len(single_tasks)} single tasks")
        return new_columns

    # ── Block Combine (block-gap-block) ─────────────────────

    def _can_combine(self, block_a: FeasibleColumn, block_b: FeasibleColumn,
                     gap: int) -> bool:
        """block 결합 가능 여부 — 시간 기반 기본 검증만. 도메인에서 override 가능."""
        cfg = self.config
        max_block_gap = cfg.block_combine_max_gap or cfg.max_idle_time
        if gap <= cfg.max_gap or gap > max_block_gap:
            return False
        if block_a.active_minutes + block_b.active_minutes > cfg.max_active_time:
            return False
        return True

    def _combine_blocks(self, columns: List[FeasibleColumn],
                        next_id: int) -> int:
        """기존 column(block)들을 대기시간 허용하여 결합 → block-gap-block column 생성.

        bisect 캐싱 + score 기반 필터 + per-block cap + early stop.
        결합 대상: driving ≤ max_active_time/2인 짧은 block끼리."""
        import bisect
        cfg = self.config
        t0 = time.time()

        # config derive: 0이면 기존 params에서 자동 도출
        max_block_gap = cfg.block_combine_max_gap or cfg.max_idle_time
        top_k = cfg.block_combine_top_k or int(cfg.effective_max_columns * 0.3)

        # 결합 대상: driving ≤ max_active/2인 짧은 block (두 block 합쳐야 max 이내)
        max_block_active = int(cfg.max_active_time * cfg.block_combine_max_active_ratio)

        def block_score(c: FeasibleColumn) -> float:
            return c.active_minutes - cfg.block_combine_score_penalty * c.idle_minutes

        # overnight column은 결합 대상 제외 (overnight끼리 결합은 무의미)
        exclude_types = cfg.block_combine_exclude_types or []
        eligible = [c for c in columns
                    if len(c.trips) >= cfg.block_combine_min_trips
                    and c.active_minutes <= max_block_active
                    and c.column_type not in exclude_types]
        scored = [(block_score(c), c) for c in eligible]
        scored.sort(key=lambda x: x[0], reverse=True)
        top_blocks = [c for _, c in scored[:top_k]]

        if not top_blocks:
            return 0

        # 시간순 정렬 (first_trip_dep 기준) — bisect 탐색용
        top_blocks.sort(key=lambda c: c.first_trip_dep)
        dep_times = [c.first_trip_dep for c in top_blocks]

        count = 0
        attempts = 0
        seen: set = set()

        for i, block_a in enumerate(top_blocks):
            combines_for_a = 0

            # bisect: block_a 종료 + max_gap 이후부터 탐색
            min_dep = block_a.last_trip_arr + cfg.max_gap
            max_dep = block_a.last_trip_arr + max_block_gap
            lo = bisect.bisect_left(dep_times, min_dep)
            hi = bisect.bisect_right(dep_times, max_dep)

            for j in range(lo, hi):
                block_b = top_blocks[j]

                # 시간 역전 방지
                if block_a.last_trip_arr >= block_b.first_trip_dep:
                    continue

                # 중복 방지
                sig = (block_a.id, block_b.id)
                if sig in seen:
                    continue
                seen.add(sig)

                gap = block_b.first_trip_dep - block_a.last_trip_arr
                attempts += 1

                # trip 겹침 방지
                if set(block_a.trips) & set(block_b.trips):
                    continue

                # 도메인별 결합 가능 여부 (base: 시간만, crew: 위치 추가)
                if not self._can_combine(block_a, block_b, gap):
                    continue

                # wait 초과 → early stop (시간 정렬이므로 이후 gap은 더 큼)
                combined_driving = block_a.active_minutes + block_b.active_minutes
                # 총 대기 = block 간 gap + 각 block 내부 대기
                estimated_wait = gap + block_a.idle_minutes + block_b.idle_minutes
                if estimated_wait > cfg.max_idle_time:
                    break

                # 결합 state 구축
                combined_trips = block_a.trips + block_b.trips
                last_task_b = self._task_map.get(block_b.trips[-1])
                state = _BeamState(
                    trips=combined_trips,
                    last_arr_time=block_b.last_trip_arr,
                    last_end_location=last_task_b.end_location if last_task_b else "",
                    total_driving=combined_driving,
                    first_dep_time=block_a.first_trip_dep,
                )

                col = self._try_build_column(state, next_id + count)
                if col:
                    col.source = "block_combine"
                    columns.append(col)
                    count += 1
                    combines_for_a += 1

                if combines_for_a >= cfg.block_combine_per_block:
                    break

        elapsed = time.time() - t0
        rate = count / max(attempts, 1)
        logger.info(
            f"[BLOCK COMBINE] attempts={attempts}, success={count}, "
            f"rate={rate:.3f}, elapsed={elapsed:.2f}s"
        )
        return count

    # ── Gap 기반 break 계산 ───────────────────────────────────

    def _classify_gaps(self, task_ids: List[int]) -> tuple:
        """
        task 간 gap을 (regular_gap, inactive_gap)으로 분류.

        base: 모든 gap을 regular로 분류 (inactive=0).
        도메인에서 override하여 수면/비활동 gap을 inactive로 분류 가능.

        Returns:
            (regular_gap_total, inactive_gap_total)
        """
        total = self._calculate_total_gap(task_ids)
        return total, 0  # base: 전부 regular

    def _calculate_total_gap(self, task_ids: List[int]) -> int:
        """task 간 총 gap (비작업 시간) 계산"""
        if len(task_ids) <= 1:
            return 0

        _day_min = self.config.day_minutes
        total_gap = 0
        for i in range(len(task_ids) - 1):
            curr = self._task_map[task_ids[i]]
            next_t = self._task_map[task_ids[i + 1]]

            dep = next_t.dep_time
            # 자정 넘김: 시간 역전(dep < arr)이면 다음날로 해석
            if dep < curr.arr_time:
                dep += _day_min

            gap = dep - curr.arr_time
            if gap > 0:
                total_gap += gap

        return total_gap

    # ── Dominance 제거 (Pareto) ───────────────────────────────

    @staticmethod
    def _dominates(a: FeasibleColumn, b: FeasibleColumn) -> bool:
        """a가 b를 Pareto 지배하는지 (4차원: elapsed, idle, active, cost)"""
        return (a.elapsed_minutes <= b.elapsed_minutes and
                a.idle_minutes <= b.idle_minutes and
                a.active_minutes >= b.active_minutes and
                a.cost <= b.cost and
                (a.elapsed_minutes < b.elapsed_minutes or
                 a.idle_minutes < b.idle_minutes or
                 a.active_minutes > b.active_minutes or
                 a.cost < b.cost))

    def _remove_dominated(self, columns: List[FeasibleColumn]) -> List[FeasibleColumn]:
        """Pareto dominance: 같은 task set에서 모든 metrics가 나쁜 column 제거.
        cost 순 정렬 + frontier sweep으로 O(n·k) (k=frontier 크기, 보통 작음)."""
        t0 = time.time()
        by_tasks: Dict[Tuple[int, ...], List[FeasibleColumn]] = {}
        for c in columns:
            key = tuple(sorted(c.trips))
            by_tasks.setdefault(key, []).append(c)

        # 프로파일링
        group_sizes = [len(g) for g in by_tasks.values()]
        group_sizes.sort()
        p95_idx = min(len(group_sizes) - 1, int(len(group_sizes) * 0.95))
        logger.info(
            f"dominance: cols={len(columns)} groups={len(by_tasks)} "
            f"max_group={max(group_sizes)} "
            f"avg_group={sum(group_sizes) / max(len(group_sizes), 1):.2f} "
            f"p95_group={group_sizes[p95_idx] if group_sizes else 0}"
        )

        result = []
        for key, group in by_tasks.items():
            if len(group) == 1:
                result.append(group[0])
                continue

            # cost 순 정렬 → cost 최소 column이 dominated될 확률 가장 낮음
            group.sort(key=lambda c: (c.cost, c.id))
            frontier = [group[0]]  # cost 최소는 항상 non-dominated

            for c in group[1:]:
                if not any(self._dominates(f, c) for f in frontier):
                    frontier.append(c)

            result.extend(frontier)

        elapsed = time.time() - t0
        logger.info(f"dominance: {len(columns)} → {len(result)} columns ({elapsed:.3f}s)")

        return result

    # ── 강제 single-task column (coverage 보장) ──────────────

    def _build_forced_single_column(self, task: TaskItem, col_id: int) -> FeasibleColumn:
        """feasibility 검증 없이 단일 task column 강제 생성"""
        cfg = self.config
        prep = cfg.setup_time
        cleanup = cfg.teardown_time
        start_time = task.dep_time - prep
        end_time = task.arr_time + cleanup
        span = end_time - start_time if end_time > start_time else (end_time + cfg.day_minutes) - start_time

        return FeasibleColumn(
            id=col_id,
            trips=[task.id],
            column_type="default",
            first_trip_dep=task.dep_time,
            last_trip_arr=task.arr_time,
            start_time=start_time,
            end_time=end_time,
            active_minutes=task.duration,
            span_minutes=span,
            elapsed_minutes=span,
            idle_minutes=max(0, span - task.duration - prep - cleanup - (cfg.min_pause_time if cfg.fallback_include_pause else 0)),
            pause_minutes=cfg.min_pause_time if cfg.fallback_include_pause else 0,
            inactive_minutes=0,
            cost=cfg.fallback_cost,
            source="fallback",
        )


# ── Helper: CSV에서 TaskItem 로딩 ────────────────────────────

def load_tasks_from_csv(csv_path: str) -> List[TaskItem]:
    """정규화된 CSV에서 TaskItem 목록 로딩"""
    import csv

    tasks = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            tasks.append(TaskItem(
                id=int(row.get('trip_id', row.get('task_id', 0))),
                dep_time=int(row.get('trip_dep_time', row.get('dep_time', 0))),
                arr_time=int(row.get('trip_arr_time', row.get('arr_time', 0))),
                duration=int(row.get('trip_duration', row.get('duration', 0))),
                start_location=row.get('dep_station', row.get('start_location', '')),
                end_location=row.get('arr_station', row.get('end_location', '')),
                direction=row.get('direction', ''),
            ))

    return tasks


# 하위 호환 alias
load_trips_from_csv = load_tasks_from_csv
