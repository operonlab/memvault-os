"""G-R-C Route Factory — auto-mount /reflect + /curate based on adapter capabilities.

Usage in module __init__.py:
    from src.shared.grc_routes import create_grc_routes
    from .grc_adapter import MyAdapter

    grc_router = create_grc_routes(
        MyAdapter(), "mymodule", "mymodule.read", "mymodule.write",
    )
    router.include_router(grc_router)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.capabilities import has_capability
from src.shared.deps import get_db, require_permission
from src.shared.grc import (
    GRCConfig,
    SupportsCurate,
    SupportsGenerate,
    SupportsReflect,
)

logger = logging.getLogger(__name__)


def create_grc_routes(
    adapter: Any,
    module_name: str,
    read_permission: str,
    write_permission: str,
) -> APIRouter:
    """Create /reflect and/or /curate routes based on adapter capabilities.

    Only mounts endpoints for protocols the adapter actually implements.
    """
    grc_router = APIRouter(tags=[f"{module_name}-grc"])

    if has_capability(adapter, SupportsReflect):

        @grc_router.post("/reflect")
        async def reflect_endpoint(
            scope_id: str = Query("default"),
            db: AsyncSession = Depends(get_db),
            _user: dict = require_permission(read_permission),
        ) -> dict:
            # Optional async pre-fetch hook: if adapter defines fetch_blocks(),
            # await it first and pass the result as blocks= kwarg to gather_items().
            # This keeps gather_items() sync-compatible with the Protocol definition.
            extra_kwargs: dict[str, Any] = {"db": db}
            if hasattr(adapter, "fetch_blocks"):
                blocks = await adapter.fetch_blocks(db, scope_id)
                extra_kwargs["blocks"] = blocks
            items = adapter.gather_items(scope_id, **extra_kwargs)
            result = adapter.reflect(items, scope_id)

            derived_count = 0
            if has_capability(adapter, SupportsGenerate):
                derived = adapter.generate_derived(result, db=db)
                derived_count = len(derived)

            logger.info(
                "grc.reflected module=%s scope=%s items=%d insights=%d",
                module_name,
                scope_id,
                result.items_analyzed,
                len(result.insights),
            )

            return {
                "module": result.module,
                "scope_id": result.scope_id,
                "items_analyzed": result.items_analyzed,
                "insights": result.insights,
                "anomalies": result.anomalies,
                "corrections": result.corrections,
                "metrics": result.metrics,
                "derived_count": derived_count,
                "reflected_at": result.reflected_at.isoformat(),
            }

    if has_capability(adapter, SupportsCurate):

        @grc_router.post("/curate")
        async def curate_endpoint(
            scope_id: str = Query("default"),
            dry_run: bool = Query(False),
            confidence_threshold: float = Query(0.15, ge=0.0, le=1.0),
            db: AsyncSession = Depends(get_db),
            _user: dict = require_permission(write_permission),
        ) -> dict:
            config = GRCConfig(confidence_threshold=confidence_threshold)
            extra_kwargs: dict[str, Any] = {"db": db}
            if hasattr(adapter, "fetch_blocks"):
                blocks = await adapter.fetch_blocks(db, scope_id)
                extra_kwargs["blocks"] = blocks
            actions = adapter.identify_candidates(scope_id, config=config, **extra_kwargs)
            result = await adapter.apply_actions(actions, dry_run=dry_run, db=db)

            if not dry_run:
                await db.commit()

            logger.info(
                "grc.curated module=%s scope=%s applied=%d skipped=%d dry=%s",
                module_name,
                scope_id,
                result.applied_count,
                result.skipped_count,
                dry_run,
            )

            return {
                "module": result.module,
                "scope_id": result.scope_id,
                "applied_count": result.applied_count,
                "skipped_count": result.skipped_count,
                "dry_run": result.dry_run,
                "curated_at": result.curated_at.isoformat(),
            }

    return grc_router
