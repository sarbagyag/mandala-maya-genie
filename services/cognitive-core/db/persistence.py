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


def record_ingestion(filename: str, chunk_count: int):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ingested_documents (filename, chunk_count) VALUES (%s, %s)",
                (filename, chunk_count),
            )
