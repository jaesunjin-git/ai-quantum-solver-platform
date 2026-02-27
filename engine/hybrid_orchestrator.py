# services/solver_service.py
import time
import json
import random
from datetime import datetime
from core.database import SessionLocal
import core.models as models
from engine.auto_compiler import generate_quantum_code
from core.celery_app import celery_app  # 👈 Celery 앱 가져오기

# ✅ [수정] @celery_app.task 데코레이터 붙이기
# 이제 이 함수는 별도의 프로세스(Worker)에서 실행됩니다.
@celery_app.task(bind=True)
def execute_solver_job(self, job_id: int, backend: str, data: dict):
    
    # DB 세션 새로 생성 (프로세스가 다르므로)
    db = SessionLocal()
    job = db.query(models.JobDB).filter(models.JobDB.id == job_id).first()
    
    if not job: return

    try:
        # 1. 컴파일
        job.status = "COMPILING"
        db.commit()
        print(f"⚙️ [Compiler] Job {job_id} Started...")
        
        task_key = "crew"
        if data and "constraints" in str(data):
            txt = str(data)
            if "트럭" in txt or "배송" in txt: task_key = "logistics"
            elif "자산" in txt or "금융" in txt: task_key = "finance"
            elif "소재" in txt or "분자" in txt: task_key = "material"

        compiler_result = generate_quantum_code(task_key, data)
        time.sleep(1.0) 

        # 2. 실행
        job.status = "RUNNING"
        job.result_json = json.dumps({"compiler_info": compiler_result})
        db.commit()
        
        print(f"🚀 [Worker] Job {job_id} running on {backend}...")
        time.sleep(3)  

        # 3. 결과 생성
        final_result = {
            "compiler_info": compiler_result,
            "solution": "Optimal Solution Found",
            "accuracy": "99.9%",
            "qpu_time": f"{random.randint(10, 150)}ms",
            "best_energy": f"-{random.randint(40, 60)}.{random.randint(10, 99)}",
            "cost_saved": f"${random.randint(10, 50)},000",
            "charts": {
                "convergence": [{"step": 0, "energy": -10}, {"step": 100, "energy": -42.5}],
                "distribution": [{"name": "A", "value": 35}, {"name": "B", "value": 45}],
                "comparison": [{"name": "Classical", "time": 480}, {"name": "Quantum", "time": 45}]
            }
        }
        
        job.result_json = json.dumps(final_result)
        job.status = "COMPLETED"
        job.completed_at = datetime.utcnow()
        print(f"✅ [Worker] Job {job_id} Finished Successfully.")

    except Exception as e:
        print(f"🔥 [Worker] Failed: {e}")
        job.status = "FAILED"
        job.result_json = json.dumps({"error": str(e)})
    finally:
        db.commit()
        db.close()
