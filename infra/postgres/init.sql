-- memvault-os Postgres init
-- 建立必要 extensions（schema 由 alembic baseline migration 處理）
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

CREATE SCHEMA IF NOT EXISTS memvault;
