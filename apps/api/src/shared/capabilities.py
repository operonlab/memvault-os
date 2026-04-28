"""Optional Capability Protocols — Go-style type assertion for Python adapters.

Inspired by cc-connect's Optional Interface pattern: adapters declare capabilities
via Protocol classes, engine detects them at runtime with isinstance().

Usage:
    from src.shared.capabilities import has_capability, SupportsGrouping

    if has_capability(channel, SupportsGrouping):
        group = channel.get_group(payload)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


def has_capability[T](adapter: Any, capability: type[T]) -> bool:
    """Check if an adapter implements an optional capability.

    Equivalent to Go's type assertion: `if switcher, ok := agent.(ModelSwitcher); ok { ... }`
    """
    return isinstance(adapter, capability)


def get_capabilities(adapter: Any, *capabilities: type) -> dict[type, bool]:
    """Check multiple capabilities at once. Returns {capability_type: bool}."""
    return {cap: isinstance(adapter, cap) for cap in capabilities}


# ─── Notification Capabilities ────────────────────────────────────────

@runtime_checkable
class SupportsGrouping(Protocol):
    """Adapter can group notifications by category/topic."""

    def get_group(self, category: str) -> str:
        """Map notification category to adapter-specific group name."""
        ...


@runtime_checkable
class SupportsPriority(Protocol):
    """Adapter supports priority/urgency levels."""

    def map_severity(self, severity: str) -> str:
        """Map Workshop severity (critical/warning/info) to adapter-specific level."""
        ...


@runtime_checkable
class SupportsIcon(Protocol):
    """Adapter can display custom icons."""

    def get_icon_url(self, category: str) -> str:
        """Return icon URL for the given notification category."""
        ...


@runtime_checkable
class SupportsSound(Protocol):
    """Adapter can play notification sounds."""

    sound_options: list[str]


@runtime_checkable
class SupportsRichContent(Protocol):
    """Adapter supports rich content (markdown, HTML, images)."""

    supported_formats: list[str]  # e.g. ["markdown", "html", "image"]

    def render_rich(self, body: str, fmt: str) -> str:
        """Render body in the specified format."""
        ...


# ─── Generic Adapter Capabilities (for future bridges/, etc.) ────────

@runtime_checkable
class SupportsHealthCheck(Protocol):
    """Adapter can report its health status."""

    async def health_check(self) -> bool:
        """Return True if the adapter is operational."""
        ...


@runtime_checkable
class SupportsRateLimit(Protocol):
    """Adapter has rate limiting information."""

    rate_limit_per_minute: int
