---
title: "Smart Async with Automatic Incremental Output - Default Streaming Pattern"
created_at: 20251217T125639+0100
kind: note
---
---
title: "Smart Async with Automatic Incremental Output - Default Streaming Pattern"
created_at: 2025-12-17T12:00:00Z
kind: note
status: production-tested
project: python-mcp
related: smart-async-decorator-production-tested-pattern, async-job-progress-tracking
---

## Overview

When implementing `@smart_async` MCP tools, **always stream output for background jobs** and **default to incremental reads** in job status checks. This eliminates the need for explicit streaming parameters and provides better UX.

## Pattern Summary

1. **Remove `enable_streaming` parameter** - streaming is automatic for background jobs
2. **Default `incremental=True`** in job_status tool - users get new output by default
3. **Capture output line-by-line** when job has context (background execution)
4. **Track read offsets** for incremental access

## Key Principle

> **Background jobs stream by default because they're long-running by definition.**  
> **Job status returns incremental output by default because users expect tail-like behavior.**

## JobMeta Structure

Compatible with existing smart_async pattern, extends with output fields:

```python
@dataclass
class JobMeta:
    """Metadata for async background jobs with incremental output."""
    id: str
    label: str
    status: str = "pending"  # pending, running, completed, failed, cancelled
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    result: Any | None = None
    task: asyncio.Task | None = None
    progress: dict[str, Any] | None = None  # From progress tracking pattern
    
    # NEW: Incremental output fields
    partial_stdout: str = ""  # Accumulated stdout for running jobs
    partial_stderr: str = ""  # Accumulated stderr for running jobs
    stdout_offset: int = 0    # Track what client has read
    stderr_offset: int = 0    # Track what client has read
```

## Tool Implementation

### No Streaming Parameter Needed

```python
@smart_async(default_timeout=20.0)
async def execute_script(
    script: str,
    async_mode: bool = False,
    job_label: str | None = None
    # NO enable_streaming parameter!
) -> dict[str, Any]:
    """Execute script with automatic streaming for background jobs."""
    
    # Get output callback (always available)
    output_callback = create_output_callback()
    
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    # Check if running as background job via context
    if current_job_id.get() and output_callback:
        # Stream line by line
        async def read_stdout():
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8")
                output_callback(stdout=text, stderr="")
        
        await asyncio.gather(read_stdout(), proc.wait())
    else:
        # Sync execution: just wait for completion
        stdout, stderr = await proc.communicate()
    
    return {"stdout": stdout, "stderr": stderr, "exit_code": proc.returncode}
```

### Job Status with Incremental Default

```python
@mcp.tool()
def job_status(job_id: str, incremental: bool = True) -> dict[str, Any]:
    """
    Get job status with incremental output by default.
    
    Args:
        job_id: Job identifier
        incremental: If True (default), return only new output since last check.
                     Set False to get full output.
    """
    job = STATE.jobs.get(job_id)
    if not job:
        return {"error": f"Job {job_id} not found"}
    
    response = {"job": _job_public(job)}
    
    if incremental and job.status == "running":
        # Return only new output since last check
        new_stdout = job.partial_stdout[job.stdout_offset:]
        new_stderr = job.partial_stderr[job.stderr_offset:]
        
        # Update offsets for next incremental read
        job.stdout_offset = len(job.partial_stdout)
        job.stderr_offset = len(job.partial_stderr)
        
        response["job"]["new_stdout"] = new_stdout
        response["job"]["new_stderr"] = new_stderr
        response["incremental"] = True
    
    return response
```

## Rationale

### Why Remove enable_streaming Parameter?

❌ **Old way** - User must predict at launch:
```python
result = await tool(..., enable_streaming=True, async_mode=True)
```

✅ **New way** - Streaming is automatic:
```python
result = await tool(..., async_mode=True)
```

**Reasons:**
1. Background jobs are long-running by definition (else they'd complete sync)
2. Users expect to monitor long-running jobs
3. No overhead for sync jobs (they complete before returning)
4. Simpler API - one less parameter to understand

### Why Default incremental=True?

❌ **Old way** - Full output every check:
```python
status = await job_status(job_id)  # Returns all output
status = await job_status(job_id)  # Returns all output again (wasteful)
```

✅ **New way** - Incremental by default:
```python
status = await job_status(job_id)  # Returns all output (first time)
status = await job_status(job_id)  # Returns only new output (efficient)

# Explicit full output when needed:
status = await job_status(job_id, incremental=False)  # All output
```

**Reasons:**
1. More efficient - reduces response size
2. Natural tail-like behavior (`tail -f`, not `cat`)
3. Matches user expectations
4. Explicit opt-out for full output

## User Experience

### Launch Background Job
```python
# Just specify async_mode, streaming is automatic
result = await execute_script(
    script="long_process.sh",
    async_mode=True
)
# Returns: {"job_id": "...", "status": "pending"}
```

### Monitor with Incremental Output
```python
# First check - all output so far
status1 = await job_status(job_id)
print(status1["job"]["new_stdout"])  # "Line 1\nLine 2\nLine 3\n"

# Second check - only new output
status2 = await job_status(job_id)
print(status2["job"]["new_stdout"])  # "Line 4\nLine 5\n"

# Get full output if needed
status3 = await job_status(job_id, incremental=False)
print(status3["job"]["partial_stdout"])  # All lines
```

## Response Examples

### First Status Check (Incremental)
```json
{
  "job": {
    "status": "running",
    "partial_stdout": "Line 1\nLine 2\nLine 3\n",
    "partial_stderr": "",
    "new_stdout": "Line 1\nLine 2\nLine 3\n",
    "new_stderr": ""
  },
  "incremental": true
}
```

### Second Status Check (Incremental)
```json
{
  "job": {
    "status": "running",
    "partial_stdout": "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n",
    "partial_stderr": "",
    "new_stdout": "Line 4\nLine 5\n",
    "new_stderr": ""
  },
  "incremental": true
}
```

### Full Output Request
```json
{
  "job": {
    "status": "running",
    "partial_stdout": "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n",
    "partial_stderr": ""
  }
}
```

## Common Mistakes to Avoid

❌ Don't require streaming flag:
```python
# BAD
async def tool(..., enable_streaming: bool = False):
```

✅ Stream automatically for background jobs:
```python
# GOOD
async def tool(..., async_mode: bool = False):
    # Streaming automatic if async_mode=True or timeout exceeded
```

❌ Don't default to full output:
```python
# BAD
def job_status(job_id: str, incremental: bool = False):
```

✅ Default to incremental:
```python
# GOOD
def job_status(job_id: str, incremental: bool = True):
```

## Benefits

1. **Simpler API** - no streaming parameter needed at launch
2. **Better defaults** - incremental is what users want
3. **No surprises** - background jobs always monitorable
4. **Explicit opt-out** - `incremental=False` when needed
5. **Performance** - only stream when job actually in background

## Production Implementation

**Project:** python-mcp  
**Status:** Production-tested, all tests passing  
**Files:**
- `src/python_mcp_server/smart_async.py` - Job tracking with incremental support
- `src/python_mcp_server/__init__.py` - Tools with automatic streaming
- `docs/INCREMENTAL_OUTPUT.md` - Complete documentation

## See Also

- **smart-async-decorator-production-tested-pattern** - Base pattern
- **async-job-progress-tracking** - Progress tracking pattern
- **health-check** - Server health patterns
