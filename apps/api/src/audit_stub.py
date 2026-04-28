"""audit_stub — minimal AuditLog model for memvault-os.

Replaces monorepo's `src.modules.admin.models.AuditLog`. Column types fully
mirror admin.AuditLog (`core/src/modules/admin/models.py:23-`) so
`BaseCRUDService._record_audit()` works without modification.

Toggled via env: `MEMVAULT_AUDIT_ENABLED=false` to early-return in
`_record_audit()` (no DB write).

Lives in the `memvault` schema (OS consolidates everything into one schema,
unlike monorepo where audit_logs sits under `admin`).
"""

from __future__ import annotations

import os

from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.models import Base, TimestampMixin

ENABLED = os.getenv("MEMVAULT_AUDIT_ENABLED", "true").lower() == "true"

SCHEMA = "memvault"


class AuditLog(Base, TimestampMixin):
    """Audit log entry — created/updated/deleted/restored/purged events.

    Columns mirror monorepo admin.AuditLog 9 fields (id, user_id, module,
    entity_type, entity_id, space_id, action, changes, snapshot) plus the
    inherited timestamps from TimestampMixin.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_entity", "module", "entity_type", "entity_id"),
        Index("idx_audit_user", "user_id"),
        Index("idx_audit_created", "created_at"),
        Index("idx_audit_space", "space_id"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    module: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(32), nullable=False)
    space_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # created | updated | deleted | restored | purged
    changes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
