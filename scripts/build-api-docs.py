#!/usr/bin/env python3
"""Generate docs/api-reference.md from docs/route_manifest.yaml.

Reads the manifest grouped by section and emits one markdown section per group,
with method/path/scope/handler/source. Schema bodies are not rendered automatically;
example payloads are pulled from a small curated table below for the most common
endpoints. Anything not in the table just shows the route signature.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "route_manifest.yaml"
OUT = ROOT / "docs" / "api-reference.md"

SECTION_TITLES: dict[str, str] = {
    "blocks": "Memory Blocks",
    "sessions": "Sessions",
    "search_query": "Search & Query",
    "kg_recall": "Cascade Recall",
    "recall_text": "Prefetch & Metrics",
    "tags_domains": "Tags & Domains",
    "profile": "Profile Score",
    "ops": "Feedback & Sync",
    "frozen": "Frozen Tier",
    "dream_review": "Dream Loop & Review Queue",
    "kg_triples": "Knowledge Graph — Triples",
    "kg_communities": "Knowledge Graph — Communities",
    "kg_entities": "Knowledge Graph — Entities",
    "kg_maintenance": "Knowledge Graph — Maintenance",
    "kg_intelligence": "Knowledge Graph — Intelligence",
    "other": "Other",
}

# Curated examples for the most-used endpoints. Anything not listed here just
# shows the route signature without an example payload.
EXAMPLES: dict[tuple[str, str], dict[str, str]] = {
    ("POST", "/api/memvault/blocks"): {
        "request": '{"content": "Postgres pgvector beats SQLite for hybrid search.", "block_type": "knowledge", "tags": ["postgres", "pgvector"]}',
        "response": '{"id": "01J...", "content": "Postgres pgvector beats SQLite for hybrid search.", "block_type": "knowledge", "tags": ["postgres","pgvector"], "confidence": 0.0, "created_at": "2026-04-28T08:00:00Z"}',
    },
    ("GET", "/api/memvault/search"): {
        "request": "?q=pgvector&top_k=5",
        "response": '{"results": [{"block": {...}, "score": 0.83}], "metadata": {"vector_used": true, "scoring_applied": true}}',
    },
    ("POST", "/api/memvault/query"): {
        "request": '{"q": "what did we decide about embeddings?", "task_mode": "lookup", "thinking_mode": "auto", "load_budget": "standard", "top_k": 6}',
        "response": '{"query": "...", "strategy": {...}, "cards": [{"id":"01J...","title":"...","summary":"..."}], "cascade_cards": [], "highlights": []}',
    },
    ("POST", "/api/memvault/inject"): {
        "request": '{"q": "session warmup for postgres planning", "task_mode": "build", "load_budget": "deep"}',
        "response": '{"system_prompt_memory": "## Relevant memories\\n- ...", "working_context": ["..."], "decision_bias": ["..."], "cards": [...]}',
    },
    ("POST", "/api/memvault/kg/triples"): {
        "request": '{"subject": "memvault-os", "predicate": "uses", "object": "qdrant", "confidence": 0.9}',
        "response": '{"id": "01J...", "subject": "memvault-os", "predicate": "uses", "object": "qdrant", "confidence": 0.9}',
    },
    ("GET", "/api/memvault/kg/recall"): {
        "request": "?seed=memvault-os&depth=2&limit=20",
        "response": '{"triples": [...], "entities": [...], "scores": {...}}',
    },
    ("POST", "/api/memvault/dream"): {
        "request": '{"window_hours": 24, "max_blocks": 200}',
        "response": '{"consolidated": 12, "invalidated": 3, "review_queued": 2}',
    },
    ("GET", "/api/memvault/review-queue"): {
        "request": "?status=pending&limit=20",
        "response": '{"items": [{"id":"01J...","kind":"dream_invalidation","block_id":"...","reason":"..."}], "total": 1}',
    },
}


def render() -> str:
    manifest = yaml.safe_load(MANIFEST.read_text())
    total_routes = sum(len(v) for v in manifest.values())

    lines: list[str] = []
    lines.append("# memvault-os — API Reference")
    lines.append("")
    lines.append(
        f"Auto-generated from [`docs/route_manifest.yaml`](./route_manifest.yaml) "
        f"by `scripts/build-api-docs.py`. Total routes: **{total_routes}**."
    )
    lines.append("")
    lines.append("All routes are mounted under the api container (host port `${API_PORT:-8080}`). "
                 "Auth in v1 single-user mode is a stub — every request runs as the lone owner. "
                 "The `scope` column reflects the permission token an embedded multi-user deployment would enforce.")
    lines.append("")

    # Table of contents
    lines.append("## Sections")
    lines.append("")
    for key in manifest.keys():
        title = SECTION_TITLES.get(key, key)
        anchor = title.lower().replace(" ", "-").replace("&", "").replace("—", "").replace("--", "-").strip("-")
        lines.append(f"- [{title}](#{anchor}) — {len(manifest[key])} route(s)")
    lines.append("")

    for key, routes in manifest.items():
        title = SECTION_TITLES.get(key, key)
        lines.append(f"## {title}")
        lines.append("")
        for r in sorted(routes, key=lambda x: (x["path"], x["method"])):
            method = r["method"]
            path = r["path"]
            scope = r.get("scope") or "—"
            handler = r.get("handler", "?")
            source = r.get("source", "?")
            desc = r.get("description")

            lines.append(f"### `{method} {path}`")
            lines.append("")
            lines.append(f"- **Scope:** `{scope}`")
            lines.append(f"- **Handler:** `{handler}` ([{source}](../apps/api/src/memvault/{source.split(':')[0]}))")
            if desc:
                lines.append(f"- **Description:** {desc}")
            lines.append("")

            example = EXAMPLES.get((method, path))
            if example:
                lines.append("**Example request:**")
                lines.append("")
                lines.append("```http")
                if method == "GET":
                    lines.append(f"{method} {path}{example['request']}")
                else:
                    lines.append(f"{method} {path}")
                    lines.append("Content-Type: application/json")
                    lines.append("")
                    lines.append(example["request"])
                lines.append("```")
                lines.append("")
                lines.append("**Example response:**")
                lines.append("")
                lines.append("```json")
                lines.append(example["response"])
                lines.append("```")
                lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Regenerate")
    lines.append("")
    lines.append("```bash")
    lines.append("python3 scripts/build-api-docs.py")
    lines.append("```")
    lines.append("")
    lines.append("`docs/route_manifest.yaml` itself is regenerated by the upstream "
                 "`scripts/build_manifest.py` whenever `apps/api/src/memvault/routes.py` "
                 "or `kg_routes.py` change.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    if not MANIFEST.exists():
        print(f"manifest not found: {MANIFEST}", file=sys.stderr)
        return 1
    content = render()
    OUT.write_text(content)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(content)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
