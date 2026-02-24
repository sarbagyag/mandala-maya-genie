# Maya Genie — Phase 1

RAG-powered chatbot for Mandala Foods Nepal.

## Architecture

```
WebSocket Client → channel-adapter (Go :8081) → Redis Streams
                                                      ↓
                   orchestrator (Go :8082) ← Redis Streams consumer
                                                      ↓
                   cognitive-core (Python :8083) ← HTTP POST
                                                      ↓
                                              pgvector (Supabase)
```

## Prerequisites

- Docker & Docker Compose
- Supabase project with pgvector enabled
- API keys (Anthropic or OpenAI)

## Setup

1. **Copy environment file:**

```bash
cp .env.example .env
# Edit .env with your actual keys and DATABASE_URL
```

2. **Run database migration:**

Apply `migrations/001_initial.sql` to your Supabase PostgreSQL instance.

3. **Ingest documents:**

```bash
./scripts/ingest.sh /path/to/mandala-catalog.pdf
```

4. **Start all services:**

```bash
docker compose -f infra/docker-compose.yml up --build
```

## Testing

**Health check:**

```bash
curl http://localhost:8083/health
# {"status":"ok"}
```

**Direct chat (bypass WebSocket):**

```bash
curl -X POST http://localhost:8083/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"test","message":"hello","conversation_history":[],"channel":"web","language":"en"}'
```

**WebSocket (full pipeline):**

```bash
wscat -c ws://localhost:8081/ws
# → {"text":"What products does Mandala Foods offer?"}
```

**Ingest new document:**

```bash
curl -X POST http://localhost:8083/admin/ingest \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -F "file=@/path/to/document.pdf"
```

## Services

| Service          | Port | Language | Purpose                        |
|------------------|------|----------|--------------------------------|
| channel-adapter  | 8081 | Go       | WebSocket gateway              |
| orchestrator     | 8082 | Go       | Session management, routing    |
| cognitive-core   | 8083 | Python   | RAG pipeline, LLM interaction  |

## Environment Variables

See `.env.example` for the full list. Key variables:

- `LLM_PROVIDER` — `anthropic`, `openai`, or `gemini`
- `DATABASE_URL` — Supabase PostgreSQL connection string
- `ADMIN_TOKEN` — Bearer token for `/admin/ingest` endpoint
