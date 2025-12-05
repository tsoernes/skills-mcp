---
title: "MCP Smart Async: Time-Threshold Background Execution (Shielded Task)"
created_at: 2025-12-04T14:45:00Z
kind: note
---
## Overview
Design MCP tools to run synchronously when fast, and automatically switch to background execution if they exceed a configured wall-time (e.g. 50 seconds). Use **asyncio.shield** so the underlying task is **not cancelled** on timeout and is **not restarted**.

## Code
See assets:
- `examples/smart_async_shielded_task.py` (focused shielded pattern)

## Pattern
- Start one task (coro_factory → asyncio.create_task(...))
- Await with `asyncio.wait_for(asyncio.shield(task), timeout)`
- On `asyncio.TimeoutError`, return a `job_id` while the same task continues in background
- Attach a `done_callback` to finalize job metadata
- Provide `job_status` and `list_jobs` tools with human-readable timestamps

## Response Example (Threshold Switch)
```json
{
  "job_id": "uuid",
  "status": "pending",
  "message": "Operation exceeded 50s; running in background.",
  "smart_async": true
}
```

## Best Practices
- Make timeout configurable via env (e.g., SMART_ASYNC_TIMEOUT_SECONDS)
- Include raw and ISO timestamps in job_status
- Provide clear messages and progress info
- Combine threshold-based switch with optional heuristics if needed
