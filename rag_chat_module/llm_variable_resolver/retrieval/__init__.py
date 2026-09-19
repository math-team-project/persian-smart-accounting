"""
Level-3 RAG fallback: chunking, embedding, and semantic retrieval
for large files where deterministic sheet-name matching is not
practical.
"""

from .chunk_models import Chunk
from .embedder import Embedder, LocalTfidfEmbedder, SentenceTransformerEmbedder
from .retriever import ChunkRetriever
from .segmenter import TableSegmenter
from .vector_store import InMemoryVectorStore, VectorStore
from .metadata_builder import build_metadata_text, collect_text_labels

try:
    from .chroma_vector_store import ChromaVectorStore
except ImportError:  # chromadb not installed -> optional, not fatal
    ChromaVectorStore = None  # type: ignore

__all__ = [
    "Chunk",
    "Embedder",
    "LocalTfidfEmbedder",
    "SentenceTransformerEmbedder",
    "VectorStore",
    "InMemoryVectorStore",
    "ChromaVectorStore",
    "TableSegmenter",
    "ChunkRetriever",
    "build_metadata_text",
    "collect_text_labels",
]