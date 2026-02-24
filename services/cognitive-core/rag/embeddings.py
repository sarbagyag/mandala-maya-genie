import os
import requests
from typing import List
from langchain_core.embeddings import Embeddings


class GeminiRESTEmbeddings(Embeddings):
    """Calls the Gemini REST API directly, bypassing the gRPC client.
    Works with free-tier AI Studio keys."""

    def __init__(
        self,
        model: str = "models/gemini-embedding-001",
        api_key: str = None,
    ):
        self.model = model
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        self._url = f"https://generativelanguage.googleapis.com/v1beta/{model}:embedContent"

    def _embed(self, text: str, task_type: str) -> List[float]:
        resp = requests.post(
            self._url,
            params={"key": self.api_key},
            json={
                "model": self.model,
                "content": {"parts": [{"text": text}]},
                "taskType": task_type,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]["values"]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(t, "RETRIEVAL_DOCUMENT") for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed(text, "RETRIEVAL_QUERY")
