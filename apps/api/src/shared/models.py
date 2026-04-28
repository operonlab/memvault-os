"""Base ORM models — TimestampMixin, SoftDeleteMixin, SpaceScopedModel, GlobalModel."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from uuid_utils import uuid7


def _uuid7_hex() -> str:
    return uuid7().hex


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=_uuid7_hex
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SoftDeleteMixin:
    """Soft delete support — set deleted_at instead of hard deleting."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True
    )


class SpaceScopedModel(TimestampMixin, SoftDeleteMixin, Base):
    """Base for entities scoped to a space (8/10 modules)."""

    __abstract__ = True

    space_id: Mapped[str] = mapped_column(String(32), index=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)


class GlobalModel(TimestampMixin, Base):
    """Base for global entities — auth, admin."""

    __abstract__ = True
