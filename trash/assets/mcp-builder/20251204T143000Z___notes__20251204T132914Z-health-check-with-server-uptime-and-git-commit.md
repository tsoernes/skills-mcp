---
title: "Health Check with Server Uptime and Git Commit"
created_at: 20251204T132914Z
kind: note
---
## Health Check Tool Pattern with Uptime Tracking

When creating MCP servers, implement a comprehensive health check that includes:

### Essential Information
1. **Server status** - "ok" or "degraded"
2. **Protocol version** - MCP protocol version supported
3. **Server version** - Application version
4. **Environment readiness** - Are required env vars present?

### Enhanced Tracking (Recommended)
5. **Server start time** - When the server started (Unix timestamp)
6. **Uptime** - How long the server has been running (human-readable)
7. **Git commit** - Current git commit hash for version tracking

### Implementation Example

```python
from dataclasses import dataclass, field
import time
import subprocess

@dataclass
class AppState:
    """Global application state."""
    settings: Settings | None = None
    # ... other fields ...
    server_started_at: float = field(default_factory=time.time)
    git_commit: str | None = None

STATE = AppState()

@mcp.tool()
async def health() -> dict[str, Any]:
    """Health probe with uptime and version tracking."""
    # Calculate uptime
    uptime_seconds = time.time() - STATE.server_started_at
    uptime_minutes = uptime_seconds / 60
    uptime_hours = uptime_minutes / 60
    
    # Format uptime human-readable
    if uptime_hours >= 1:
        uptime_str = f"{uptime_hours:.1f}h"
    elif uptime_minutes >= 1:
        uptime_str = f"{uptime_minutes:.1f}m"
    else:
        uptime_str = f"{uptime_seconds:.1f}s"
    
    return {
        "status": "ok",
        "protocol": "2024-11-05",
        "version": "1.0.0",
        "env_ready": True,
        "server_started_at": STATE.server_started_at,
        "uptime_seconds": round(uptime_seconds, 1),
        "uptime": uptime_str,
        "git_commit": STATE.git_commit,
    }

def main():
    """Main entry point."""
    # Initialize
    ensure_settings()
    
    # Try to get git commit at startup
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            STATE.git_commit = result.stdout.strip()
    except Exception:
        STATE.git_commit = None
    
    # Log startup
    logger.info(f"Starting server v{APP_VERSION}")
    logger.info(f"Git commit: {STATE.git_commit or 'unknown'}")
    
    # Run server
    mcp.run()
```

### Benefits
- **Debugging:** Know which version is running
- **Monitoring:** Track server uptime
- **Troubleshooting:** Correlate issues with specific commits
- **Deployment:** Verify correct version deployed

### Example Response
```json
{
  "status": "ok",
  "protocol": "2024-11-05",
  "version": "1.0.0",
  "env_ready": true,
  "server_started_at": 1764854659.5,
  "uptime_seconds": 125.3,
  "uptime": "2.1m",
  "git_commit": "f5b7de0"
}
```
