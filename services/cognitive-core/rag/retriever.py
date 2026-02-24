import os
from langchain_community.vectorstores import PGVector
from rag.embeddings import GeminiRESTEmbeddings


def get_retriever(k: int = 4):
    """Create a PGVector retriever for the Mandala Foods knowledge base."""
    connection_string = os.getenv("DATABASE_URL")
    embeddings = GeminiRESTEmbeddings()
    vectorstore = PGVector(
        connection_string=connection_string,
        embedding_function=embeddings,
        collection_name="mandala_public_kb",
    )
    return vectorstore.as_retriever(search_kwargs={"k": k})
