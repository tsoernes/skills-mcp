#!/usr/bin/env python3
"""
Complete example of async job progress tracking for MCP servers.

This example demonstrates:
1. Context-based job tracking with contextvars
2. Progress callback pattern
3. Concurrent task execution with progress
4. Integration with rate limiting
5. Persistence and querying

Based on production implementation from rag-mcp project.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------------------
# Context Variables
# --------------------------------------------------------------------------------------

# Context variable to track current job_id in async tasks
current_job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_job_id", default=None
)

# --------------------------------------------------------------------------------------
# Data Models
# --------------------------------------------------------------------------------------


class JobStatus(StrEnum):
    """Job status enum."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobMeta:
    """Metadata for async background jobs with progress tracking."""

    id: str
    label: str
    status: JobStatus = JobStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    result: Any | None = None
    task: asyncio.Task[Any] | None = None
    progress: dict[str, Any] | None = (
        None  # {"current": 5, "total": 100, "message": "..."}
    )


@dataclass
class AppState:
    """Global application state."""

    jobs: dict[str, JobMeta] = field(default_factory=dict)
    persistence_dir: Path = Path.home() / ".mcp_example"


STATE = AppState()

# --------------------------------------------------------------------------------------
# Job Management
# --------------------------------------------------------------------------------------


def _save_jobs() -> None:
    """Persist jobs to disk."""
    jobs_path = STATE.persistence_dir / "meta" / "jobs.json"
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
            "progress": j.progress,
        }
        for j in STATE.jobs.values()
    ]

    _ = jobs_path.write_text(json.dumps(serializable, indent=2))
    print(f"💾 Saved {len(serializable)} jobs to {jobs_path}")


def _update_job_progress(
    job_id: str, current: int, total: int, message: str | None = None
) -> None:
    """Update progress for a running job."""
    job = STATE.jobs.get(job_id)
    if not job:
        return

    job.progress = {"current": current, "total": total}
    if message:
        job.progress["message"] = message

    _save_jobs()


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
        "progress": job.progress,
    }


def _launch_background_job(
    label: str, coro_factory: Callable[[], Awaitable[Any]]
) -> dict[str, Any]:
    """Launch a background job and return job_id immediately."""
    job_id = str(uuid.uuid4())
    job = JobMeta(
        id=job_id,
        label=label,
        status=JobStatus.PENDING,
        created_at=datetime.now().isoformat(),
    )
    STATE.jobs[job_id] = job

    async def _run_job():
        # KEY: Set the job_id in context for progress tracking
        _ = current_job_id.set(job_id)

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now().isoformat()
        _save_jobs()

        try:
            result = await coro_factory()
            job.status = JobStatus.COMPLETED
            job.result = result
            job.completed_at = datetime.now().isoformat()
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now().isoformat()
            print(f"❌ Job {job_id} ({label}) failed: {e}")
        finally:
            _save_jobs()

    job.task = asyncio.create_task(_run_job())
    _save_jobs()
    return {"job_id": job_id, "status": "pending"}


def job_status(job_id: str) -> dict[str, Any]:
    """Get status for a specific async job."""
    job = STATE.jobs.get(job_id)
    if not job:
        return {"error": f"Job {job_id} not found"}
    return {"job": _job_public(job)}


# --------------------------------------------------------------------------------------
# Worker Functions with Progress
# --------------------------------------------------------------------------------------


async def generate_questions(
    num_questions: int,
    progress_callback: Callable[[int, int, str | None], None] | None = None,
) -> list[dict[str, Any]]:
    """
    Simulate generating questions with progress reporting.

    Args:
        num_questions: Number of questions to generate
        progress_callback: Optional callback(current, total, message) for progress updates
    """
    questions = []
    total = num_questions

    # Initial progress
    if progress_callback:
        progress_callback(0, total, "Starting question generation...")

    # Simulate processing
    for i in range(num_questions):
        # Simulate work (e.g., API call)
        await asyncio.sleep(0.1)

        question = {
            "id": i + 1,
            "question": f"What is the meaning of question {i + 1}?",
            "answer": f"Answer to question {i + 1}",
        }
        questions.append(question)

        # Update progress after each item
        if progress_callback:
            progress_callback(i + 1, total, f"Generated {i + 1}/{total} questions")

    return questions


async def process_items_concurrent(
    items: list[str],
    progress_callback: Callable[[int, int, str | None], None] | None = None,
) -> list[dict[str, Any]]:
    """
    Process items concurrently with progress tracking.

    Uses atomic counter and lock for thread-safe progress updates.
    """
    completed = 0
    total = len(items)
    lock = asyncio.Lock()
    results = []

    async def process_with_progress(item: str, index: int):
        nonlocal completed

        # Simulate processing
        await asyncio.sleep(0.2)
        result = {"index": index, "item": item, "processed": True}

        # Atomic progress update
        async with lock:
            completed += 1
            if progress_callback:
                progress_callback(
                    completed, total, f"Processed {completed}/{total} items"
                )

        return result

    if progress_callback:
        progress_callback(0, total, "Starting concurrent processing...")

    results = await asyncio.gather(
        *[process_with_progress(item, i) for i, item in enumerate(items)]
    )

    return results


# --------------------------------------------------------------------------------------
# MCP-Style Tools
# --------------------------------------------------------------------------------------


async def generate_benchmark_dataset(
    num_questions: int = 10, async_mode: bool = False, job_label: str | None = None
) -> dict[str, Any]:
    """
    Generate benchmark questions with progress tracking.

    Args:
        num_questions: Number of questions to generate
        async_mode: If True, run in background and return job_id
        job_label: Optional label for the job
    """

    async def _do_work():
        # Create progress callback that uses context
        def progress_callback(current: int, total: int, message: str | None = None):
            job_id = current_job_id.get()
            if job_id:
                _update_job_progress(job_id, current, total, message)

        # Generate questions with progress
        questions = await generate_questions(
            num_questions=num_questions, progress_callback=progress_callback
        )

        return {"questions": questions, "total": len(questions)}

    if async_mode:
        return _launch_background_job(
            label=job_label or "generate_benchmark_dataset", coro_factory=_do_work
        )
    else:
        return await _do_work()


async def process_batch(
    items: list[str], async_mode: bool = False, job_label: str | None = None
) -> dict[str, Any]:
    """
    Process items in batch with concurrent execution and progress tracking.

    Args:
        items: List of items to process
        async_mode: If True, run in background and return job_id
        job_label: Optional label for the job
    """

    async def _do_work():
        # Create progress callback
        def progress_callback(current: int, total: int, message: str | None = None):
            job_id = current_job_id.get()
            if job_id:
                _update_job_progress(job_id, current, total, message)

        # Process items concurrently with progress
        results = await process_items_concurrent(
            items=items, progress_callback=progress_callback
        )

        return {"results": results, "total": len(results)}

    if async_mode:
        return _launch_background_job(
            label=job_label or "process_batch", coro_factory=_do_work
        )
    else:
        return await _do_work()


# --------------------------------------------------------------------------------------
# Demo & Testing
# --------------------------------------------------------------------------------------


async def demo_progress_tracking():
    """Demonstrate progress tracking with various scenarios."""
    print("\n" + "=" * 70)
    print("MCP Async Job Progress Tracking Demo")
    print("=" * 70)

    # Test 1: Synchronous execution (no job tracking)
    print("\n📝 Test 1: Synchronous Execution")
    result = await generate_benchmark_dataset(num_questions=5, async_mode=False)
    print(f"✅ Completed: {result['total']} questions generated")

    # Test 2: Async execution with progress tracking
    print("\n📝 Test 2: Async Execution with Progress Tracking")
    result = await generate_benchmark_dataset(
        num_questions=10, async_mode=True, job_label="Generate 10 questions"
    )
    job_id = result["job_id"]
    print(f"🚀 Job launched: {job_id}")

    # Poll for progress
    for _ in range(15):
        await asyncio.sleep(0.5)
        status = job_status(job_id)

        if status["job"]["progress"]:
            progress = status["job"]["progress"]
            current = progress["current"]
            total = progress["total"]
            message = progress.get("message", "")
            pct = (current / total * 100) if total > 0 else 0
            print(f"  📊 Progress: {current}/{total} ({pct:.0f}%) - {message}")

        if status["job"]["status"] in (JobStatus.COMPLETED, JobStatus.FAILED):
            print(f"  ✅ Job {status['job']['status']}")
            break

    # Test 3: Concurrent processing with progress
    print("\n📝 Test 3: Concurrent Processing with Progress")
    items = [f"item_{i}" for i in range(8)]
    result = await process_batch(
        items=items, async_mode=True, job_label="Process 8 items concurrently"
    )
    job_id = result["job_id"]
    print(f"🚀 Job launched: {job_id}")

    # Poll for progress
    for _ in range(15):
        await asyncio.sleep(0.3)
        status = job_status(job_id)

        if status["job"]["progress"]:
            progress = status["job"]["progress"]
            current = progress["current"]
            total = progress["total"]
            message = progress.get("message", "")
            pct = (current / total * 100) if total > 0 else 0
            print(f"  📊 Progress: {current}/{total} ({pct:.0f}%) - {message}")

        if status["job"]["status"] in (JobStatus.COMPLETED, JobStatus.FAILED):
            print(f"  ✅ Job {status['job']['status']}")
            break

    # Show final status
    print("\n📋 Final Job Status:")
    for job_id, job in STATE.jobs.items():
        status_emoji = {
            JobStatus.COMPLETED: "✅",
            JobStatus.FAILED: "❌",
            JobStatus.RUNNING: "⏳",
            JobStatus.PENDING: "🔵",
            JobStatus.CANCELLED: "🚫",
        }[job.status]
        print(f"  {status_emoji} {job.label}: {job.status}")
        if job.progress:
            print(f"     Progress: {job.progress['current']}/{job.progress['total']}")

    print("\n" + "=" * 70)
    print("Demo Complete!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(demo_progress_tracking())
