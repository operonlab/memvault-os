"""Community detection via Leiden algorithm on entity graphs.

Extracted from mcp/memvault/pipelines/community_pipeline.py.
Pure graph algorithms — no ORM or DB dependency.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Default Leiden resolution levels
DEFAULT_RESOLUTIONS: dict[int, float] = {
    0: 1.0,   # fine: many small communities
    1: 0.3,   # medium
    2: 0.05,  # coarse: few large themes
}

# Only run Leiden on components with >= this many vertices
MIN_COMPONENT_FOR_LEIDEN = 5


def build_entity_graph(
    triples: list[dict[str, Any]],
    subject_key: str = "subject",
    object_key: str = "object",
) -> tuple[Any, dict[str, int]]:
    """Build undirected graph: entities as vertices, co-occurrence in triples as edges.

    Args:
        triples: List of dicts with subject/object keys.
        subject_key: Key name for subject (default "subject", memvault uses "s").
        object_key: Key name for object (default "object", memvault uses "o").

    Returns:
        Tuple of (igraph.Graph, entity_to_idx mapping).
    """
    import igraph as ig

    entities: set[str] = set()
    for t in triples:
        s = t.get(subject_key, "")
        o = t.get(object_key, "")
        if s:
            entities.add(s)
        if o:
            entities.add(o)

    entity_list = sorted(entities)
    entity_to_idx = {e: i for i, e in enumerate(entity_list)}

    edge_counts: dict[tuple[int, int], int] = {}
    for t in triples:
        s = t.get(subject_key, "")
        o = t.get(object_key, "")
        if not s or not o:
            continue
        s_idx = entity_to_idx[s]
        o_idx = entity_to_idx[o]
        if s_idx != o_idx:
            edge = (min(s_idx, o_idx), max(s_idx, o_idx))
            edge_counts[edge] = edge_counts.get(edge, 0) + 1

    edges = list(edge_counts.keys())
    weights = [edge_counts[e] for e in edges]

    g = ig.Graph(n=len(entity_list), edges=edges, directed=False)
    g.vs["name"] = entity_list
    if weights:
        g.es["weight"] = weights

    logger.info("build_entity_graph: %d entities, %d edges", len(entity_list), len(edges))
    return g, entity_to_idx


def run_leiden(
    graph: Any,
    resolutions: dict[int, float] | None = None,
    min_component: int = MIN_COMPONENT_FOR_LEIDEN,
) -> dict[int, list[list[int]]]:
    """Detect communities using connected components + Leiden subdivision.

    Strategy adapted to sparse KGs (many small disconnected components):
      Level 0 (fine): Connected components >= min_component → Leiden sub-communities.
                      Components 2-(min_component-1) → kept as-is.
      Level 1+: Leiden at lower resolutions on large components only.

    Args:
        graph: igraph.Graph instance.
        resolutions: Dict mapping level → resolution (defaults to DEFAULT_RESOLUTIONS).
        min_component: Minimum component size for Leiden (smaller kept as-is).

    Returns:
        Dict mapping level → list of community member lists (global vertex indices).
    """
    res_map = resolutions or DEFAULT_RESOLUTIONS

    components = graph.connected_components()
    comp_groups: dict[int, list[int]] = {}
    for v_idx, comp_id in enumerate(components.membership):
        comp_groups.setdefault(comp_id, []).append(v_idx)

    large_comps = {
        cid: members for cid, members in comp_groups.items() if len(members) >= min_component
    }
    small_comps = {
        cid: members
        for cid, members in comp_groups.items()
        if 2 <= len(members) < min_component
    }

    results: dict[int, list[list[int]]] = {}

    for level, resolution in res_map.items():
        communities: list[list[int]] = []

        for _cid, members in large_comps.items():
            sub = graph.subgraph(members)
            partition = sub.community_leiden(
                objective_function="modularity",
                resolution=resolution,
                weights="weight" if sub.es and "weight" in sub.es.attributes() else None,
                n_iterations=5,
            )
            for comm_members in partition:
                if len(comm_members) >= 2:
                    global_members = [members[i] for i in comm_members]
                    communities.append(global_members)

        # Small components: include as-is at level 0 only
        if level == 0:
            for members in small_comps.values():
                communities.append(members)

        results[level] = communities
        logger.info(
            "run_leiden: level=%d res=%.2f → %d communities",
            level,
            resolution,
            len(communities),
        )

    return results


def assign_triples_to_communities(
    triples: list[dict[str, Any]],
    entity_to_idx: dict[str, int],
    entity_to_community: dict[int, int],
    subject_key: str = "subject",
    object_key: str = "object",
) -> dict[int, list[dict[str, Any]]]:
    """Map each triple to a community: use subject's community (fallback to object's).

    Args:
        triples: List of triple dicts.
        entity_to_idx: Entity name → graph vertex index.
        entity_to_community: Vertex index → community ID.
        subject_key: Key for subject in triple dict.
        object_key: Key for object in triple dict.

    Returns:
        Dict mapping community_id → list of triples.
    """
    buckets: dict[int, list[dict[str, Any]]] = {}
    unassigned = 0

    for t in triples:
        s = t.get(subject_key, "")
        o = t.get(object_key, "")
        s_idx = entity_to_idx.get(s)
        o_idx = entity_to_idx.get(o)

        comm_id = None
        if s_idx is not None and s_idx in entity_to_community:
            comm_id = entity_to_community[s_idx]
        elif o_idx is not None and o_idx in entity_to_community:
            comm_id = entity_to_community[o_idx]

        if comm_id is None:
            unassigned += 1
            continue

        buckets.setdefault(comm_id, []).append(t)

    if unassigned:
        logger.debug("assign_triples_to_communities: %d triples unassigned", unassigned)

    return buckets
