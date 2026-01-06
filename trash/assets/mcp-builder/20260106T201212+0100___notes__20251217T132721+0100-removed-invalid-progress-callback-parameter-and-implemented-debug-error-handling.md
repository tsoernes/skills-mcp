---
title: "Removed invalid progress_callback parameter and implemented debug error handling in repo-summarize-mcp"
created_at: 20251217T132721+0100
kind: note
---
## Issue
The repo-summarize-mcp server was failing with:
```
TypeError: summarize_repository() got an unexpected keyword argument 'progress_callback'
```

This occurred because `_launch_repo_summary_job` was passing `progress_callback=_progress` to `summarize_repository`, but the function signature in `repo_summary_core.core.summarizer` doesn't accept this parameter.

## Root Cause
The codebase had two versions of `summarize_repository`:
1. **Legacy version** (`repo_summary_mcp.py`) - accepts `progress_callback`
2. **New modular version** (`repo_summary_core.core.summarizer`) - does NOT accept `progress_callback`

The tools module was importing from the new modular version but still trying to pass the old parameter.

## Solution

### 1. Removed Invalid Parameter
Removed `progress_callback=_progress` from the call to `summarize_repository` in `_launch_repo_summary_job` (line 203 in `src/repo_summary_server/tools.py`).

### 2. Implemented Debug Mode Error Handling
Following the mcp-builder skill note on debug-mode-error-handling-with-stack-traces, added:

```python
# Debug mode control
DEBUG_MODE = os.getenv("MCP_DEBUG", "false").lower() in ("true", "1", "yes")

def _format_error(e: Exception) -> dict[str, Any]:
    """
    Format exception based on debug mode setting.
    
    In debug mode (MCP_DEBUG=true):
        Returns full stack trace with line numbers
    In production mode:
        Returns compact error message only
    """
    error_dict = {
        "error": str(e),
        "error_type": type(e).__name__,
        "debug_mode": DEBUG_MODE,
    }
    
    if DEBUG_MODE:
        error_dict["traceback"] = traceback.format_exc()
        get_logger(__name__).error(f"Tool error with traceback:\n{traceback.format_exc()}")
    else:
        get_logger(__name__).error(f"Tool error: {type(e).__name__}: {e}")
    
    return error_dict
```

### 3. Updated All Job Error Handlers
Updated all three background job functions to use the new error formatter:
- `_launch_repo_summary_job`
- `_launch_repo_changes_job`
- `_launch_incremental_job`

Changed from:
```python
except Exception as e:
    await mark_error(job_id, e)
```

To:
```python
except Exception as e:
    error_info = _format_error(e)
    await mark_error(job_id, error_info)
```

## Benefits

1. **Fixed TypeError** - Removed the invalid parameter causing immediate failures
2. **Better Debugging** - When `MCP_DEBUG=true`, full stack traces are returned in error responses
3. **Production-Safe** - Compact error messages in production mode
4. **Consistent** - All job error handlers use the same pattern
5. **Logged** - Errors are properly logged with context

## Usage

Enable debug mode:
```bash
export MCP_DEBUG=true
```

Error response in debug mode:
```json
{
  "error": "Division by zero",
  "error_type": "ZeroDivisionError",
  "traceback": "Traceback (most recent call last):\n  File ...",
  "debug_mode": true
}
```

Error response in production:
```json
{
  "error": "Division by zero",
  "error_type": "ZeroDivisionError",
  "debug_mode": false
}
```

## Files Changed
- `src/repo_summary_server/tools.py`
  - Added `DEBUG_MODE` constant
  - Added `_format_error()` helper function
  - Removed `progress_callback` parameter from `summarize_repository` call
  - Updated error handling in all three `_launch_*_job` functions
