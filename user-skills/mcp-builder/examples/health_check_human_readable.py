from dataclasses import dataclass, field
import os
import time

@dataclass
class AppState:
    server_started_at: float = field(default_factory=time.time)
    git_commit: str | None = None

    @property
    def server_started_at_iso(self) -> str:
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.server_started_at))
        except Exception:
            return ""

async def health() -> dict:
    uptime_seconds = time.time() - STATE.server_started_at
    uptime_minutes = uptime_seconds / 60
    uptime_hours = uptime_minutes / 60
    if uptime_hours >= 1:
        uptime_str = f"{uptime_hours:.1f}h"
    elif uptime_minutes >= 1:
        uptime_str = f"{uptime_minutes:.1f}m"
    else:
        uptime_str = f"{uptime_seconds:.1f}s"
    return {
        "server_started_at": STATE.server_started_at_iso,
        "uptime_seconds": round(uptime_seconds, 1),
        "uptime": uptime_str,
        "env_present": ["AZURE_OPENAI_API_KEY", "AZURE_ENDPOINT"],
        "env_missing": [],
        "git_commit": STATE.git_commit,
    }

STATE = AppState()
