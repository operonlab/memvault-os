"""Personalized PageRank on entity graphs — HippoRAG-inspired retrieval.

Provides PPR-based graph ranking to complement Leiden community detection.
PPR surfaces multi-hop connected entities from seed nodes, enabling
relationship-aware retrieval beyond pure vector similarity.

Uses igraph's native personalized_pagerank() which supports weighted edges.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def personalized_pagerank(
    graph: Any,
    seed_entities: list[str],
    entity_to_idx: dict[str, int],
    damping: float = 0.85,
    top_k: int = 20,
) -> list[tuple[str, float]]:
    """Run Personalized PageRank from seed entity nodes.

    Args:
        graph: igraph.Graph with vertex attribute "name" = entity text.
        seed_entities: Entity names to seed the PPR walk from.
        entity_to_idx: Mapping from entity name to vertex index.
        damping: Damping factor (0.85 = standard, higher = stay closer to seeds).
        top_k: Number of top-ranked entities to return.

    Returns:
        List of (entity_name, ppr_score) tuples, sorted descending by score.
    """
    if graph.vcount() == 0:
        return []

    # Build reset vector: seed vertices get equal weight, rest get 0
    reset = [0.0] * graph.vcount()
    valid_seeds = 0
    for entity in seed_entities:
        idx = entity_to_idx.get(entity)
        if idx is not None and idx < graph.vcount():
            reset[idx] = 1.0
            valid_seeds += 1

    if valid_seeds == 0:
        logger.debug("ppr: no valid seed entities found in graph")
        return []

    # Normalize reset vector
    for i in range(len(reset)):
        reset[i] /= valid_seeds

    # Run PPR with edge weights if available
    weights = graph.es["weight"] if graph.ecount() > 0 and "weight" in graph.es.attributes() else None

    scores = graph.personalized_pagerank(
        reset=reset,
        damping=damping,
        weights=weights,
    )

    # Pair entity names with scores and sort
    name_scores = [
        (graph.vs[i]["name"], scores[i])
        for i in range(graph.vcount())
        if scores[i] > 0
    ]
    name_scores.sort(key=lambda x: x[1], reverse=True)

    return name_scores[:top_k]


def global_pagerank(
    graph: Any,
    top_k: int = 20,
) -> list[tuple[str, float]]:
    """Run standard (non-personalized) PageRank on the full graph.

    Useful for identifying hub entities (knowledge pillars) and
    orphan entities (forgetting candidates) in the dream loop.

    Returns:
        List of (entity_name, pagerank_score) tuples, sorted descending.
    """
    if graph.vcount() == 0:
        return []

    weights = graph.es["weight"] if graph.ecount() > 0 and "weight" in graph.es.attributes() else None

    scores = graph.pagerank(weights=weights)

    name_scores = [
        (graph.vs[i]["name"], scores[i])
        for i in range(graph.vcount())
    ]
    name_scores.sort(key=lambda x: x[1], reverse=True)

    return name_scores[:top_k]


def ppr_from_triples(
    triples: list[dict[str, Any]],
    seed_entities: list[str],
    top_k: int = 20,
    damping: float = 0.85,
    subject_key: str = "subject",
    object_key: str = "object",
) -> list[tuple[str, float]]:
    """Build graph from triples and run PPR in one call.

    Convenience function that combines build_entity_graph() + personalized_pagerank().

    Args:
        triples: List of dicts with subject/object keys.
        seed_entities: Entity names to seed the PPR walk from.
        top_k: Number of top-ranked entities to return.
        damping: Damping factor for PPR.
        subject_key: Key name for subject in triple dicts.
        object_key: Key name for object in triple dicts.

    Returns:
        List of (entity_name, ppr_score) tuples, sorted descending.
    """
    from .community import build_entity_graph

    if not triples or not seed_entities:
        return []

    graph, entity_to_idx = build_entity_graph(
        triples, subject_key=subject_key, object_key=object_key
    )

    return personalized_pagerank(
        graph, seed_entities, entity_to_idx,
        damping=damping, top_k=top_k,
    )
