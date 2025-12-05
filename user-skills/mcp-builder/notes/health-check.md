---
title: "MCP Health Check: Human-Readable Time + Env Details"
created_at: 2025-12-04T14:30:00Z
kind: note
---
## Overview
Guidance for building an MCP health tool that reports:
- Human-readable start time (ISO 8601, UTC)
- Uptime (seconds + friendly duration)
- Explicit env presence (present vs missing)
- Git commit identifier

## Code
See asset: `examples/health_check_human_readable.py`

## Recommended Response Shape
```json
{
  "status": "ok",
  "protocol": "2024-11-05",
  "version": "1.0.0",
  "env_ready": true,
  "env_error": "",
  "env_present": ["AZURE_OPENAI_API_KEY", "AZURE_ENDPOINT"],
  "env_missing": [],
  "server_started_at": "2025-12-04T14:07:25Z",
  "uptime_seconds": 37.0,
  "uptime": "37.0s",
  "git_commit": "f5b7de0"
}
```

## Best Practices
- Prefer ISO 8601 UTC for timestamps
- Include both human-readable and machine-readable durations
- Explicitly list which env vars are present/missing
- Verify values on server startup (and log them)
