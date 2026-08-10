-- Reverts 003_tabular_kb.sql: the text-to-SQL tabular ingestion feature
-- (xlsx/csv -> document_metadata/document_rows) has been removed. This is a
-- new forward migration rather than an edit to 003 because 003 was already
-- applied against the production database.

ALTER TABLE ingested_documents DROP COLUMN IF EXISTS dataset_id;

DROP TABLE IF EXISTS document_rows;
DROP TABLE IF EXISTS document_metadata;
