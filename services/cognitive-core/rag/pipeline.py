import json
import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from llm.client import get_llm
from rag import tabular_store
from rag.retriever import get_retriever

logger = logging.getLogger(__name__)

HISTORY_WINDOW = 10  # messages of chat history kept, mirrors the old memory k=10

SYSTEM_PROMPT = """You are Maya, a helpful nutrition assistant for Mandala Foods Nepal.
Answer questions only about Mandala Foods products, their nutritional content,
ingredients, and benefits. If a question is outside this scope, politely redirect
the user. You support both English and Nepali. If the user writes in Nepali or
code-switches between Nepali and English, respond in the same language they used.
Always base your answers on the retrieved product information provided to you.
Never make up nutritional claims not present in the context."""

# Deliberately NOT using native tool/function-calling here: the LLM backend
# behind LLM_PROVIDER=claude-code doesn't support an OpenAI-style `tools`
# array (it maps unrelated things onto it), so sending one breaks the system
# prompt entirely. Instead, structured-data lookup is done as a second plain
# completion that just asks the model to emit SQL or "NONE" as text, which
# works through any chat-completions-shaped backend.
SQL_ROUTER_PROMPT = """You decide whether a question needs structured spreadsheet data.

Available datasets:
{dataset_list}

If answering the question requires filtering, counting, comparing, or
aggregating rows from one of these datasets, respond with ONLY a single
PostgreSQL SELECT statement -- no explanation, no markdown fences. Query
`document_rows` (columns: dataset_id, sheet_name, row_data jsonb) and/or
`document_metadata` (id, title, schema, row_count). Read a cell with
row_data->>'ColumnName'. Always filter with WHERE dataset_id = '...'.

If no structured data is relevant, respond with exactly: NONE"""

_SQL_START_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_DATASET_ID_RE = re.compile(r"dataset_id\s*=\s*'([^']+)'", re.IGNORECASE)


def _build_chat_history(conversation_history: list[dict] | None) -> list:
    history = []
    for msg in conversation_history or []:
        if msg["role"] == "user":
            history.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            history.append(AIMessage(content=msg["content"]))
    return history[-HISTORY_WINDOW:]


def _extract_sql(text: str) -> str | None:
    text = text.strip()
    if text.upper().startswith("NONE"):
        return None
    # strip accidental markdown fences (```sql ... ``` or ``` ... ```)
    text = text.strip("`").strip()
    if text.lower().startswith("sql"):
        text = text.split("\n", 1)[-1].strip()
    return text if _SQL_START_RE.match(text) else None


def _maybe_query_structured_data(llm, message: str, chat_history: list, sources: list[str]) -> str | None:
    """Plain-text routing step: ask the model for SQL or NONE, run it if
    given, return a text block to fold into the final prompt's context."""
    datasets = tabular_store.list_datasets()
    if not datasets:
        return None

    dataset_list = "\n".join(
        f"- dataset_id='{d['id']}' title='{d['title']}' rows={d['row_count']} "
        f"columns_by_sheet={d['schema']}"
        for d in datasets
    )
    router_messages = [
        SystemMessage(content=SQL_ROUTER_PROMPT.format(dataset_list=dataset_list)),
        *chat_history,
        HumanMessage(content=message),
    ]

    try:
        decision = llm.invoke(router_messages).content
    except Exception as e:
        logger.warning(f"Structured-data router call failed: {e}")
        return None

    sql = _extract_sql(decision)
    if not sql:
        return None

    try:
        rows = tabular_store.run_readonly_query(sql)
    except Exception as e:
        logger.warning(f"Router-generated SQL failed ({sql!r}): {e}")
        return None

    match = _DATASET_ID_RE.search(sql)
    if match:
        for d in datasets:
            if d["id"] == match.group(1):
                label = f"{d['title']} (structured data)"
                if label not in sources:
                    sources.append(label)
                break

    return json.dumps(rows, default=str) if rows else "Query returned no rows."


def _search_documents(message: str, sources: list[str]) -> str | None:
    docs = get_retriever(k=4).invoke(message)
    if not docs:
        return None
    blocks = []
    for doc in docs:
        src = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page")
        label = f"{src}_page_{page}" if page is not None else src
        if label not in sources:
            sources.append(label)
        blocks.append(doc.page_content)
    return "\n\n---\n\n".join(blocks)


async def run_pipeline(
    message: str,
    conversation_history: list[dict] | None = None,
) -> dict:
    """Run the RAG pipeline and return response with sources.

    Two plain (non-tool-calling) completions: one to optionally pull
    structured data via a text-only SQL-or-NONE decision, one for the final
    answer. See the SQL_ROUTER_PROMPT comment for why this avoids native
    function-calling.
    """
    llm = get_llm()
    chat_history = _build_chat_history(conversation_history)
    sources: list[str] = []

    prose_context = _search_documents(message, sources)
    structured_context = _maybe_query_structured_data(llm, message, chat_history, sources)

    context_parts = [c for c in (prose_context, structured_context) if c]
    context = "\n\n---\n\n".join(context_parts) if context_parts else "No relevant information found."

    final_messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        *chat_history,
        HumanMessage(
            content=f"Context from knowledge base:\n{context}\n\nQuestion: {message}\n\nAnswer:"
        ),
    ]
    answer = llm.invoke(final_messages).content

    return {
        "response": answer,
        "sources": sources,
    }
