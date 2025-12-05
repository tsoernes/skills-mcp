---
title: "Update: Smart Async with Time Threshold Switch (50s)"
created_at: 20251204T134536Z
kind: note
---
### Update: Smart Async with Time Threshold (50s)

Prefer a time-threshold approach to smart async: attempt synchronous execution and if the operation exceeds a configured budget (e.g., 50 seconds), switch to background job mode and return a job_id.

#### Pattern

```python
TIMEOUT_SECONDS = 50.0

try:
    # Attempt synchronous completion under time budget
    result = await asyncio.wait_for(do_work(args), timeout=TIMEOUT_SECONDS)
    return result
except asyncio.TimeoutError:
    # Exceeded the budget: launch background job
    job_id = str(uuid.uuid4())
    job = JobMeta(id=job_id, label=f"work:{args['label']}", status="pending")
    STATE.jobs[job_id] = job

    async def _run_job():
        job.status = "running"
        job.started_at = time.time()
        try:
            job.result = await do_work(args)
            job.status = "completed"
        except Exception as e:
            job.error = str(e)
            job.status = "failed"
        finally:
            job.completed_at = time.time()

    job.task = asyncio.create_task(_run_job())
    return {
        "job_id": job_id,
        "status": "pending",
        "message": f"Operation exceeded {int(TIMEOUT_SECONDS)}s; running in background. Use job_status to track progress.",
        "smart_async": True,
    }
```

#### Why Time Thresholds?
- **Reliable**: Uses actual elapsed time rather than guessing based on heuristics
- **Predictable**: Avoids client-side timeouts across environments
- **Flexible**: Timeout can be tuned per tool or via config

#### Best Practice
- Support `async_mode=True` to force background execution immediately
- Combine threshold-based fallback with optional heuristics (LLM chunking, many files) to preemptively go async
- Always return helpful messages and `smart_async: true` when the switch occurs
