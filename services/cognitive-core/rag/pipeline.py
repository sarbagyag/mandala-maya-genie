import json
import logging
import re

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

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
Never make up nutritional claims not present in the context.

You have two kinds of knowledge available as tools:
- search_documents: unstructured product write-ups (PDFs, docs). Use for
  open-ended or descriptive questions.
- list_tabular_datasets / query_tabular_data: structured spreadsheets
  (nutrition tables, price lists). Use these whenever the question involves
  filtering, comparing, sorting, or aggregating across rows. Always call
  list_tabular_datasets first to see exact column names -- never guess one."""

_DATASET_ID_RE = re.compile(r"dataset_id\s*=\s*'([^']+)'", re.IGNORECASE)


def _make_tools(sources: list[str]):
    """Build the agent's tools. `sources` is mutated in place as tools are
    called, so run_pipeline can read it back after the agent finishes."""
    retriever = get_retriever(k=4)

    @tool
    def search_documents(query: str) -> str:
        """Semantic search over unstructured Mandala Foods product documents (PDFs, write-ups). Use for descriptive or open-ended questions."""
        docs = retriever.invoke(query)
        if not docs:
            return "No relevant documents found."
        blocks = []
        for doc in docs:
            src = doc.metadata.get("source", "unknown")
            page = doc.metadata.get("page")
            label = f"{src}_page_{page}" if page is not None else src
            if label not in sources:
                sources.append(label)
            blocks.append(doc.page_content)
        return "\n\n---\n\n".join(blocks)

    @tool
    def list_tabular_datasets() -> str:
        """List every structured spreadsheet/CSV dataset available, with its dataset_id and exact column names per sheet. Always call this before query_tabular_data."""
        datasets = tabular_store.list_datasets()
        if not datasets:
            return "No structured datasets are available."
        return "\n".join(
            f"- dataset_id='{d['id']}' title='{d['title']}' rows={d['row_count']} "
            f"columns_by_sheet={d['schema']}"
            for d in datasets
        )

    @tool
    def query_tabular_data(sql: str) -> str:
        """Run a read-only SELECT against the structured datasets. Only the
        tables `document_rows` (columns: dataset_id, sheet_name, row_data
        jsonb) and `document_metadata` (id, title, schema, row_count) are
        queryable. Read a cell with row_data->>'ColumnName'. Always filter
        with WHERE dataset_id = '...'. Call list_tabular_datasets first to
        get exact column names -- do not guess them."""
        try:
            rows = tabular_store.run_readonly_query(sql)
        except Exception as e:
            return f"Query failed: {e}. Check column names via list_tabular_datasets and retry."

        match = _DATASET_ID_RE.search(sql)
        if match:
            dataset_id = match.group(1)
            for d in tabular_store.list_datasets():
                if d["id"] == dataset_id:
                    label = f"{d['title']} (structured data)"
                    if label not in sources:
                        sources.append(label)
                    break

        if not rows:
            return "Query returned no rows."
        return json.dumps(rows, default=str)

    return [search_documents, list_tabular_datasets, query_tabular_data]


def _build_chat_history(conversation_history: list[dict] | None) -> list:
    history = []
    for msg in conversation_history or []:
        if msg["role"] == "user":
            history.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            history.append(AIMessage(content=msg["content"]))
    return history[-HISTORY_WINDOW:]


def build_agent(sources: list[str]) -> AgentExecutor:
    """Build a tool-calling agent with access to both the vector retriever
    and the structured-data SQL tools."""
    llm = get_llm()
    tools = _make_tools(sources)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=False)


async def run_pipeline(
    message: str,
    conversation_history: list[dict] | None = None,
) -> dict:
    """Run the RAG pipeline (vector search + structured SQL tools) and return
    response with sources."""
    sources: list[str] = []
    executor = build_agent(sources)
    chat_history = _build_chat_history(conversation_history)

    result = await executor.ainvoke({"input": message, "chat_history": chat_history})

    return {
        "response": result["output"],
        "sources": sources,
    }
