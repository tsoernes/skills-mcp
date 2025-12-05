---
title: "Smart Async Pattern for Long-Running MCP Tools"
created_at: 20251204T132955Z
kind: note
---
## Smart Async Pattern for MCP Tools

Prevent MCP client timeouts by automatically detecting when operations will take >50 seconds and running them in background with job tracking.

### The Problem
- MCP clients typically timeout after 60 seconds
- Long-running operations (LLM processing, large datasets) exceed this limit
- Users get timeout errors and don't know how to fix it

### The Solution: Smart Async
Automatically detect slow operations and switch to background execution without user intervention.

### Implementation Pattern

```python
import asyncio
import time
import uuid
from dataclasses import dataclass, field

@dataclass
class JobMeta:
    """Background job metadata."""
    id: str
    label: str
    status: str = "pending"  # pending, running, completed, failed
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    result: Any | None = None
    task: asyncio.Task | None = None

@mcp.tool()
async def my_slow_operation(
    data: str,
    async_mode: bool = False,  # Allow explicit async override
) -> dict[str, Any]:
    """
    Process data - automatically uses background mode for large inputs.
    
    Smart async: Auto-detects slow operations and runs in background
    to prevent client timeouts.
    """
    
    # Smart async decision heuristics
    smart_async_needed = False
    reason = ""
    
    if not async_mode:
        # Heuristic 1: Large input size
        if len(data) > 10000:
            smart_async_needed = True
            reason = f"Large input ({len(data)} chars, estimated >60s)"
        
        # Heuristic 2: Expensive operation enabled
        elif requires_llm_processing:
            smart_async_needed = True
            reason = "LLM processing enabled (typically >50s)"
        
        # Heuristic 3: Many items to process
        elif item_count > 20:
            smart_async_needed = True
            reason = f"Many items ({item_count}, estimated >{item_count * 3}s)"
    
    use_async = async_mode or smart_async_needed
    
    if use_async:
        # Create background job
        job_id = str(uuid.uuid4())
        job = JobMeta(
            id=job_id,
            label=f"operation:{data[:20]}",
            status="pending",
        )
        STATE.jobs[job_id] = job
        
        async def _run_job():
            job.status = "running"
            job.started_at = time.time()
            try:
                result = await _do_actual_work(data)
                job.status = "completed"
                job.result = result
            except Exception as e:
                job.status = "failed"
                job.error = str(e)
            finally:
                job.completed_at = time.time()
        
        job.task = asyncio.create_task(_run_job())
        
        response = {"job_id": job_id, "status": "pending"}
        if smart_async_needed:
            response["message"] = f"Running in background (smart async: {reason}). Use job_status to track."
            response["smart_async"] = True
        
        return response
    else:
        # Run synchronously - should complete in <50s
        return await _do_actual_work(data)

# Companion tools
@mcp.tool()
def job_status(job_id: str) -> dict[str, Any]:
    """Get status of background job."""
    job = STATE.jobs.get(job_id)
    if not job:
        return {"error": f"Job {job_id} not found"}
    
    return {
        "job_id": job.id,
        "label": job.label,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "error": job.error,
        "result": job.result,
    }

@mcp.tool()
def list_jobs() -> dict[str, Any]:
    """List all background jobs."""
    return {
        "jobs": [
            {
                "id": j.id,
                "label": j.label,
                "status": j.status,
                "created_at": j.created_at,
            }
            for j in STATE.jobs.values()
        ]
    }
```

### Detection Heuristics

Choose heuristics based on your operation:

| Operation Type | Heuristic | Threshold |
|----------------|-----------|-----------|
| LLM Processing | Always async | N/A |
| Large Files | File size | >10MB |
| Many Files | File count | >20 files |
| Batch Operations | Batch size | >50 items |
| Database Queries | Row count estimate | >10,000 rows |
| API Calls | Call count | >100 calls |

### Benefits

1. **No User Configuration:** Works automatically
2. **Prevents Timeouts:** Never exceeds client limits
3. **Progress Tracking:** Users can monitor long jobs
4. **Fallback Safety:** Short operations still synchronous
5. **Explicit Override:** Users can force async_mode=True

### Best Practices

✅ **Do:**
- Estimate operation time based on input characteristics
- Provide helpful messages explaining why background mode was used
- Mark smart async responses with a flag for client awareness
- Support explicit async_mode for user override
- Implement job_status and list_jobs tools

❌ **Don't:**
- Use fixed timeouts (client-side timeouts vary)
- Make everything async (overhead for quick operations)
- Hide background execution from users
- Forget to handle job cleanup
- Skip error handling in background jobs

### Real-World Example (RAG Index Creation)

```python
@mcp.tool()
async def rag_upsert_index(
    index_name: str,
    source_dir: str,
    llm_chunking: bool = True,
    async_mode: bool = False,
) -> dict[str, Any]:
    """Create/update index with smart async."""
    
    smart_async_needed = False
    reason = ""
    
    if not async_mode:
        # LLM chunking is always slow
        if llm_chunking:
            smart_async_needed = True
            reason = "LLM chunking enabled (typically >50s for multiple files)"
        else:
            # Count files in directory
            file_count = count_files_in_dir(source_dir)
            if file_count > 20:
                smart_async_needed = True
                reason = f"Many files ({file_count}, estimated >{file_count * 2}s)"
    
    if async_mode or smart_async_needed:
        return create_background_job(...)
    else:
        return await process_synchronously(...)
```

### Testing Smart Async

```python
# Test 1: Small operation (synchronous)
result = await my_tool(data="small")
assert "job_id" not in result  # Completed immediately

# Test 2: Large operation (background)
result = await my_tool(data="x" * 100000)
assert "job_id" in result
assert result["smart_async"] == True

# Test 3: Explicit async
result = await my_tool(data="small", async_mode=True)
assert "job_id" in result
assert result.get("smart_async") != True  # User-requested, not auto
```

This pattern ensures MCP tools are robust, user-friendly, and never timeout unexpectedly!
