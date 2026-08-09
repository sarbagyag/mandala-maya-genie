"""Structured (csv/xlsx) ingestion and safe query access.

Tabular files are not chunked into prose-style Documents and embedded --
splitting a table by character count separates cells from their column
headers, and vector similarity can't filter/sort/aggregate anyway. Instead
each row is stored as JSONB in `document_rows`, keyed by a `dataset_id`,
alongside a `document_metadata` registry that records the column schema per
sheet (see migrations/002_tabular_kb.sql).

At query time an LLM tool (rag/pipeline.py) reads the schema first, then
writes a constrained SQL SELECT against these two tables -- this is what
lets the model answer "which products have under 5g sugar" or "compare X
and Y" correctly, which plain retrieval cannot do.
"""

import json
import logging
import os
import re
from typing import Any

import pandas as pd
import psycopg2
import psycopg2.extras
import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

ALLOWED_TABLES = {"document_rows", "document_metadata"}
TABULAR_EXTENSIONS = (".csv", ".xlsx", ".xls")
DEFAULT_ROW_LIMIT = 200
QUERY_TIMEOUT_MS = 5000


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "dataset"


def dataset_id_for(file_path: str) -> str:
    base = os.path.splitext(os.path.basename(file_path))[0]
    return _slugify(base)


def _connect():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is required")
    return psycopg2.connect(url)


def _read_sheets(file_path: str) -> dict[str, pd.DataFrame]:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        return {"Sheet1": pd.read_csv(file_path, dtype=str, keep_default_na=False)}
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(file_path, sheet_name=None, dtype=str, keep_default_na=False)
    raise ValueError(f"Unsupported tabular file type: {ext}. Use .csv, .xlsx or .xls")


def ingest_tabular(file_path: str) -> dict[str, Any]:
    """Load a csv/xlsx file into document_metadata + document_rows.

    Replaces any existing rows for the same dataset_id (re-ingestion of an
    updated file), matching upsert semantics.

    Returns {dataset_id, title, row_count, schema}.
    """
    dataset_id = dataset_id_for(file_path)
    title = os.path.basename(file_path)
    sheets = _read_sheets(file_path)

    schema: dict[str, list[str]] = {}
    rows: list[tuple[str, str, str]] = []
    for sheet_name, df in sheets.items():
        df = df.dropna(how="all")
        schema[sheet_name] = list(df.columns)
        for _, row in df.iterrows():
            row_data = {col: val for col, val in row.items() if str(val).strip()}
            if not row_data:
                continue
            rows.append((dataset_id, sheet_name, json.dumps(row_data)))

    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO document_metadata (id, title, schema, row_count)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                    SET title = EXCLUDED.title,
                        schema = EXCLUDED.schema,
                        row_count = EXCLUDED.row_count,
                        updated_at = NOW()
                """,
                (dataset_id, title, json.dumps(schema), len(rows)),
            )
            cur.execute("DELETE FROM document_rows WHERE dataset_id = %s", (dataset_id,))
            if rows:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO document_rows (dataset_id, sheet_name, row_data) VALUES %s",
                    rows,
                    template="(%s, %s, %s::jsonb)",
                )
    finally:
        conn.close()

    logger.info(f"Ingested {len(rows)} rows from {title} into dataset '{dataset_id}'")
    return {"dataset_id": dataset_id, "title": title, "row_count": len(rows), "schema": schema}


def list_datasets() -> list[dict[str, Any]]:
    """Return id/title/schema/row_count for every ingested tabular dataset.

    This is what the list_tabular_datasets LLM tool exposes, so the model
    always has real column names instead of guessing them.
    """
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, title, schema, row_count FROM document_metadata ORDER BY title")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _validate_select_only(sql: str) -> exp.Select:
    """Reject anything but a single read-only SELECT over allowlisted tables."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except Exception as e:
        raise ValueError(f"Could not parse SQL: {e}")

    if len(statements) != 1:
        raise ValueError("Exactly one SQL statement is allowed")

    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        raise ValueError("Only SELECT statements are allowed")

    tables = {t.name.lower() for t in stmt.find_all(exp.Table)}
    disallowed = tables - ALLOWED_TABLES
    if disallowed:
        raise ValueError(
            f"Query references disallowed table(s): {sorted(disallowed)}. "
            f"Only {sorted(ALLOWED_TABLES)} are queryable."
        )
    return stmt


def run_readonly_query(sql: str, row_limit: int = DEFAULT_ROW_LIMIT) -> list[dict[str, Any]]:
    """Validate and execute an LLM-generated SELECT against the tabular KB tables."""
    stmt = _validate_select_only(sql)
    if stmt.args.get("limit") is None:
        stmt = stmt.limit(row_limit)
    bounded_sql = stmt.sql(dialect="postgres")

    conn = _connect()
    try:
        conn.set_session(readonly=True)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SET LOCAL statement_timeout = '{QUERY_TIMEOUT_MS}ms'")
            cur.execute(bounded_sql)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
