"""
Pluggable embedding backend.

The default implementation (LocalTfidfEmbedder) works fully offline,
so the retrieval pipeline can be built and tested without any network
access. In production this should be swapped for a real embedding
model by implementing the same Embedder interface.

SentenceTransformerEmbedder supports CPU, CUDA, and Apple MPS. When
an unavailable device is requested, it emits a warning and silently
falls back to CPU instead of crashing, so a run always succeeds.
"""

import logging
import warnings
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Device resolution
# ---------------------------------------------------------------------------

def _torch_availability() -> dict:
    """Return which compute backends are usable in the current process.

    Imports torch lazily: if it is missing, everything is False and
    callers fall back to CPU.
    """
    info = {"torch": False, "cuda": False, "mps": False, "cuda_count": 0}
    try:
        import torch
    except ImportError:
        return info

    info["torch"] = True
    info["cuda"] = bool(torch.cuda.is_available())
    if info["cuda"]:
        try:
            info["cuda_count"] = torch.cuda.device_count()
        except Exception:
            info["cuda_count"] = 0
    try:
        info["mps"] = bool(
            hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
        )
    except Exception:
        info["mps"] = False
    return info


def resolve_device(requested: str | None) -> str:
    """Turn a user-requested device into one the machine can actually use.

    Accepted values:
      - None / "auto"  : pick cuda > mps > cpu (in that order)
      - "cuda"         : use CUDA if available; else warn + fall back to CPU
      - "cuda:N"       : use that specific GPU index if available; else CPU
      - "mps"          : use Apple Metal if available; else warn + CPU
      - "cpu"          : always accepted
      - anything else  : passed through unchanged (sentence-transformers
                         will raise if it is invalid)
    """
    requested_raw = (requested or "auto").strip().lower()
    avail = _torch_availability()

    if requested_raw in ("auto", ""):
        if avail["cuda"]:
            return "cuda"
        if avail["mps"]:
            return "mps"
        return "cpu"

    if requested_raw.startswith("cuda"):
        if not avail["torch"]:
            warnings.warn(
                "device='cuda' requested but torch is not installed; "
                "falling back to CPU.",
                RuntimeWarning, stacklevel=2,
            )
            return "cpu"
        if not avail["cuda"]:
            warnings.warn(
                "device='cuda' requested but CUDA is not available "
                "(no GPU, or torch was installed without CUDA support); "
                "falling back to CPU.",
                RuntimeWarning, stacklevel=2,
            )
            return "cpu"
        # Honour cuda:N only if that index exists; otherwise fall back.
        if ":" in requested_raw:
            try:
                idx = int(requested_raw.split(":", 1)[1])
            except ValueError:
                idx = 0
            if idx >= max(avail["cuda_count"], 1):
                warnings.warn(
                    f"device='{requested_raw}' requested but only "
                    f"{avail['cuda_count']} CUDA device(s) present; using cuda:0.",
                    RuntimeWarning, stacklevel=2,
                )
                return "cuda:0"
        return requested_raw

    if requested_raw == "mps":
        if not avail["mps"]:
            warnings.warn(
                "device='mps' requested but Apple Metal is not available; "
                "falling back to CPU.",
                RuntimeWarning, stacklevel=2,
            )
            return "cpu"
        return "mps"

    if requested_raw == "cpu":
        return "cpu"

    # Unknown string - let sentence-transformers produce its own message.
    return requested_raw


def describe_device(device: str) -> str:
    """Human-readable summary for logging."""
    info = _torch_availability()
    if device.startswith("cuda"):
        return (f"device={device} (CUDA, "
                f"{info['cuda_count']} device(s) visible)")
    if device == "mps":
        return f"device={device} (Apple Metal)"
    if device == "cpu":
        suffix = ""
        if not info["torch"]:
            suffix = " [torch not installed]"
        elif not info["cuda"] and not info["mps"]:
            suffix = " [no hardware accelerator available]"
        return f"device=cpu{suffix}"
    return f"device={device}"


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------

class Embedder(ABC):
    @abstractmethod
    def fit(self, texts: List[str]) -> None:
        """Prepares the embedder using the full corpus of chunk texts."""
        raise NotImplementedError

    @abstractmethod
    def embed(self, text: str) -> np.ndarray:
        raise NotImplementedError

    @property
    def resolved_device(self) -> str | None:
        """The device this embedder actually runs on, if applicable."""
        return None


# ---------------------------------------------------------------------------
# Offline default
# ---------------------------------------------------------------------------

class LocalTfidfEmbedder(Embedder):
    """Offline default: TF-IDF vectors compared with cosine similarity.

    Good enough to match Persian financial labels by shared
    vocabulary, and needs no network access - useful as a default and
    for tests. Swap for a real embedding-model client in production.
    """

    def __init__(self):
        self._vectorizer = TfidfVectorizer()
        self._fitted = False

    def fit(self, texts: List[str]) -> None:
        self._vectorizer.fit(texts)
        self._fitted = True

    def embed(self, text: str) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("Embedder must be fit() on the corpus before embed().")
        return self._vectorizer.transform([text]).toarray()[0]


# ---------------------------------------------------------------------------
# Neural encoder
# ---------------------------------------------------------------------------

class SentenceTransformerEmbedder(Embedder):
    """Real semantic embeddings via a pretrained sentence-transformer model.

    `device` accepts "auto" (default), "cpu", "cuda", "cuda:N", or "mps".
    If an unavailable backend is requested, the embedder logs a warning
    and falls back to CPU instead of raising.
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        cache_dir: str | None = None,
        device: str | None = None,
    ):
        from sentence_transformers import SentenceTransformer  # deferred heavy import

        if cache_dir:
            cache_dir = str(Path(cache_dir).expanduser().resolve())
            Path(cache_dir).mkdir(parents=True, exist_ok=True)

        self._device = resolve_device(device)
        logger.info("Loading embedding model %s on %s",
                    model_name, describe_device(self._device))

        self._model = SentenceTransformer(
            model_name,
            cache_folder=cache_dir,
            device=self._device,
        )

    @property
    def resolved_device(self) -> str:
        return self._device

    def fit(self, texts: List[str]) -> None:
        pass  # pretrained encoder: no corpus-specific fitting needed

    def embed(self, text: str) -> np.ndarray:
        return self._model.encode(text, normalize_embeddings=True)