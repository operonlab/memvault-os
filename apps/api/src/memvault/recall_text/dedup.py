"""
recall_dedup — Client-side deduplication for the Memvault cascade recall pipeline.

The cascade recall API returns results across 4 overlapping layers:
  L2  summaries : CommunitySummaryResponse  — community_id, summary, key_findings, representative_triples
  L1  communities: CommunityResponse        — id, name, size, summary, parent_community_id
  L0  triples   : TripleResponse            — subject, predicate, object
      blocks    : Memory blocks             — topic, content, tags

Three dedup phases are applied in order:

  Phase 1 — L2↔L1 Merge
    CommunitySummary.community_id == Community.id (1:1).
    Inject _community_name/_community_size into matching summaries; drop those
    communities from the L1 list (they are already represented).

  Phase 2 — Community Hierarchy Dedup
    Communities have parent_community_id.
    Parent with <3 children in result → remove parent (children more specific).
    Parent with ≥3 children in result → remove children (parent gives better overview).

  Phase 3 — Triple Cross-Layer Dedup
    L2 representative_triples are text strings already covered by summaries.
    L0 triples that overlap (Jaccard ≥ 0.7) with any representative triple are dropped.
"""


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _normalize_triple_text(text: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    return " ".join(text.lower().split())


def _text_overlap(needle: str, haystack_set: set, threshold: float = 0.7) -> bool:
    """Return True if needle's Jaccard word overlap >= threshold with any string in haystack_set."""
    needle_tokens = set(needle.split())
    if len(needle_tokens) < 2:
        return False
    for hay in haystack_set:
        hay_tokens = set(hay.split())
        union = needle_tokens | hay_tokens
        if not union:
            continue
        intersection = needle_tokens & hay_tokens
        if len(intersection) / len(union) >= threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# Phase implementations
# ---------------------------------------------------------------------------

def _phase1_merge_l2_l1(summaries: list, communities: list) -> tuple:
    """Merge CommunitySummary↔Community by community_id; filter matched communities."""
    community_by_id = {c["id"]: c for c in communities if c.get("id")}
    summary_community_ids = set()

    for summary in summaries:
        cid = summary.get("community_id")
        if cid and cid in community_by_id:
            community = community_by_id[cid]
            summary["_community_name"] = community.get("name")
            summary["_community_size"] = community.get("size")
            summary_community_ids.add(cid)

    remaining_communities = [c for c in communities if c.get("id") not in summary_community_ids]
    return summaries, remaining_communities


def _phase2_hierarchy_dedup(communities: list) -> list:
    """Remove redundant parent or child communities based on child count threshold."""
    if not communities:
        return communities

    community_ids = {c["id"] for c in communities if c.get("id")}

    # Build parent → children mapping (only for communities present in result)
    parent_to_children: dict = {}
    for c in communities:
        pid = c.get("parent_community_id")
        cid = c.get("id")
        if pid and pid in community_ids and cid:
            parent_to_children.setdefault(pid, []).append(cid)

    remove_ids: set = set()
    for parent_id, child_ids in parent_to_children.items():
        if len(child_ids) < 3:
            # Few children — children are more specific; remove parent
            remove_ids.add(parent_id)
        else:
            # Many children — parent gives better overview; remove children
            remove_ids.update(child_ids)

    return [c for c in communities if c.get("id") not in remove_ids]


def _phase3_triple_dedup(summaries: list, triples: list) -> list:
    """Remove L0 triples already covered by L2 representative_triples (Jaccard >= 0.7)."""
    rep_texts: set = set()
    for summary in summaries:
        for rt in summary.get("representative_triples") or []:
            if isinstance(rt, str):
                rep_texts.add(_normalize_triple_text(rt))

    if not rep_texts:
        return triples

    result = []
    for triple in triples:
        subject = triple.get("subject", "") or ""
        predicate = triple.get("predicate", "") or ""
        obj = triple.get("object", "") or ""
        normalized = _normalize_triple_text(f"{subject} {predicate} {obj}")
        if not _text_overlap(normalized, rep_texts):
            result.append(triple)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def dedup_cascade(cascade_data: dict) -> dict:
    """
    Run three dedup phases over cascade recall results and return the modified dict.

    Modifies cascade_data in place (also returns it for convenience).
    Missing layers are skipped gracefully.
    """
    summaries = cascade_data.get("summaries") or []
    communities = cascade_data.get("communities") or []
    triples = cascade_data.get("triples") or []

    # Phase 1: L2↔L1 merge
    if summaries or communities:
        summaries, communities = _phase1_merge_l2_l1(summaries, communities)

    # Phase 2: hierarchy dedup (on remaining communities after Phase 1)
    if communities:
        communities = _phase2_hierarchy_dedup(communities)

    # Phase 3: triple cross-layer dedup
    if triples and summaries:
        triples = _phase3_triple_dedup(summaries, triples)

    cascade_data["summaries"] = summaries
    cascade_data["communities"] = communities
    cascade_data["triples"] = triples
    return cascade_data
