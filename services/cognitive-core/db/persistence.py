import os
import psycopg2
from contextlib import contextmanager


@contextmanager
def _conn():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_conversation(session_id: str, channel: str = "web") -> str:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversations (session_id, channel)
                VALUES (%s, %s)
                ON CONFLICT (session_id) DO UPDATE SET updated_at = NOW()
                RETURNING id
                """,
                (session_id, channel),
            )
            return str(cur.fetchone()[0])


def save_messages(conversation_id: str, user_content: str, assistant_content: str):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO messages (conversation_id, role, content)
                VALUES (%s, 'user', %s), (%s, 'assistant', %s)
                """,
                (conversation_id, user_content, conversation_id, assistant_content),
            )


def begin_ingestion(filename: str) -> str:
    """Insert a pending ingested_documents record and return its id."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ingested_documents (filename, chunk_count) VALUES (%s, 0) RETURNING id",
                (filename,),
            )
            return str(cur.fetchone()[0])


def complete_ingestion(doc_id: str, chunk_count: int, dataset_id: str | None = None):
    """dataset_id links this record to document_metadata for csv/xlsx
    uploads (see rag/tabular_store.py), so delete_document can cascade into
    document_rows too. None for prose (.pdf/.txt/.md) uploads."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ingested_documents SET chunk_count = %s, dataset_id = %s WHERE id = %s",
                (chunk_count, dataset_id, doc_id),
            )


def delete_document(doc_id: str):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM langchain_pg_embedding WHERE cmetadata->>'document_id' = %s",
                (doc_id,),
            )
            cur.execute(
                """
                DELETE FROM document_metadata
                WHERE id = (SELECT dataset_id FROM ingested_documents WHERE id = %s)
                """,
                (doc_id,),
            )  # cascades into document_rows via its FK
            cur.execute("DELETE FROM ingested_documents WHERE id = %s", (doc_id,))


def list_documents():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, filename, chunk_count, ingested_at
                FROM ingested_documents
                ORDER BY ingested_at DESC
                """
            )
            return [
                {
                    "id": str(r[0]),
                    "filename": r[1],
                    "chunk_count": r[2],
                    "ingested_at": r[3].isoformat(),
                }
                for r in cur.fetchall()
            ]
