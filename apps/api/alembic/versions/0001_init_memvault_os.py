"""init memvault-os schema (fresh baseline)

Revision ID: 0001_init_memvault_os
Revises:
Create Date: 2026-04-28

Fresh baseline migration for memvault-os. Builds the complete schema
(17 tables) in one shot — does NOT preserve the monorepo's 25-step
migration history. Source of truth: docs/schema_manifest.yaml.

Tables created (memvault schema):
  Memory tier:        blocks, blocks_archive, blocks_frozen, memory_block
  Domain index:       tags, knowledge_domains, profile_scores
  Search:             search_feedback, query_journal, interest_snapshots
  Knowledge graph:    entity_canonicals, entity_edges, triples,
                      communities, community_triples, community_summaries
  Audit:              audit_logs (mirror of monorepo admin.AuditLog)

pgvector extension is enabled at the head of upgrade() to keep the
container image consistent with the monorepo (Postgres 16 + pgvector),
even though OS v1 routes vectors through Qdrant.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_init_memvault_os"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "memvault"


# ---------------------------------------------------------------------------
# Helpers — mirror src.shared.models mixins
# ---------------------------------------------------------------------------


def _timestamp_cols() -> list[sa.Column]:
    """Columns provided by TimestampMixin: id + created_at + updated_at."""
    return [
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def _space_scoped_cols() -> list[sa.Column]:
    """Columns provided by SpaceScopedModel = TimestampMixin + SoftDeleteMixin + space cols."""
    return _timestamp_cols() + [
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("space_id", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
    ]


def _add_mixin_indexes(table: str) -> None:
    """Mirror the index=True flags on SoftDeleteMixin.deleted_at and
    SpaceScopedModel.space_id."""
    op.create_index(
        f"ix_{table}_deleted_at", table, ["deleted_at"], schema=SCHEMA
    )
    op.create_index(
        f"ix_{table}_space_id", table, ["space_id"], schema=SCHEMA
    )


# ---------------------------------------------------------------------------
# upgrade()
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # 0. Extensions + schema ------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # 1. blocks -------------------------------------------------------------
    op.create_table(
        "blocks",
        *_space_scoped_cols(),
        sa.Column("source_session", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "block_type",
            sa.String(length=50),
            server_default=sa.text("'general'"),
            nullable=False,
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("invalid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.String(length=32), nullable=True),
        sa.Column("invalidation_reason", sa.String(length=200), nullable=True),
        sa.Column(
            "access_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fold_id", sa.String(length=16), nullable=True),
        sa.Column("content_hash", sa.String(length=16), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    _add_mixin_indexes("blocks")
    op.create_index("ix_blocks_fold_id", "blocks", ["fold_id"], schema=SCHEMA)
    op.create_index(
        "idx_blocks_tags",
        "blocks",
        ["tags"],
        schema=SCHEMA,
        postgresql_using="gin",
    )
    op.create_index("idx_blocks_type", "blocks", ["block_type"], schema=SCHEMA)
    op.create_index(
        "idx_blocks_session", "blocks", ["source_session"], schema=SCHEMA
    )

    # 2. blocks_archive (pure Base — no soft delete, created_at as Text) ----
    op.create_table(
        "blocks_archive",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("space_id", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("source_session", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("block_type", sa.String(length=50), nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("archived_at", sa.Text(), nullable=False),
        sa.Column(
            "archive_type",
            sa.String(length=20),
            server_default=sa.text("'cold-archive'"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_ba_tags",
        "blocks_archive",
        ["tags"],
        schema=SCHEMA,
        postgresql_using="gin",
    )
    op.create_index(
        "idx_ba_type", "blocks_archive", ["block_type"], schema=SCHEMA
    )
    op.create_index(
        "idx_ba_created", "blocks_archive", ["created_at"], schema=SCHEMA
    )
    op.create_index(
        "idx_ba_archived", "blocks_archive", ["archived_at"], schema=SCHEMA
    )

    # 3. tags ---------------------------------------------------------------
    op.create_table(
        "tags",
        *_space_scoped_cols(),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "usage_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    _add_mixin_indexes("tags")
    op.create_index(
        "idx_tags_name",
        "tags",
        ["space_id", "name"],
        schema=SCHEMA,
        unique=True,
    )

    # 4. knowledge_domains --------------------------------------------------
    op.create_table(
        "knowledge_domains",
        *_space_scoped_cols(),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "maturity",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        sa.Column(
            "block_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    _add_mixin_indexes("knowledge_domains")
    op.create_index(
        "idx_kd_name",
        "knowledge_domains",
        ["space_id", "name"],
        schema=SCHEMA,
        unique=True,
    )

    # 5. profile_scores -----------------------------------------------------
    op.create_table(
        "profile_scores",
        *_space_scoped_cols(),
        sa.Column(
            "knowledge_score",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        sa.Column(
            "attitude_score",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        sa.Column(
            "skill_score",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    _add_mixin_indexes("profile_scores")
    op.create_index(
        "idx_profile_scores_space",
        "profile_scores",
        ["space_id"],
        schema=SCHEMA,
        unique=True,
    )

    # 6. search_feedback ----------------------------------------------------
    op.create_table(
        "search_feedback",
        *_space_scoped_cols(),
        sa.Column("entity_id", sa.String(length=32), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("signal", sa.String(length=20), nullable=False),
        sa.Column(
            "feedback_source",
            sa.String(length=20),
            server_default=sa.text("'agent'"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    _add_mixin_indexes("search_feedback")
    op.create_index(
        "idx_sf_entity", "search_feedback", ["entity_id"], schema=SCHEMA
    )
    op.create_index(
        "idx_sf_query_hash", "search_feedback", ["query_hash"], schema=SCHEMA
    )
    op.create_index(
        "idx_sf_entity_signal",
        "search_feedback",
        ["entity_id", "signal"],
        schema=SCHEMA,
    )

    # 7. query_journal ------------------------------------------------------
    op.create_table(
        "query_journal",
        *_space_scoped_cols(),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("routing_intent", sa.String(length=50), nullable=True),
        sa.Column("routing_confidence", sa.Float(), nullable=True),
        sa.Column(
            "layers_searched",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "result_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("evaluation_verdict", sa.String(length=20), nullable=True),
        sa.Column("evaluation_score", sa.Float(), nullable=True),
        sa.Column(
            "top_entity_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    _add_mixin_indexes("query_journal")
    op.create_index(
        "idx_qj_query_hash", "query_journal", ["query_hash"], schema=SCHEMA
    )
    op.create_index(
        "idx_qj_space_created",
        "query_journal",
        ["space_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_qj_routing_intent",
        "query_journal",
        ["routing_intent"],
        schema=SCHEMA,
    )

    # 8. interest_snapshots -------------------------------------------------
    op.create_table(
        "interest_snapshots",
        *_space_scoped_cols(),
        sa.Column(
            "snapshot_date", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("period", sa.String(length=20), nullable=False),
        sa.Column("top_intents", postgresql.JSONB(), nullable=True),
        sa.Column("top_entities", postgresql.JSONB(), nullable=True),
        sa.Column("top_communities", postgresql.JSONB(), nullable=True),
        sa.Column("knowledge_gaps", postgresql.JSONB(), nullable=True),
        sa.Column("attention_profile", postgresql.JSONB(), nullable=True),
        sa.Column(
            "query_volume",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("avg_result_quality", sa.Float(), nullable=True),
        schema=SCHEMA,
    )
    _add_mixin_indexes("interest_snapshots")
    op.create_index(
        "idx_is_space_date",
        "interest_snapshots",
        ["space_id", "snapshot_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_is_period", "interest_snapshots", ["period"], schema=SCHEMA
    )

    # 9. memory_block (hot snapshot) ---------------------------------------
    op.create_table(
        "memory_block",
        *_space_scoped_cols(),
        sa.Column("block_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "word_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "block_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    _add_mixin_indexes("memory_block")
    op.create_index(
        "uq_memory_block_space_type_active",
        "memory_block",
        ["space_id", "block_type"],
        schema=SCHEMA,
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_memory_block_type", "memory_block", ["block_type"], schema=SCHEMA
    )

    # 10. blocks_frozen (pure Base) ----------------------------------------
    op.create_table(
        "blocks_frozen",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("space_id", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.Text(), nullable=False),
        sa.Column("frozen_at", sa.Text(), nullable=False),
        sa.Column("block_type", sa.String(length=50), nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("source_session", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("s3_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_size", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_bf_space_created",
        "blocks_frozen",
        ["space_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_bf_tags",
        "blocks_frozen",
        ["tags"],
        schema=SCHEMA,
        postgresql_using="gin",
    )
    op.create_index(
        "idx_bf_frozen", "blocks_frozen", ["frozen_at"], schema=SCHEMA
    )
    op.create_index(
        "idx_bf_type", "blocks_frozen", ["block_type"], schema=SCHEMA
    )

    # 11. entity_canonicals ------------------------------------------------
    op.create_table(
        "entity_canonicals",
        *_space_scoped_cols(),
        sa.Column("canonical_name", sa.String(length=500), nullable=False),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "entity_type",
            sa.String(length=50),
            server_default=sa.text("'concept'"),
            nullable=False,
        ),
        sa.Column(
            "merge_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "space_id", "canonical_name", name="uq_entity_canonical_space_name"
        ),
        schema=SCHEMA,
    )
    _add_mixin_indexes("entity_canonicals")
    op.create_index(
        "idx_ec_canonical_name",
        "entity_canonicals",
        ["space_id", "canonical_name"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_ec_entity_type",
        "entity_canonicals",
        ["entity_type"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_ec_aliases",
        "entity_canonicals",
        ["aliases"],
        schema=SCHEMA,
        postgresql_using="gin",
    )

    # 12. entity_edges -----------------------------------------------------
    op.create_table(
        "entity_edges",
        *_space_scoped_cols(),
        sa.Column(
            "entity_a_id",
            sa.String(length=32),
            sa.ForeignKey(f"{SCHEMA}.entity_canonicals.id"),
            nullable=False,
        ),
        sa.Column(
            "entity_b_id",
            sa.String(length=32),
            sa.ForeignKey(f"{SCHEMA}.entity_canonicals.id"),
            nullable=False,
        ),
        sa.Column(
            "cooccurrence_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "session_overlap",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        sa.Column(
            "adamic_adar",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        sa.Column(
            "type_affinity",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        sa.Column(
            "semantic_similarity",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        sa.Column(
            "composite_weight",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        sa.Column("last_computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "space_id",
            "entity_a_id",
            "entity_b_id",
            name="uq_entity_edge_pair",
        ),
        sa.CheckConstraint(
            "entity_a_id < entity_b_id", name="chk_edge_order"
        ),
        schema=SCHEMA,
    )
    _add_mixin_indexes("entity_edges")
    op.create_index(
        "idx_ee_weight",
        "entity_edges",
        ["composite_weight"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_ee_entities",
        "entity_edges",
        ["entity_a_id", "entity_b_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_ee_space", "entity_edges", ["space_id"], schema=SCHEMA
    )

    # 13. triples (self-FK on invalidated_by) ------------------------------
    op.create_table(
        "triples",
        *_space_scoped_cols(),
        sa.Column("source_session", sa.String(length=64), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("predicate", sa.String(length=100), nullable=False),
        sa.Column("object", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(length=500), nullable=True),
        sa.Column("display_zh", sa.Text(), nullable=True),
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "invalidated_by",
            sa.String(length=32),
            sa.ForeignKey(
                f"{SCHEMA}.triples.id",
                use_alter=True,
                name="fk_triples_invalidated_by",
            ),
            nullable=True,
        ),
        sa.Column("invalidation_reason", sa.String(length=50), nullable=True),
        sa.Column(
            "canonical_subject_id",
            sa.String(length=32),
            sa.ForeignKey(f"{SCHEMA}.entity_canonicals.id"),
            nullable=True,
        ),
        sa.Column(
            "canonical_object_id",
            sa.String(length=32),
            sa.ForeignKey(f"{SCHEMA}.entity_canonicals.id"),
            nullable=True,
        ),
        sa.Column(
            "access_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "space_id",
            "source_session",
            "subject",
            "predicate",
            "object",
            name="uq_triples_space_session_spo",
        ),
        schema=SCHEMA,
    )
    _add_mixin_indexes("triples")
    op.create_index(
        "idx_triples_session", "triples", ["source_session"], schema=SCHEMA
    )
    op.create_index(
        "idx_triples_predicate", "triples", ["predicate"], schema=SCHEMA
    )
    op.create_index(
        "idx_triples_subject", "triples", ["subject"], schema=SCHEMA
    )
    op.create_index(
        "idx_triples_object", "triples", ["object"], schema=SCHEMA
    )
    op.create_index(
        "idx_triples_valid",
        "triples",
        ["space_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("invalid_at IS NULL"),
    )

    # 14. communities (self-FK on parent_community_id) ---------------------
    op.create_table(
        "communities",
        *_space_scoped_cols(),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("resolution_level", sa.Integer(), nullable=False),
        sa.Column(
            "size",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("entity_ids", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "top_entities", postgresql.ARRAY(sa.Text()), nullable=True
        ),
        sa.Column(
            "top_predicates", postgresql.ARRAY(sa.Text()), nullable=True
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("description_zh", sa.Text(), nullable=True),
        sa.Column(
            "parent_community_id",
            sa.String(length=32),
            sa.ForeignKey(
                f"{SCHEMA}.communities.id",
                use_alter=True,
                name="fk_communities_parent",
            ),
            nullable=True,
        ),
        sa.Column("generation_batch", sa.String(length=32), nullable=True),
        sa.Column("modularity_score", sa.Float(), nullable=True),
        sa.Column(
            "access_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    _add_mixin_indexes("communities")
    op.create_index(
        "idx_communities_level",
        "communities",
        ["resolution_level"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_communities_parent",
        "communities",
        ["parent_community_id"],
        schema=SCHEMA,
    )

    # 15. community_triples (M2M: community ↔ triple) ----------------------
    op.create_table(
        "community_triples",
        *_space_scoped_cols(),
        sa.Column(
            "community_id",
            sa.String(length=32),
            sa.ForeignKey(f"{SCHEMA}.communities.id"),
            nullable=False,
        ),
        sa.Column(
            "triple_id",
            sa.String(length=32),
            sa.ForeignKey(f"{SCHEMA}.triples.id"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    _add_mixin_indexes("community_triples")
    op.create_index(
        "idx_community_triples_community",
        "community_triples",
        ["community_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_community_triples_triple",
        "community_triples",
        ["triple_id"],
        schema=SCHEMA,
    )

    # 16. community_summaries ----------------------------------------------
    op.create_table(
        "community_summaries",
        *_space_scoped_cols(),
        sa.Column(
            "community_id",
            sa.String(length=32),
            sa.ForeignKey(f"{SCHEMA}.communities.id"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "key_findings", postgresql.ARRAY(sa.Text()), nullable=True
        ),
        sa.Column(
            "representative_triples",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
        sa.Column("evidence_count", sa.Integer(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("llm_model", sa.String(length=100), nullable=True),
        sa.Column("generation_batch", sa.String(length=32), nullable=True),
        schema=SCHEMA,
    )
    _add_mixin_indexes("community_summaries")
    op.create_index(
        "idx_community_summaries_community",
        "community_summaries",
        ["community_id"],
        schema=SCHEMA,
    )

    # 17. audit_logs (Base + TimestampMixin only — no soft delete) ---------
    op.create_table(
        "audit_logs",
        *_timestamp_cols(),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("module", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.String(length=32), nullable=False),
        sa.Column("space_id", sa.String(length=32), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("changes", postgresql.JSONB(), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_audit_entity",
        "audit_logs",
        ["module", "entity_type", "entity_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_audit_user", "audit_logs", ["user_id"], schema=SCHEMA
    )
    op.create_index(
        "idx_audit_created", "audit_logs", ["created_at"], schema=SCHEMA
    )
    op.create_index(
        "idx_audit_space", "audit_logs", ["space_id"], schema=SCHEMA
    )


# ---------------------------------------------------------------------------
# downgrade()
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Drop in reverse FK dependency order.
    for tbl in (
        "audit_logs",
        "community_summaries",
        "community_triples",
        "communities",
        "triples",
        "entity_edges",
        "entity_canonicals",
        "blocks_frozen",
        "memory_block",
        "interest_snapshots",
        "query_journal",
        "search_feedback",
        "profile_scores",
        "knowledge_domains",
        "tags",
        "blocks_archive",
        "blocks",
    ):
        op.drop_table(tbl, schema=SCHEMA)

    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
