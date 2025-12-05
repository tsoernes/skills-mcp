---
title: "Update: Health Check Example to Use Human-Readable Timestamps"
created_at: 20251204T133334Z
kind: note
---
### Update: Prefer Human-Readable Timestamps in Health Checks

When exposing timestamps in MCP tools (especially health checks), prefer human-readable formats over raw Unix timestamps.

#### Recommended Format
- Use ISO 8601 in UTC, for example: `2025-01-03T13:29:14Z`

#### Revised Example

```python
from dataclasses import dataclass, field
import time
import subprocess

@dataclass
class AppState:
    """Global application state."""
    settings: Settings | None = None
    server_started_at: float = field(default_factory=time.time)
    git_commit: str | None = None

    @property
    def server_started_at_iso(self) -> str:
        """Return server start time as ISO 8601 string."""
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.server_started_at))
        except Exception:
            return ""

STATE = AppState()

@mcp.tool()
async def health() -> dict[str, Any]:
    """Health probe with uptime and version tracking."""
    # Calculate uptime
    uptime_seconds = time.time() - STATE.server_started_at
    uptime_minutes = uptime_seconds / 60
    uptime_hours = uptime_minutes / 60

    # Format uptime
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
        # Human-readable timestamp
        "server_started_at": STATE.server_started_at_iso,
        # Keep uptime_seconds for programmatic use
        "uptime_seconds": round(uptime_seconds, 1),
        "uptime": uptime_str,
        "git_commit": STATE.git_commit,
    }
```

#### Why This Matters
- Easier for humans to read logs and health responses
- Still preserves `uptime_seconds` for machines
- Aligns with best practices for observability APIs

When updating existing code or designing new tools, prefer ISO timestamps and explicit duration fields instead of raw numeric timestamps.
