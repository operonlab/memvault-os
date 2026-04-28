"""Shared FastAPI dependencies — memvault-os standalone."""

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.database import get_db as _get_db
from src.shared.errors import ForbiddenError


async def get_db() -> AsyncSession:
    """Alias for database.get_db — use in Depends()."""
    async for session in _get_db():
        yield session


def get_current_user(request: Request) -> dict:  # noqa: ARG001
    """Single-tenant OSS stub: returns an implicit owner with full perms."""
    return {"id": "owner", "role": "admin", "space_id": "default"}


def require_permission(permission: str):  # noqa: ARG001
    """Dependency factory: in OS single-tenant mode, always allow."""
    from src.auth_stub import has_permission

    def _check(request: Request) -> dict:
        user = get_current_user(request)
        if not has_permission(user, permission):
            raise ForbiddenError(
                f"Permission denied: {permission}",
                code=f"{permission.split('.')[0]}.forbidden",
            )
        return user

    return Depends(_check)
