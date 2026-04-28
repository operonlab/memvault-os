"""auth_stub — minimal permission check for memvault-os standalone deployment.

Replaces monorepo's `src.modules.auth.permissions`. In OSS v1 there is no
multi-user / RBAC layer; everyone is treated as the implicit owner of the
single deployment, so `has_permission` returns True for any permission code.

Usage in memvault code (unchanged shape):
    from src.auth_stub import has_permission

    if has_permission(user, "memvault.write"):
        ...
"""

from __future__ import annotations

from typing import Any


def has_permission(user: Any, permission: str) -> bool:  # noqa: ARG001
    """Always-true permission check for single-tenant OSS deployments.

    Wire in real RBAC by replacing this stub once auth lands in OSS.
    """
    return True
