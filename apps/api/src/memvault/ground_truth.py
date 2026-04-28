"""Ground truth builder — validates knowledge claims against actual system state.

Builds a frozen truth map from:
- port_registry.py (service ports)
- core/src/modules/ directory (module names)
- stations/ directory (station names)
- Hardcoded deprecated names (Pulso, old URLs)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Project root: 4 levels up from this file (core/src/modules/memvault/ → workshop/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


@dataclass(frozen=True)
class GroundTruth:
    """Frozen snapshot of system state for knowledge validation."""

    port_map: dict[str, int]  # service_name → port
    module_names: frozenset[str]  # core module directory names
    module_count: int
    service_names: frozenset[str]  # all registered service names
    station_names: frozenset[str]  # stations/ subdirectory names
    deprecated_names: frozenset[str]  # known-dead names


# Names that are definitively deprecated — any triple referencing these is stale
_DEPRECATED_NAMES = frozenset(
    {
        "pulso",
        "claw.joneshong.com",
        "browser-bridge",
        "browser_bridge",
    }
)

# Predicates whose object values can be fact-checked against system state
GROUNDABLE_PREDICATES = frozenset(
    {
        "implemented_as",
        "configured_with",
        "default_is",
        "format_is",
        "uses",
        "depends_on",
        "requires",
        "pattern_is",
        "flow_is",
    }
)

# Port number regex (4-5 digit numbers in typical service port range)
_PORT_RE = re.compile(r"\b(1\d{3,4}|[2-9]\d{3}|[1-6]\d{4})\b")

# Module count regex (e.g., "13 modules", "17 domain modules")
_MODULE_COUNT_RE = re.compile(r"(\d+)\s*(?:domain\s+)?modules?", re.IGNORECASE)


def build_ground_truth(project_root: Path | None = None) -> GroundTruth:
    """Build truth map from filesystem state. Call once per lint run."""
    root = project_root or _PROJECT_ROOT

    # Port registry
    port_map: dict[str, int] = {}
    service_names: set[str] = set()
    try:
        import sys

        sdk_path = str(root / "libs" / "sdk-client")
        if sdk_path not in sys.path:
            sys.path.insert(0, sdk_path)
        from sdk_client.port_registry import PORTS

        for sp in PORTS:
            port_map[sp.name] = sp.port
            service_names.add(sp.name)
    except ImportError:
        pass

    # Core modules
    modules_dir = root / "core" / "src" / "modules"
    module_names: set[str] = set()
    if modules_dir.is_dir():
        module_names = {
            d.name for d in modules_dir.iterdir() if d.is_dir() and not d.name.startswith("_")
        }

    # Stations
    stations_dir = root / "stations"
    station_names: set[str] = set()
    if stations_dir.is_dir():
        station_names = {
            d.name for d in stations_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
        }

    return GroundTruth(
        port_map=port_map,
        module_names=frozenset(module_names),
        module_count=len(module_names),
        service_names=frozenset(service_names),
        station_names=frozenset(station_names),
        deprecated_names=_DEPRECATED_NAMES,
    )


def check_port_claim(text: str, truth: GroundTruth) -> tuple[int, int] | None:
    """Extract a port number from text and check against truth.

    Returns (claimed_port, actual_port) if drift detected, None if OK or not a port claim.
    """
    match = _PORT_RE.search(text)
    if not match:
        return None
    claimed = int(match.group(1))
    # Check if any service has this port as its OLD value
    for name, actual in truth.port_map.items():
        if name.lower() in text.lower() and claimed != actual:
            return (claimed, actual)
    return None


def check_module_count_claim(text: str, truth: GroundTruth) -> tuple[int, int] | None:
    """Extract a module count claim and compare to actual.

    Returns (claimed_count, actual_count) if drift > 2, None otherwise.
    """
    match = _MODULE_COUNT_RE.search(text)
    if not match:
        return None
    claimed = int(match.group(1))
    if abs(claimed - truth.module_count) > 2:
        return (claimed, truth.module_count)
    return None


def check_deprecated_reference(text: str, truth: GroundTruth) -> str | None:
    """Check if text references a deprecated name. Returns the name if found."""
    text_lower = text.lower()
    for name in truth.deprecated_names:
        if name in text_lower:
            return name
    return None


def is_groundable(predicate: str) -> bool:
    """Check if a predicate's claims can be validated against system state."""
    return predicate in GROUNDABLE_PREDICATES
