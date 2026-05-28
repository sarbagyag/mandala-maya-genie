-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Conversations table (session metadata)
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL UNIQUE,
    user_id UUID,  -- NULL for anonymous users, populated when auth is added later
    channel TEXT NOT NULL DEFAULT 'web',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Messages table (full conversation log)
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS langchain_pg_collection (
    name varchar NULL,
    cmetadata json NULL,
    uuid uuid NOT NULL,
    CONSTRAINT langchain_pg_collection_pkey PRIMARY KEY (uuid)
);

CREATE TABLE IF NOT EXISTS langchain_pg_embedding (
    collection_id uuid NULL,
    embedding vector NULL,
    document varchar NULL,
    cmetadata json NULL,
    custom_id varchar NULL,
    uuid uuid NOT NULL,
    CONSTRAINT langchain_pg_embedding_pkey PRIMARY KEY (uuid),
    CONSTRAINT langchain_pg_embedding_collection_id_fkey
        FOREIGN KEY (collection_id) REFERENCES langchain_pg_collection(uuid) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversations_session_id ON conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
