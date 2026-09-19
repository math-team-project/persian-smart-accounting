"""
Vector store backed by Chroma - a real, production-grade vector
database - instead of the plain in-memory numpy default.

Because this pipeline always computes embeddings itself (via a
swappable Embedder), Chroma's own embedding function is disabled here
(`embedding_function=None`): Chroma is used purely as the similarity
index over vectors we hand it, not as an embedding provider.

Chroma stores vectors and lightweight metadata well, but not a pandas
DataFrame. So the full Chunk object (including its dataframe) is kept
in a local in-memory cache keyed by chunk_id; Chroma only needs to
tell us WHICH chunk_id matched. If cross-process persistence of the
chunk content itself is ever needed (not just the embeddings), the
chunk's table should also be serialized - e.g. to CSV - into Chroma's
metadata or a separate store.
"""

from typing import Dict, List, Optional, Tuple

import chromadb
import numpy as np

from .chunk_models import Chunk
from .vector_store import VectorStore


class ChromaVectorStore(VectorStore):
    def __init__(self, collection_name: str = "llm_variable_resolver", persist_directory: Optional[str] = None):
        self._client = (
            chromadb.PersistentClient(path=persist_directory) if persist_directory else chromadb.Client()
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )
        self._chunk_cache: Dict[str, Chunk] = {}

    def add(self, chunk: Chunk, vector: np.ndarray) -> None:
        self._chunk_cache[chunk.chunk_id] = chunk
        self._collection.add(
            ids=[chunk.chunk_id],
            embeddings=[vector.tolist()],
            metadatas=[{"file_key": chunk.file_key, "sheets": ", ".join(chunk.source_sheet_names)}],
        )

    def search(self, query_vector: np.ndarray, top_k: int) -> List[Tuple[Chunk, float]]:
        if not self._chunk_cache:
            return []

        result = self._collection.query(
            query_embeddings=[query_vector.tolist()],
            n_results=min(top_k, len(self._chunk_cache)),
        )
        chunk_ids = result["ids"][0]
        distances = result["distances"][0]  # cosine distance: 0 = identical
        return [(self._chunk_cache[cid], 1.0 - dist) for cid, dist in zip(chunk_ids, distances)]
