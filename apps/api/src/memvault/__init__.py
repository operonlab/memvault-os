"""Memvault module — LLM memory persistence, semantic search, KAS profiles, Knowledge Graph."""

from src.shared.grc_routes import create_grc_routes

from .grc_adapter import MemvaultGRCAdapter
from .kg_routes import router as kg_router
from .routes import router

grc_router = create_grc_routes(
    MemvaultGRCAdapter(), "memvault", "memvault.read", "memvault.write"
)
router.include_router(grc_router)
router.include_router(kg_router)
