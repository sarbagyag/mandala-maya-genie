"""Ingestion pipeline for loading documents into pgvector.

Usage:
    python -m rag.ingestion --file /path/to/catalog.pdf
"""

import argparse
import os
import logging

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import PGVector
from rag.embeddings import GeminiRESTEmbeddings

logger = logging.getLogger(__name__)

COLLECTION_NAME = "mandala_public_kb"
CHUNK_SIZE = 512
CHUNK_OVERLAP = 64


def ingest_file(file_path: str) -> int:
    """Load, chunk, embed and upsert a file into pgvector.

    Returns the number of chunks ingested.
    """
    # Load document
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        loader = PyPDFLoader(file_path)
    elif ext in (".txt", ".text"):
        loader = TextLoader(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}. Use .pdf or .txt")

    documents = loader.load()
    logger.info(f"Loaded {len(documents)} pages/sections from {file_path}")

    # Split into chunks
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(documents)
    logger.info(f"Split into {len(chunks)} chunks")

    # Embed and upsert
    embeddings = GeminiRESTEmbeddings()
    connection_string = os.getenv("DATABASE_URL")

    PGVector.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=COLLECTION_NAME,
        connection_string=connection_string,
        pre_delete_collection=False,
    )

    logger.info(f"Ingested {len(chunks)} chunks into collection '{COLLECTION_NAME}'")
    return len(chunks)


def main():
    parser = argparse.ArgumentParser(description="Ingest documents into pgvector")
    parser.add_argument("--file", required=True, help="Path to PDF or text file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL environment variable is required")
    if not os.getenv("GOOGLE_API_KEY"):
        raise RuntimeError("GOOGLE_API_KEY environment variable is required")

    count = ingest_file(args.file)
    print(f"Successfully ingested {count} chunks from {args.file}")


if __name__ == "__main__":
    main()
