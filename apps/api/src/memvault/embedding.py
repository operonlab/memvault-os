"""Memvault embedding — re-exports from shared embedding service."""

from src.shared.embedding import EMBEDDING_DIM, get_embedding, get_embeddings_batch

__all__ = ["EMBEDDING_DIM", "get_embedding", "get_embeddings_batch"]
