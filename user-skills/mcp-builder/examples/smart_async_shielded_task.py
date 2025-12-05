import asyncio
import time
import uuid
from dataclasses import dataclass, field

@dataclass
class JobMeta:
    id: str
    label: str
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    result: dict | None = None
    task: asyncio.Task | None = None

STATE = type("State", (), {"jobs": {}})()

async def do_work(delay: float) -> dict:
    await asyncio.sleep(delay)
    return {"done_in_seconds": delay}

def finalize(job: JobMeta, task: asyncio.Task) -> None:
    job.completed_at = time.time()
    try:
        job.result = task.result()
        job.status = "completed"
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.error = None
    except Exception as e:
        job.error = str(e)
        job.status = "failed"

async def run_with_budget(delay: float, timeout_seconds: float = 50.0) -> dict:
    job_id = str(uuid.uuid4())
    job = JobMeta(id=job_id, label=f"shielded:{delay}s", status="running", started_at=time.time())
    task = asyncio.create_task(do_work(delay))
    job.task = task
    STATE.jobs[job_id] = job
    task.add_done_callback(lambda t: finalize(job, t))

    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        job.result = result
        job.completed_at = time.time()
        job.status = "completed"
        return {"status": "completed", "result": result, "job": job}
    except asyncio.TimeoutError:
        return {
            "status": "pending",
            "job_id": job_id,
            "message": f"Exceeded {int(timeout_seconds)}s; running in background.",
            "smart_async": True,
        }
