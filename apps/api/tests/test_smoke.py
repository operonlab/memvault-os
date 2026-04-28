"""Smoke tests — fast sanity checks that the API surface boots.

All tests below must run in well under 5 seconds and require zero external
services (no DB, no Redis, no Qdrant connection). They guard against:

  * import-time crashes in `src/main.py` (missing module, circular import)
  * route registration regressions (target ≥ 60, expected 66)
  * health-probe contract drift
  * stub-module schema drift (audit_logs, settings, event bus)

Run with:  pytest tests/test_smoke.py
"""

from __future__ import annotations

import asyncio
import os

import pytest


@pytest.fixture(scope="module")
def app():
    """Import the FastAPI app once per test module.

    Imported lazily so a top-level ImportError surfaces as a failed test
    rather than a collection error.
    """
    from src.main import app as _app

    return _app


def test_app_import(app):
    assert app is not None
    assert app.title == "memvault-os"


def test_routes_count(app):
    # 66 routes expected (memvault CRUD + KG + GRC + 2 health probes).
    # Use ≥ 60 so unrelated trivial route additions don't break the test.
    assert len(app.routes) >= 60, (
        f"only {len(app.routes)} routes registered — expected ≥ 60"
    )


def test_health_liveliness(app):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/health/liveliness")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_audit_stub_schema():
    from src.audit_stub import AuditLog

    assert AuditLog.__tablename__ == "audit_logs"
    # Module column is non-nullable text — guard against accidental rename.
    cols = {c.name for c in AuditLog.__table__.columns}
    for required in ("user_id", "module", "entity_type", "entity_id", "action"):
        assert required in cols, f"AuditLog missing column: {required}"


def test_events_stub_emits():
    """Round-trip: subscribe a coroutine, publish, expect the handler ran."""
    from src.events_stub import Event, event_bus

    received: list[Event] = []

    async def _handler(evt: Event) -> None:
        received.append(evt)

    async def _run() -> None:
        event_bus.subscribe("memvault.smoke.fired", _handler)
        await event_bus.start()
        try:
            await event_bus.publish(
                Event(type="memvault.smoke.fired", data={"x": 1}, source="smoke-test")
            )
            # Allow scheduled handler to drain.
            await asyncio.sleep(0.05)
        finally:
            await event_bus.stop()

    asyncio.run(_run())

    assert received, "subscribed handler was never invoked"
    assert received[0].type == "memvault.smoke.fired"
    assert received[0].data == {"x": 1}


def test_config_stub_loads(monkeypatch):
    """Settings must read MEMVAULT_-prefixed env vars."""
    monkeypatch.setenv("MEMVAULT_PORT", "12345")
    monkeypatch.setenv("MEMVAULT_DEBUG", "true")

    # Re-import to pick up the patched env (pydantic-settings reads on init).
    from src.config_stub import Settings

    s = Settings()
    assert s.port == 12345
    assert s.debug is True
    # Default values still resolvable.
    assert s.embed_dim == 768

    # Sanity: the singleton also exists and has a db_url.
    from src.config_stub import settings

    assert settings.db_url.startswith("postgresql"), (
        f"unexpected default db_url: {settings.db_url}"
    )


def test_audit_enabled_default():
    """ENABLED toggle reads MEMVAULT_AUDIT_ENABLED at import time."""
    # Don't reload the module here; just check the imported flag exists.
    from src import audit_stub

    assert hasattr(audit_stub, "ENABLED")
    assert audit_stub.SCHEMA == "memvault"
