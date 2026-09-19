"""
Pluggable vector similarity search.

InMemoryVectorStore is a small numpy-based default - plenty for the
number of chunks a single file produces. Swapping in a real vector
database (FAISS, Chroma, Pinecone, ...) later only requires
implementing the same VectorStore interface.
"""

from abc import ABC, abstractmethod
from typing import List, Tuple

import numpy as np

from .chunk_models import Chunk


class VectorStore(ABC):
    @abstractmethod
    def add(self, chunk: Chunk, vector: np.ndarray) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(self, query_vector: np.ndarray, top_k: int) -> List[Tuple[Chunk, float]]:
        raise NotImplementedError


class InMemoryVectorStore(VectorStore):
    def __init__(self):
        self._chunks: List[Chunk] = []
        self._vectors: List[np.ndarray] = []

    def add(self, chunk: Chunk, vector: np.ndarray) -> None:
        self._chunks.append(chunk)
        self._vectors.append(vector)

    def search(self, query_vector: np.ndarray, top_k: int) -> List[Tuple[Chunk, float]]:
        if not self._vectors:
            return []
        scored = [(c, self._cosine_similarity(query_vector, v)) for c, v in zip(self._chunks, self._vectors)]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1e-9
        return float(np.dot(a, b) / denom)
