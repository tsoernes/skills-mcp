---
title: "Async Job Progress Tracking for Long-Running MCP Operations"
created_at: 20251208T123724+0100
kind: note
---
---
title: "Async Job Progress Tracking for Long-Running MCP Operations"
created_at: 2025-12-08T12:36:00Z
kind: note
status: production-tested
related: smart-async-decorator-production-tested-pattern
---

## Overview

Enable real-time progress tracking for long-running async MCP operations like batch processing, dataset generation, and bulk indexing. This pattern extends the `@smart_async` decorator pattern with progress updates that are persisted and queryable.

## Key Features

1. **Real-Time Progress** - Track current/total items and custom messages
2. **Persistent Progress** - Saved to disk with job metadata
3. **Context-Based Tracking** - Uses `contextvars` to track job_id in async tasks
4. **Zero Boilerplate** - Progress callback automatically wired via context
5. **Queryable Status** - Progress included in job status responses

## Implementation Pattern

### Step 1: Add Progress Field to JobMeta

```python
from dataclasses import dataclass, field

@dataclass
class JobMeta:
    """Metadata for async background jobs."""
    id: str
    label: str
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    result: Any | None = None
    task: asyncio.Task | None = None
    progress: dict[str, Any] | None = None  # NEW: Progress tracking
```

### Step 2: Create Context Variable for Job Tracking

```python
import contextvars

# Context variable to track current job_id in async tasks
current_job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_job_id", 
    default=None
)
```

### Step 3: Set Job ID in Background Job Runner

```python
def _launch_background_job(label: str, coro_factory: Callable) -> dict[str, Any]:
    """Launch a background job and return job_id immediately."""
    job_id = str(uuid.uuid4())
    job = JobMeta(id=job_id, label=label, status="pending")
    STATE.jobs[job_id] = job

    async def _run_job():
        # Set the job_id in context for progress tracking
        current_job_id.set(job_id)  # KEY: Set context before running
        
        job.status = "running"
        job.started_at = datetime.now().isoformat()
        _save_jobs()
        
        try:
            result = await coro_factory()
            job.status = "completed"
            job.result = result
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
        finally:
            job.completed_at = datetime.now().isoformat()
            _save_jobs()

    job.task = asyncio.create_task(_run_job())
    _save_jobs()
    return {"job_id": job_id, "status": "pending"}
```

### Step 4: Create Progress Update Helper

```python
def _update_job_progress(
    job_id: str, 
    current: int, 
    total: int, 
    message: str | None = None
) -> None:
    """Update progress for a running job."""
    job = STATE.jobs.get(job_id)
    if not job:
        return
    
    job.progress = {"current": current, "total": total}
    if message:
        job.progress["message"] = message
    
    _save_jobs()  # Persist immediately
```

### Step 5: Wire Progress Callback in MCP Tool

```python
@mcp.tool()
@smart_async(timeout_env="SMART_ASYNC_TIMEOUT_SECONDS", default_timeout=50.0)
async def generate_benchmark_dataset(
    index_name: str,
    num_questions: int = 100,
    async_mode: bool = False,
    job_label: str | None = None,
) -> dict[str, Any]:
    """Generate benchmark questions with progress tracking."""
    
    # Create progress callback that uses context
    def progress_callback(current: int, total: int, message: str | None = None):
        job_id = current_job_id.get()  # Get job_id from context
        if job_id:
            _update_job_progress(job_id, current, total, message)
    
    # Pass callback to worker function
    questions = await generate_questions(
        num_questions=num_questions,
        progress_callback=progress_callback  # Pass callback
    )
    
    return {"questions": questions, "total": len(questions)}
```

### Step 6: Implement Progress in Worker Function

```python
async def generate_questions(
    num_questions: int,
    progress_callback: Callable[[int, int, str | None], None] | None = None
) -> list[dict]:
    """Generate questions with progress reporting."""
    
    questions = []
    total = num_questions
    
    # Initial progress
    if progress_callback:
        progress_callback(0, total, "Starting question generation...")
    
    # Process items
    for i in range(num_questions):
        question = await generate_single_question()
        questions.append(question)
        
        # Update progress after each item
        if progress_callback:
            progress_callback(
                i + 1, 
                total, 
                f"Generated {i + 1}/{total} questions"
            )
    
    return questions
```

### Step 7: Include Progress in Job Status

```python
def _job_public(job: JobMeta) -> dict[str, Any]:
    """Convert JobMeta to public dict."""
    return {
        "id": job.id,
        "label": job.label,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "error": job.error,
        "result": job.result,
        "progress": job.progress,  # NEW: Include progress
    }

@mcp.tool()
def job_status(job_id: str) -> dict[str, Any]:
    """Get status for a specific async job."""
    job = STATE.jobs.get(job_id)
    if not job:
        return {"error": f"Job {job_id} not found"}
    return {"job": _job_public(job)}
```

### Step 8: Persist Progress in Job Storage

```python
def _save_jobs() -> None:
    """Persist jobs to disk."""
    jobs_path = STATE.settings.persistence_dir / "meta" / "jobs.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    
    serializable = [
        {
            "id": j.id,
            "label": j.label,
            "status": j.status,
            "created_at": j.created_at,
            "started_at": j.started_at,
            "completed_at": j.completed_at,
            "error": j.error,
            "result": j.result,
            "progress": j.progress,  # NEW: Persist progress
        }
        for j in STATE.jobs.values()
    ]
    
    jobs_path.write_text(json.dumps(serializable, indent=2))
```

## Usage Example

```python
# Start async job with progress tracking
result = await generate_benchmark_dataset(
    index_name="my_index",
    num_questions=100,
    async_mode=True,
    job_label="Generate 100 benchmark questions"
)

# Returns immediately:
# {"job_id": "abc-123", "status": "pending"}

# Check progress
status1 = job_status(result["job_id"])
# {
#   "job": {
#     "id": "abc-123",
#     "status": "running",
#     "progress": {
#       "current": 25,
#       "total": 100,
#       "message": "Generated 25/100 questions"
#     }
#   }
# }

# Check again later
status2 = job_status(result["job_id"])
# {
#   "job": {
#     "id": "abc-123",
#     "status": "running",
#     "progress": {
#       "current": 75,
#       "total": 100,
#       "message": "Generated 75/100 questions"
#     }
#   }
# }

# Final check
status3 = job_status(result["job_id"])
# {
#   "job": {
#     "id": "abc-123",
#     "status": "completed",
#     "result": {...},
#     "progress": {
#       "current": 100,
#       "total": 100,
#       "message": "Generated 100/100 questions"
#     }
#   }
# }
```

## Response Examples

### Job Running with Progress
```json
{
  "job": {
    "id": "9c0af4c2-2a74-430e-bc1d-0f419b6bd503",
    "label": "Generate 100 questions with GPT-5",
    "status": "running",
    "created_at": "2025-12-08T12:34:29.821388",
    "started_at": "2025-12-08T12:34:29.825988",
    "completed_at": null,
    "error": null,
    "result": null,
    "progress": {
      "current": 52,
      "total": 92,
      "message": "Generated 52/92 questions"
    }
  }
}
```

### Job Completed
```json
{
  "job": {
    "id": "9c0af4c2-2a74-430e-bc1d-0f419b6bd503",
    "status": "completed",
    "result": {
      "total_questions": 92,
      "dataset_id": 5
    },
    "progress": {
      "current": 92,
      "total": 92,
      "message": "Completed"
    }
  }
}
```

## Best Practices

### Progress Update Frequency
- **High-frequency operations (< 1s per item)**: Update every 10-50 items
- **Medium-frequency operations (1-10s per item)**: Update every item
- **Low-frequency operations (> 10s per item)**: Update every item + substeps

### Progress Messages
- Be specific: "Generated 52/92 questions" not "Processing..."
- Include context: "Embedding chunk 1000/5000 (batch 20/100)"
- Add time estimates if known: "~2 minutes remaining"

### Error Handling
- Progress callback should never throw exceptions
- Wrap callback calls in try/except if unsure
- Continue operation even if progress update fails

### Performance Considerations
- `_save_jobs()` writes to disk (~10-50ms)
- For very high-frequency updates, consider batching (e.g., update every 10 items)
- Progress updates are I/O bound, not CPU bound

## Advanced: Progress with Concurrent Tasks

For concurrent task execution (e.g., asyncio.gather), use atomic counter:

```python
async def process_items_concurrent(
    items: list,
    progress_callback: Callable | None = None
) -> list:
    """Process items concurrently with progress tracking."""
    
    completed = 0
    total = len(items)
    lock = asyncio.Lock()
    
    async def process_with_progress(item):
        nonlocal completed
        result = await process_item(item)
        
        # Atomic progress update
        async with lock:
            completed += 1
            if progress_callback:
                progress_callback(completed, total, f"Processed {completed}/{total}")
        
        return result
    
    if progress_callback:
        progress_callback(0, total, "Starting concurrent processing...")
    
    results = await asyncio.gather(*[
        process_with_progress(item) for item in items
    ])
    
    return results
```

## Integration with Rate Limiting

Combine with rate limiter for realistic progress:

```python
async def generate_with_rate_limit_and_progress(
    items: list,
    rate_limiter,
    progress_callback: Callable | None = None
):
    """Generate with rate limiting + progress tracking."""
    
    total = len(items)
    completed = 0
    
    for i, item in enumerate(items):
        # Acquire rate limit
        await rate_limiter.acquire(tokens=estimated_tokens)
        
        # Process
        result = await process_item(item)
        
        # Update progress
        completed += 1
        if progress_callback:
            stats = rate_limiter.get_stats()
            message = (
                f"Processed {completed}/{total} "
                f"(TPM: {stats['tpm_utilization_pct']:.0f}%)"
            )
            progress_callback(completed, total, message)
```

## Testing Progress Tracking

```python
async def test_progress_tracking():
    """Test progress updates during job execution."""
    
    # Launch async job
    result = await my_tool(async_mode=True)
    job_id = result["job_id"]
    
    # Poll for progress
    for _ in range(10):
        await asyncio.sleep(2)
        status = job_status(job_id)
        
        if status["job"]["progress"]:
            progress = status["job"]["progress"]
            print(f"Progress: {progress['current']}/{progress['total']} - {progress.get('message', '')}")
        
        if status["job"]["status"] in ("completed", "failed"):
            break
    
    # Verify completion
    final_status = job_status(job_id)
    assert final_status["job"]["status"] == "completed"
    assert final_status["job"]["progress"]["current"] == final_status["job"]["progress"]["total"]
```

## Common Use Cases

1. **Batch Processing**: Track items processed out of total
2. **Dataset Generation**: Track questions generated
3. **Index Building**: Track files/chunks processed
4. **Benchmark Runs**: Track test cases executed
5. **Data Migration**: Track records migrated
6. **File Uploads**: Track bytes uploaded / total bytes

## Related Patterns

- **Smart Async Decorator**: Base async job infrastructure
- **Rate Limiting**: Combine with progress for realistic ETAs
- **Job Pruning**: Clean up old progress data
- **Streaming Progress**: Real-time updates via SSE (Server-Sent Events)

## Production Example

Real-world implementation from rag-mcp project:
- File: `src/rag_mcp/server.py`
- Tool: `rag_create_benchmark_dataset`
- Worker: `benchmark.generate_questions_from_chunks`
- Tests: 6/6 passing with progress tracking

## See Also

- Note: `smart-async-decorator-production-tested-pattern.md`
- Asset: `examples/job_progress_tracking.py` (this note's example code)
