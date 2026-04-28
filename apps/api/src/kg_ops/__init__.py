"""Knowledge Graph operators — shared by memvault and docvault.

Entity normalization, predicate vocabulary, triple extraction,
Leiden community detection, community summary prompts, and
Personalized PageRank for graph-aware retrieval.
"""

from .community import assign_triples_to_communities, build_entity_graph, run_leiden
from .community_prompts import build_community_summary_messages, build_triple_text
from .normalize import normalize_entity_text
from .pagerank import global_pagerank, personalized_pagerank, ppr_from_triples
from .predicates import (
    PREDICATE_ALIASES,
    PREDICATE_VOCABULARY,
    VALID_PREDICATES,
    normalize_predicate,
    register_predicates,
)
from .triple_extract import (
    build_extraction_prompt,
    extract_triples,
    validate_extracted_triples,
)

__all__ = [
    "PREDICATE_ALIASES",
    "PREDICATE_VOCABULARY",
    "VALID_PREDICATES",
    "assign_triples_to_communities",
    "build_community_summary_messages",
    "build_entity_graph",
    "build_extraction_prompt",
    "build_triple_text",
    "extract_triples",
    "global_pagerank",
    "normalize_entity_text",
    "normalize_predicate",
    "personalized_pagerank",
    "ppr_from_triples",
    "register_predicates",
    "run_leiden",
    "validate_extracted_triples",
]
