"""Multi-scope memory isolation for memvault.

Parses scope strings and generates SQLAlchemy WHERE clauses.
"""

from dataclasses import dataclass

from sqlalchemy import ColumnElement

from .models import MemoryBlock

VALID_SCOPE_PREFIXES = ("global", "session", "user", "type")


@dataclass
class Scope:
    kind: str  # "global", "session", "user", "type"
    value: str | None = None  # None for global

    @classmethod
    def parse(cls, scope_str: str) -> "Scope":
        """Parse scope string like 'session:abc123' or 'global'."""
        if not scope_str or scope_str == "global":
            return cls(kind="global")
        if ":" not in scope_str:
            raise ValueError(
                f"Invalid scope format: {scope_str}. Expected 'kind:value' or 'global'"
            )
        kind, value = scope_str.split(":", 1)
        if kind not in VALID_SCOPE_PREFIXES:
            raise ValueError(f"Unknown scope kind: {kind}. Valid: {VALID_SCOPE_PREFIXES}")
        if kind == "global":
            return cls(kind="global")
        return cls(kind=kind, value=value)

    def to_filter(self) -> ColumnElement | None:
        """Convert scope to SQLAlchemy WHERE clause. Returns None for global."""
        if self.kind == "global" or not self.value:
            return None
        if self.kind == "session":
            return MemoryBlock.source_session == self.value
        if self.kind == "user":
            return MemoryBlock.created_by == self.value
        if self.kind == "type":
            return MemoryBlock.block_type == self.value
        return None

    def __str__(self) -> str:
        if self.kind == "global":
            return "global"
        return f"{self.kind}:{self.value}"


def parse_scopes(scope_str: str | None) -> list[Scope]:
    """Parse comma-separated scope string into list of Scopes.

    Examples:
        "global" → [Scope(global)]
        "session:abc123" → [Scope(session, abc123)]
        "session:abc,type:knowledge" → [Scope(session,abc), Scope(type,knowledge)]
    """
    if not scope_str or scope_str.strip() == "global":
        return [Scope(kind="global")]
    parts = [p.strip() for p in scope_str.split(",") if p.strip()]
    return [Scope.parse(p) for p in parts]


def scopes_to_filters(scopes: list[Scope]) -> list[ColumnElement]:
    """Convert list of scopes to SQLAlchemy filters. Global scope produces no filters."""
    filters = []
    for s in scopes:
        f = s.to_filter()
        if f is not None:
            filters.append(f)
    return filters
