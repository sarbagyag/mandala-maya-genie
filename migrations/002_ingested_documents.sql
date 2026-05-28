CREATE TABLE IF NOT EXISTS ingested_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);
