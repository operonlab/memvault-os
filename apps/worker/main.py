"""memvault-os background worker entrypoint.

Runs cron-style jobs and keeps memvault's reactive event pipes wired:

    cron:
      04:00  dream_consolidation     (full Dream Loop, dry_run=False)
      05:00  interest_snapshot       (daily InterestSnapshot per space)
      */30m  sleeptime_reflection    (low-activity heuristic — health-check + hot blocks)

    reactive:
      MEMORY_STORED        → KG write pipe                    (memvault.events)
      capture.promoted     → ConditionalOp → KG write pipe    (memvault.events)
      intelligence.digest  → DigestToBlock → MemoryBlock      (memvault.events)
      QUERY_COMPLETED      → Slow Thinker prefetch pipeline   (memvault.events)
      capture.entry.created→ counter → maybe_trigger_sleeptime(memvault.events)

Slow Thinker is purely event-driven — no cron needed; it activates whenever
QUERY_COMPLETED fires. Importing `src.memvault.events` registers all five pipes.

Process command: ``python -m apps.worker.main``
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("memvault.worker")

DEFAULT_SPACE_ID = "default"
JOB_FAILED_EVENT = "memvault.worker.job_failed"


# ---------------------------------------------------------------------------
# Cron primitives — self-rolled so we don't depend on apscheduler
# ---------------------------------------------------------------------------


@dataclass
class CronJob:
    """A scheduled job. Either daily-at-HH-MM or every-N-minutes."""

    name: str
    func: Callable[[], Awaitable[None]]
    daily_at: tuple[int, int] | None = None  # (hour, minute) UTC
    every_minutes: int | None = None

    def next_fire(self, now: datetime) -> datetime:
        if self.daily_at is not None:
            h, m = self.daily_at
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target
        if self.every_minutes is not None:
            return now + timedelta(minutes=self.every_minutes)
        raise ValueError(f"CronJob {self.name} has no schedule")


async def _run_job(job: CronJob) -> None:
    """Run a single job with structured logging + best-effort failure event."""
    started = datetime.now(UTC)
    logger.info("job.start name=%s", job.name)
    try:
        await job.func()
        elapsed = (datetime.now(UTC) - started).total_seconds()
        logger.info("job.done name=%s elapsed_s=%.2f", job.name, elapsed)
    except Exception as exc:  # noqa: BLE001 — worker must not crash
        elapsed = (datetime.now(UTC) - started).total_seconds()
        logger.exception("job.failed name=%s elapsed_s=%.2f", job.name, elapsed)
        await _publish_failure(job.name, exc)


async def _publish_failure(job_name: str, exc: BaseException) -> None:
    """Best-effort publish of job failure event. Never raises."""
    try:
        from src.events_stub import Event, event_bus

        await event_bus.publish(
            Event(
                type=JOB_FAILED_EVENT,
                payload={
                    "job": job_name,
                    "error": repr(exc),
                    "at": datetime.now(UTC).isoformat(),
                },
            )
        )
    except Exception:
        logger.debug("publish_failure suppressed for job=%s", job_name, exc_info=True)


async def _scheduler_loop(jobs: list[CronJob], stop: asyncio.Event) -> None:
    """Single-threaded scheduler: at each tick, fire any jobs whose next_fire ≤ now."""
    now = datetime.now(UTC)
    next_fires: dict[str, datetime] = {j.name: j.next_fire(now) for j in jobs}
    for name, fire in next_fires.items():
        logger.info("job.scheduled name=%s next_fire=%s", name, fire.isoformat())

    while not stop.is_set():
        now = datetime.now(UTC)
        due = [j for j in jobs if next_fires[j.name] <= now]
        for job in due:
            asyncio.create_task(_run_job(job))
            next_fires[job.name] = job.next_fire(now)
            logger.info(
                "job.next name=%s next_fire=%s", job.name, next_fires[job.name].isoformat()
            )

        # Sleep until earliest next_fire (capped at 30s for stop responsiveness)
        soonest = min(next_fires.values())
        sleep_for = max(1.0, min(30.0, (soonest - datetime.now(UTC)).total_seconds()))
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            continue


# ---------------------------------------------------------------------------
# Job implementations — heavy imports inside the function body so module load
# stays cheap and stub gaps surface as job-level errors, not boot crashes.
# ---------------------------------------------------------------------------


async def _with_session(coro_factory):
    """Open a fresh AsyncSession from the shared factory and run coro_factory(db)."""
    from src.shared.database import async_session_factory

    async with async_session_factory() as db:  # type: ignore[misc]
        return await coro_factory(db)


async def job_dream_consolidation() -> None:
    """Full Dream Loop pass for the default space (every 04:00 UTC)."""
    from src.memvault.dream import run_dream

    async def _go(db):
        return await run_dream(db, space_id=DEFAULT_SPACE_ID, dry_run=False, force=False)

    report = await _with_session(_go)
    logger.info(
        "dream.report skipped=%s errors=%d", getattr(report, "skipped", False),
        len(getattr(report, "errors", []) or []),
    )


async def job_interest_snapshot() -> None:
    """Generate today's InterestSnapshot for the default space (every 05:00 UTC)."""
    from src.memvault.interest_profile import InterestProfileService

    service = InterestProfileService()

    async def _go(db):
        return await service.generate_daily_snapshot(db, space_id=DEFAULT_SPACE_ID)

    snapshot = await _with_session(_go)
    logger.info("interest_snapshot.done has_data=%s", bool(snapshot))


async def job_sleeptime_reflection() -> None:
    """Heuristic 30-min reflection pass for the default space.

    The reactive path (capture.entry.created → counter → ensure_future) is the
    primary trigger; this cron is a low-activity safety net so a quiet day
    still produces hot-block updates.
    """
    from src.memvault.sleeptime import _run_sleeptime

    result = await _run_sleeptime(DEFAULT_SPACE_ID)
    logger.info(
        "sleeptime.done findings=%d blocks_updated=%d",
        len(result.get("findings", []) or []),
        len(result.get("blocks_updated", []) or []),
    )


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------


def _wire_reactive_pipes() -> None:
    """Import memvault.events to register the five reactive subscriptions.

    Best-effort: if event_bus stub or any flow fails to wire, log and continue —
    cron jobs can still run.
    """
    try:
        import src.memvault.events  # noqa: F401  — import side-effect wires pipes

        logger.info("reactive.wired memvault.events imported")
    except Exception:
        logger.exception("reactive.wire_failed — cron jobs will still run")


def _build_jobs() -> list[CronJob]:
    return [
        CronJob(
            name="dream_consolidation",
            func=job_dream_consolidation,
            daily_at=(4, 0),
        ),
        CronJob(
            name="interest_snapshot",
            func=job_interest_snapshot,
            daily_at=(5, 0),
        ),
        CronJob(
            name="sleeptime_reflection",
            func=job_sleeptime_reflection,
            every_minutes=30,
        ),
    ]


async def amain() -> None:
    logger.info("worker.boot")
    _wire_reactive_pipes()
    jobs = _build_jobs()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows / restricted env — fall back to default behavior
            pass

    logger.info("worker.ready jobs=%d", len(jobs))
    try:
        await _scheduler_loop(jobs, stop)
    finally:
        logger.info("worker.shutdown")


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
