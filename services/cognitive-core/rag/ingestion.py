"""Ingestion pipeline for loading documents into pgvector.

For .csv/.xlsx/.xls, rows are (primarily) loaded into the queryable
document_rows/document_metadata tables instead -- see rag/tabular_store.py
for why plain chunk-and-embed is the wrong approach for tabular data.

Usage:
    python -m rag.ingestion --file /path/to/catalog.pdf
    python -m rag.ingestion --file /path/to/products.xlsx
"""

import argparse
import os
import logging

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import PGVector
from langchain_core.documents import Document

from rag import tabular_store
from rag.embeddings import GeminiRESTEmbeddings
from db.persistence import begin_ingestion, complete_ingestion, delete_document

logger = logging.getLogger(__name__)

COLLECTION_NAME = "mandala_public_kb"
CHUNK_SIZE = 512
CHUNK_OVERLAP = 64


def _load_prose_documents(file_path: str, ext: str) -> list[Document]:
    if ext == ".pdf":
        loader = PyPDFLoader(file_path)
    elif ext in (".txt", ".text", ".md", ".markdown"):
        loader = TextLoader(file_path)
    else:
        raise ValueError(
            f"Unsupported file type: {ext}. Use .pdf, .txt, .md, .csv, .xlsx or .xls"
        )
    return loader.load()


def _preview_documents(result: dict, document_id: str | None) -> list[Document]:
    """One lightweight pointer Document per sheet, for semantic fallback
    discovery (e.g. "what data do we have about kiwi crush"). The
    authoritative, queryable data lives in document_rows -- this only tells
    the agent that a matching dataset exists and how to query it, so it
    deliberately does not embed row content.

    Tagged with the same document_id as the ingested_documents record so
    db.persistence.delete_document's langchain_pg_embedding cleanup catches
    these too.
    """
    docs = []
    for sheet_name, columns in result["schema"].items():
        content = (
            f"Structured dataset '{result['title']}' (sheet: {sheet_name}) "
            f"with columns: {', '.join(columns)}. "
            f"Use the query_tabular_data tool with dataset_id="
            f"'{result['dataset_id']}' to read it."
        )
        docs.append(
            Document(
                page_content=content,
                metadata={
                    "source": result["title"],
                    "sheet": sheet_name,
                    "dataset_id": result["dataset_id"],
                    "kind": "tabular_preview",
                    "document_id": document_id,
                },
            )
        )
    return docs


def _ingest_tabular(file_path: str, original_filename: str | None) -> int:
    filename = original_filename or os.path.basename(file_path)
    doc_id = None
    try:
        doc_id = begin_ingestion(filename)
    except Exception as e:
        logger.warning(f"Failed to begin ingestion record: {e}")

    try:
        result = tabular_store.ingest_tabular(file_path)
    except Exception:
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        raise

    preview_docs = _preview_documents(result, doc_id)
    embeddings = GeminiRESTEmbeddings()
    PGVector.from_documents(
        documents=preview_docs,
        embedding=embeddings,
        collection_name=COLLECTION_NAME,
        connection_string=os.getenv("DATABASE_URL"),
        pre_delete_collection=False,
    )

    if doc_id:
        try:
            complete_ingestion(doc_id, result["row_count"], dataset_id=result["dataset_id"])
        except Exception as e:
            logger.warning(f"Failed to complete ingestion record: {e}")

    logger.info(
        f"Ingested {result['row_count']} rows from {file_path} into "
        f"dataset '{result['dataset_id']}' (+{len(preview_docs)} preview docs)"
    )
    return result["row_count"]


def ingest_file(file_path: str, original_filename: str | None = None) -> int:
    """Load and upsert a file into the knowledge base.

    .pdf/.txt/.md are chunked and embedded into pgvector. .csv/.xlsx/.xls are
    loaded row-by-row into document_rows (queryable via SQL), with only a
    small schema-pointer Document embedded into pgvector for fuzzy discovery.
    Either way, an ingested_documents record is created so the admin UI can
    list/delete it.

    Returns the number of chunks (prose) or rows (tabular) ingested.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext in tabular_store.TABULAR_EXTENSIONS:
        return _ingest_tabular(file_path, original_filename)

    documents = _load_prose_documents(file_path, ext)
    logger.info(f"Loaded {len(documents)} pages/sections from {file_path}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(documents)
    logger.info(f"Split into {len(chunks)} chunks")

    # Register document in DB first so we can attach its ID to chunk metadata
    doc_id = None
    try:
        doc_id = begin_ingestion(original_filename or os.path.basename(file_path))
        for chunk in chunks:
            chunk.metadata["document_id"] = doc_id
    except Exception as e:
        logger.warning(f"Failed to begin ingestion record: {e}")

    embeddings = GeminiRESTEmbeddings()
    connection_string = os.getenv("DATABASE_URL")

    try:
        PGVector.from_documents(
            documents=chunks,
            embedding=embeddings,
            collection_name=COLLECTION_NAME,
            connection_string=connection_string,
            pre_delete_collection=False,
        )
    except Exception:
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        raise

    logger.info(f"Ingested {len(chunks)} chunks into collection '{COLLECTION_NAME}'")

    if doc_id:
        try:
            complete_ingestion(doc_id, len(chunks))
        except Exception as e:
            logger.warning(f"Failed to complete ingestion record: {e}")

    return len(chunks)


def main():
    parser = argparse.ArgumentParser(description="Ingest documents into the knowledge base")
    parser.add_argument("--file", required=True, help="Path to a PDF, TXT, MD, CSV or XLSX file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL environment variable is required")
    if not os.getenv("GOOGLE_API_KEY"):
        raise RuntimeError("GOOGLE_API_KEY environment variable is required")

    count = ingest_file(args.file)
    print(f"Successfully ingested {count} rows/chunks from {args.file}")


if __name__ == "__main__":
    main()
