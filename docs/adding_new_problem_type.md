# Adding a New Problem Type

> 이 가이드는 플랫폼에 새로운 problem type을 추가하는 개발자를 위한 것입니다.
> 기존 problem type (crew_scheduling 등)의 코드를 읽을 필요가 없습니다.

## 개요

AI Quantum Solver Platform은 **problem type에 무관한 플랫폼 계층** 위에
각 problem type이 자신의 엔진을 구현하는 구조입니다.

```
Platform (engine/, core/)
    ↕ 인터페이스
Problem Type (problem_types/{your_type}/)
    ↕ 지식팩 참조
Domain Knowledge (knowledge/domains/{your_domain}/)
```

## Step 1: 디렉토리 생성

```
problem_types/{your_type}/
├── engine_defaults.yaml        # 이 problem type의 engine 기본 파라미터
├── param_field_mapping.yaml    # 사용자 파라미터 → config 필드 매핑 (선택)
└── (Python 모듈)               # 필수 인터페이스 구현
```

## Step 2: 필수 구현 인터페이스

### 2-1. SolverPipeline

platform이 호출하는 **진입점**입니다. 모든 problem type은 이 흐름을 구현해야 합니다.

```python
# problem_types/{your_type}/pipeline.py

class YourPipeline:
    """
    Platform이 호출하는 계약:
      result = pipeline.run(math_model, solver_id, project_id, **kwargs)
    """
    async def run(self, math_model, solver_id, project_id, **kwargs):
        # 1. 입력 데이터 바인딩
        bound_data = self.bind_data(math_model, project_id)

        # 2. 모델 컴파일 (solver-specific)
        compiled = self.compile(bound_data, solver_id)

        # 3. solver 실행
        result = self.execute(compiled, solver_id)

        # 4. 결과 변환
        return self.convert_result(result)
```

**활용 가능한 platform 모듈:**
- `engine/compiler/data_binder.py` — 파일 → 파라미터 바인딩
- `engine/compiler/ortools_compiler.py` — CP-SAT 컴파일 (일반 MIP)
- `engine/compiler/cqm_compiler.py` — D-Wave CQM 컴파일
- `engine/executor/` — solver 실행기

### 2-2. engine_defaults.yaml

이 problem type의 **기본 파라미터**입니다. 산업 도메인별 override가 이 위에 적용됩니다.

```yaml
# problem_types/{your_type}/engine_defaults.yaml
#
# 로딩 계층:
#   이 파일 (problem type 기본값)
#   → knowledge/domains/{domain}/engine_config.yaml (산업 도메인 override)
#   → confirmed_problem params (고객/프로젝트 override)

# 이 problem type에 필요한 파라미터를 자유롭게 정의
solver_timeout_sec: 300
max_iterations: 1000
# ...
```

### 2-3. ResultConverter

solver 결과를 **platform 표준 포맷**으로 변환합니다.

```python
def convert_result(solution, **kwargs) -> dict:
    """
    Returns:
        {
            "status": "OPTIMAL" | "FEASIBLE" | ...,
            "objective_value": float,
            "kpi": {...},
            "details": [...],
        }
    """
```

## Step 3: Platform 연동

### 3-1. domain_registry.py에 매핑 추가

```python
# engine/domain_registry.py
_DOMAIN_MODULES = {
    "your_domain": {
        "module": "problem_types.your_type.pipeline",
        "pipeline_class": "YourPipeline",
        ...
    },
}
```

### 3-2. config_loader.py에 problem type 등록

```python
# engine/config_loader.py
_PROBLEM_TYPE_ENGINE_DEFAULTS = {
    "crew_scheduling": "problem_types/crew_scheduling/engine_defaults.yaml",
    "your_type": "problem_types/your_type/engine_defaults.yaml",  # 추가
}

_DOMAIN_PROBLEM_TYPE = {
    "your_domain": "your_type",  # 추가
}
```

## Step 4: 도메인 지식팩 연결

```
knowledge/domains/{your_domain}/
├── _index.yaml                 # 도메인 메타 + problem_type, code_module 명시
├── engine_config.yaml          # engine_defaults.yaml의 도메인별 override
├── constraints.yaml            # 제약조건 카탈로그
├── parameter_catalog.yaml      # 파라미터 정의 (type, range, aliases)
└── ...
```

## 선택 구현 (필요한 경우에만)

### FeasibilityCheck handlers

`engine/feasibility/` 프레임워크를 사용하여 YAML 선언 기반 검증을 추가할 수 있습니다.

```python
# problem_types/{your_type}/feasibility_handlers.py
from engine.feasibility.base import FeasibilityCheck, CheckResult, FeasibilityCheckRegistry

class YourCustomCheck(FeasibilityCheck):
    def check(self, column, config, params):
        # ...
        return CheckResult(feasible=True)

FeasibilityCheckRegistry.register("your_check", YourCustomCheck)
```

### SideConstraint handlers (Phase 2 이후)

SP Side Constraint 프레임워크를 사용하여 solver 제약을 추가할 수 있습니다.

## 사용하지 않아도 되는 것

| 모듈 | 이유 |
|------|------|
| `problem_types/crew_scheduling/` | 다른 problem type의 구현 (참조 불필요) |
| Column Generation, Set Partitioning | crew_scheduling 전용 알고리즘 |
| DAY/NIGHT 구분, duty/trip 개념 | crew_scheduling 전용 도메인 개념 |
| `domains/crew/` | crew scheduling 도메인 확장 코드 |

## 예시: Material Science 추가

```
problem_types/
  └── material_optimization/
      ├── engine_defaults.yaml
      │     solver_timeout_sec: 600
      │     composition_resolution: 0.01
      │     max_candidates: 10000
      ├── pipeline.py
      │     class MaterialPipeline(BaseSolverPipeline):
      │         def compile(...): # 조성 탐색 모델 구축
      │         def execute(...): # D-Wave NL or CP-SAT 실행
      └── result_converter.py

knowledge/domains/
  └── battery_materials/
      ├── _index.yaml
      │     problem_type: material_optimization
      │     code_module: problem_types.material_optimization
      ├── engine_config.yaml
      │     # material_optimization 기본값을 배터리 재료용으로 override
      ├── constraints.yaml
      │     # 조성 제약, 공정 제약 등
      └── parameter_catalog.yaml

# config_loader.py에 2줄 추가:
_PROBLEM_TYPE_ENGINE_DEFAULTS["material_optimization"] = "problem_types/material_optimization/engine_defaults.yaml"
_DOMAIN_PROBLEM_TYPE["battery_materials"] = "material_optimization"
```
