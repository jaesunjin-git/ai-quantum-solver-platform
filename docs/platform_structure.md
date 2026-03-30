# Platform Structure — 플랫폼 계층 구조

> 이 문서는 AI Quantum Solver Platform의 디렉토리 구조와 계층 경계를 정의합니다.
> 새로운 problem type을 추가하는 개발자는 이 문서를 먼저 읽어야 합니다.

## 핵심 원칙

1. **Platform은 problem type을 모른다.** engine/과 core/는 특정 problem type(crew scheduling, material optimization 등)에 의존하지 않는다.
2. **Problem type은 platform 위에 구현된다.** problem_types/{type}/ 안에서 platform 인터페이스를 구현한다.
3. **Domain은 knowledge다.** knowledge/domains/{domain}/는 산업별 지식(제약 정의, 파라미터 카탈로그)만 담고, 코드를 포함하지 않는다.
4. **MongoDB를 추가할 때 PostgreSQL 코드를 건드리지 않아도 되는 구조.** 새 problem type은 기존 problem type을 전혀 모르고도 구현할 수 있어야 한다.

## 설정 로딩 계층

```
problem_type 기본값     → 산업 도메인 override  → 고객(tenant) override
(crew_scheduling)        (railway)               (향후 constraint_policy)
```

## 디렉토리 구조

### Platform 계층 (problem type 무관)

```
core/                           # 인프라: auth, database, errors, session
engine/                         # 엔진 프레임워크
  ├── config_loader.py          # 3계층 설정 로딩 (problem_type → domain → tenant)
  ├── domain_registry.py        # problem_type ↔ domain 매핑
  ├── solver_registry.py        # solver 카탈로그 + 추천
  ├── pre_decision.py           # solver 추천 엔진
  ├── file_service.py           # 파일 파싱
  ├── math_model_generator.py   # LLM 수학모델 생성
  ├── template_model_builder.py # 템플릿 기반 모델 조립
  ├── post_processing.py        # 결과 후처리
  ├── result_interpreter_base.py# 결과 해석 base class
  ├── hybrid_strategy.py        # solver 전략
  ├── skills.py                 # UI 스킬 모델
  ├── tasks.py                  # Celery 비동기
  ├── compiler/                 # 솔버별 컴파일러 (solver-specific, problem-agnostic)
  │   ├── base.py               # 컴파일러 인터페이스
  │   ├── data_binder.py        # 데이터 바인딩
  │   ├── expression_parser.py  # 수식 파싱
  │   ├── ortools_compiler.py   # CP-SAT 컴파일러
  │   ├── cqm_compiler.py       # D-Wave CQM 컴파일러
  │   └── ...
  ├── executor/                 # 솔버별 실행기
  ├── feasibility/              # feasibility check 프레임워크
  ├── policy/                   # policy engine (YAML 선언적 규칙)
  ├── gates/                    # 검증 게이트 (Gate1~4)
  └── validation/               # 검증 프레임워크
configs/                        # 플랫폼 인프라 설정만
  ├── pipeline.yaml             # 워크플로우 단계 정의
  ├── skill_intents.yaml        # 대화 intent 라우팅
  ├── hybrid_strategy.yaml      # solver 전략
  ├── result_display.yaml       # 결과 UI 표시
  ├── problem_class_keywords.yaml # 문제 분류
  └── solvers/                  # solver 레지스트리
```

### Problem Type 계층 (problem type별 엔진 구현)

```
problem_types/
  └── crew_scheduling/          # 승무원 스케줄링
      ├── engine_defaults.yaml  # 이 problem type의 engine 기본값
      ├── param_field_mapping.yaml # 파라미터→config 매핑
      └── (향후: pipeline.py, sp_problem.py 등 HYBRID 분리 시)
```

### Domain 계층 (산업 도메인 지식 + 도메인별 코드)

```
knowledge/domains/              # YAML 지식팩
  ├── railway/
  │   ├── _index.yaml           # 도메인 메타 + code_module 매핑
  │   ├── engine_config.yaml    # crew_scheduling 기본값을 railway용으로 override
  │   ├── constraints.yaml      # 제약조건 카탈로그
  │   ├── parameter_catalog.yaml # 파라미터 정의
  │   ├── policies.yaml         # policy engine 규칙
  │   └── ...
  └── logistics/
      └── ...

domains/                        # 도메인별 Python 코드 (문제 유형 구현체)
  ├── crew/                     # crew scheduling 문제 유형 구현
  │   ├── duty_generator.py     # CrewDutyGenerator (BaseColumnGenerator 상속)
  │   ├── result_converter.py   # 결과 변환
  │   └── skills/               # 도메인 스킬 (problem_definition 등)
  ├── common/                   # 공유 스킬
  └── ...
```

## crew/railway 네이밍 관계

| 이름 | 의미 | 위치 | 재사용 |
|------|------|------|--------|
| `crew` (또는 `crew_scheduling`) | 문제 유형 | `domains/crew/`, `problem_types/crew_scheduling/` | bus, airline에서 재사용 가능 |
| `railway` | 산업 도메인 | `knowledge/domains/railway/` | railway 고유 지식 |

`engine/domain_registry.py`에서 `railway → domains.crew` 매핑.
`engine/config_loader.py`에서 `railway → crew_scheduling` problem type 매핑.

## HYBRID 파일 분리 전략 (향후)

현재 engine/에 있는 3개 HYBRID 파일의 분리 방향:

### solver_pipeline.py (가장 중요)

```python
# engine/solver_pipeline.py (PLATFORM — base class)
class BaseSolverPipeline(ABC):
    """problem type 무관한 공통 흐름"""
    async def run(self, math_model, solver_id, project_id, **kwargs):
        bound_data = self.bind_data(math_model, project_id)
        compiled = self.compile(bound_data, solver_id)
        result = self.execute(compiled, solver_id)
        return self.convert_result(result)

    @abstractmethod
    def compile(self, bound_data, solver_id): ...

    @abstractmethod
    def execute(self, compiled, solver_id): ...

# problem_types/crew_scheduling/pipeline.py
class CrewSchedulingPipeline(BaseSolverPipeline):
    """SP 기반 crew scheduling 파이프라인"""
    def compile(self, bound_data, solver_id):
        columns = self.generator.generate(tasks, params)
        sp_problem = build_sp_problem(columns, params)
        return sp_compiler.compile(sp_problem, math_model, params)

    def execute(self, compiled, solver_id):
        return executor.execute(compiled, time_limit)
```

### result_interpreter.py

```python
# engine/result_interpreter_base.py (PLATFORM — 이미 존재)
# engine/result_interpreter.py → problem_types/crew_scheduling/result_interpreter.py
```

### column_generator.py

```python
# engine/column_generator.py (PLATFORM — base class + FeasibleColumn은 유지)
# problem_types/crew_scheduling/column_generator.py (SP 전용 빔서치는 여기로)
# domains/crew/duty_generator.py (도메인 확장은 현위치 유지)
```

**분리 시점:** Phase 2 이후 또는 Material Science 착수 시.
**분리 원칙:** platform base에 crew 코드가 0줄이 되어야 함.
