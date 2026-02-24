#!/usr/bin/env bash
set -euo pipefail

# Ingestion wrapper script
# Usage: ./scripts/ingest.sh /path/to/document.pdf
#
# Runs locally if Python deps are available, otherwise uses Docker.

FILE="${1:?Usage: $0 <file_path>}"

if [ ! -f "$FILE" ]; then
    echo "Error: File not found: $FILE"
    exit 1
fi

# Check if .env exists and source it
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    source "$ROOT_DIR/.env"
    set +a
fi

# Try running locally first
if command -v python3 &>/dev/null && python3 -c "import langchain" 2>/dev/null; then
    echo "Running ingestion locally..."
    cd "$ROOT_DIR/services/cognitive-core"
    python3 -m rag.ingestion --file "$FILE"
else
    echo "Running ingestion via Docker..."
    ABS_FILE="$(cd "$(dirname "$FILE")" && pwd)/$(basename "$FILE")"
    docker run --rm \
        --env-file "$ROOT_DIR/.env" \
        -v "$ABS_FILE:/data/$(basename "$FILE")" \
        --network maya-genie-app_default \
        "$(docker compose -f "$ROOT_DIR/infra/docker-compose.yml" images cognitive-core -q 2>/dev/null || echo maya-genie-app-cognitive-core)" \
        python -m rag.ingestion --file "/data/$(basename "$FILE")"
fi
