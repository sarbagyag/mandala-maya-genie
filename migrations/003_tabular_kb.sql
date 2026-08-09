-- Structured (csv/xlsx) knowledge: schema registry + row storage.
--
-- Kept separate from the prose pgvector store (langchain_pg_embedding /
-- mandala_public_kb) because vector similarity search cannot filter, sort,
-- join, or aggregate. Instead an LLM tool (see rag/tabular_store.py and
-- rag/pipeline.py) reads the schema below and writes a real SQL SELECT
-- against document_rows.
--
-- One generic rows table (rather than a CREATE TABLE per uploaded file)
-- keeps the queryable surface fixed and small. LLM-generated SQL runs
-- through the existing DATABASE_URL connection; safety is enforced in
-- rag/tabular_store.py (SELECT-only, allowlisted to these two tables,
-- forced LIMIT, statement timeout, read-only transaction).

CREATE TABLE IF NOT EXISTS document_metadata (
    id TEXT PRIMARY KEY,                       -- slug derived from filename, e.g. 'kiwi_crush_product_info'
    title TEXT NOT NULL,
    schema JSONB NOT NULL DEFAULT '{}'::jsonb, -- {sheet_name: [column, ...]}
    row_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_rows (
    id BIGSERIAL PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES document_metadata(id) ON DELETE CASCADE,
    sheet_name TEXT NOT NULL DEFAULT 'Sheet1',
    row_data JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_rows_dataset_id ON document_rows(dataset_id);
CREATE INDEX IF NOT EXISTS idx_document_rows_row_data ON document_rows USING GIN (row_data);

-- Links an ingested_documents record (the admin UI's list/delete surface,
-- see db/persistence.py) to its structured dataset, so deleting a csv/xlsx
-- upload from the admin panel also cleans up document_metadata/document_rows.
-- NULL for prose (.pdf/.txt/.md) uploads.
ALTER TABLE ingested_documents ADD COLUMN IF NOT EXISTS dataset_id TEXT REFERENCES document_metadata(id) ON DELETE SET NULL;
