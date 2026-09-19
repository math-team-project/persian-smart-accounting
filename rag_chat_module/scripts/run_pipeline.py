"""
Runs the M1-M5 pipeline from config files.

All LLM access goes through a single OpenAI-compatible client; the
provider differences (DeepSeek, OpenRouter, Groq, ...) come from
config/providers.yaml.

The sentence-transformer embedder's weights are cached locally under
the path in pipeline.yaml (embedder.cache_dir), so a second run loads
from disk without touching the network.

Usage:
    python scripts/run_pipeline.py
    python scripts/run_pipeline.py --dry-run     # mock LLM, no network
    python scripts/run_pipeline.py --config config/pipeline.yaml
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env before anything reads env vars.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed; rely on the shell environment

from llm_variable_resolver import ResolverPipeline
from llm_variable_resolver.llm import (
    EnsembleExtractor,
    ExtractionPromptBuilder,
    MockLLMClient,
    OpenAIClient,
    RedesignEnsemble,
    RedesignPromptBuilder,
    RedesignVoter,
    Voter,
)
from llm_variable_resolver.normalize_persian import normalize_persian_text
from llm_variable_resolver.size_router import SizeRouter

from config import (
    PipelineConfig,
    ResolvedVoter,
    load_files_config,
    load_pipeline_config,
)


# ---------------------------------------------------------------------------
# HuggingFace cache
# ---------------------------------------------------------------------------

def _apply_hf_cache_env(cache_dir: str) -> None:
    """Point HuggingFace's libraries at our local cache.

    Every HuggingFace tool (transformers, sentence-transformers,
    huggingface_hub) reads these env vars. Setting them BEFORE any
    library import guarantees the download goes to the cache dir we
    want, not the default user cache in ~/.cache/huggingface.
    """
    resolved = str(Path(cache_dir).expanduser().resolve())
    os.makedirs(resolved, exist_ok=True)
    os.environ.setdefault("HF_HOME", resolved)
    os.environ.setdefault("TRANSFORMERS_CACHE", str(Path(resolved) / "transformers"))
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", resolved)


# ---------------------------------------------------------------------------
# LLM client factory
# ---------------------------------------------------------------------------

def _build_client(resolved: ResolvedVoter, dry_run: bool = False):
    if dry_run:
        return MockLLMClient(
            responses=['{"value": null, "confidence": "needs_review"}'],
            name=f"mock:{resolved.name}",
        )
    return OpenAIClient(
        model=resolved.model,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        temperature=resolved.temperature,
        max_tokens=resolved.max_tokens,
        json_mode=resolved.json_mode,
    )

# ---------------------------------------------------------------------------
# embedder + vector store factories
# ---------------------------------------------------------------------------

def _build_embedder(cfg: PipelineConfig):
    from llm_variable_resolver.retrieval import (
        LocalTfidfEmbedder, SentenceTransformerEmbedder,
    )

    if cfg.embedder.backend == "sentence_transformer":
        cache_dir = cfg.embedder.cache_dir
        if cache_dir:
            # Resolve relative to the project root and set HF env vars
            # before importing sentence-transformers (which is imported
            # lazily inside SentenceTransformerEmbedder.__init__).
            cache_dir = str((ROOT / cache_dir).resolve())
            _apply_hf_cache_env(cache_dir)
        return SentenceTransformerEmbedder(
            model_name=cfg.embedder.sentence_transformer_model,
            cache_dir=cache_dir,
            device=cfg.embedder.device,
        )
    return LocalTfidfEmbedder()


def _build_vector_store_factory(cfg: PipelineConfig):
    from llm_variable_resolver.retrieval import InMemoryVectorStore

    if cfg.vector_store.backend == "chroma":
        from llm_variable_resolver.retrieval import ChromaVectorStore
        persist = cfg.vector_store.chroma_persist_directory
        if persist:
            persist = str((ROOT / persist).resolve())
        collection = cfg.vector_store.chroma_collection

        def _factory():
            return ChromaVectorStore(
                collection_name=collection,
                persist_directory=persist,
            )
        return _factory

    return InMemoryVectorStore


# ---------------------------------------------------------------------------
# pipeline builder
# ---------------------------------------------------------------------------

def _normalize_fn(cfg: PipelineConfig):
    return normalize_persian_text if cfg.normalize.enabled else None


def build_pipeline(cfg: PipelineConfig) -> ResolverPipeline:
    from llm_variable_resolver.retrieval import ChunkRetriever

    normalize = _normalize_fn(cfg)

    extractor = EnsembleExtractor([
        Voter(
            name=v.name,
            client=_build_client(v, cfg.dry_run),
            prompt_builder=ExtractionPromptBuilder(normalize_fn=normalize),
        )
        for v in cfg.extraction_voters
    ])

    redesign_ensemble = None
    if cfg.redesign_voters:
        redesign_ensemble = RedesignEnsemble([
            RedesignVoter(
                name=v.name,
                client=_build_client(v, cfg.dry_run),
                prompt_builder=RedesignPromptBuilder(normalize_fn=normalize),
            )
            for v in cfg.redesign_voters
        ])

    # Build the embedder + vector store + retriever once, then share the
    # retriever with the router so its per-file index cache is reused
    # across every variable in a run.
    embedder = _build_embedder(cfg)
    resolved = getattr(embedder, "resolved_device", None)
    if resolved:
        print(f"[pipeline] embedder device: {resolved}")
            
    vector_store_factory = _build_vector_store_factory(cfg)
    chunk_retriever = ChunkRetriever(
        embedder=embedder,
        vector_store_factory=vector_store_factory,
        top_k=cfg.thresholds.top_k,
        min_similarity=cfg.thresholds.min_similarity,
        min_confidence_margin=cfg.thresholds.min_confidence_margin,
    )

    router = SizeRouter(
        chunk_retriever=chunk_retriever,
        slice_accept_threshold=cfg.thresholds.slice_accept_threshold,
        full_file_threshold=cfg.thresholds.full_file_threshold,
    )

    return ResolverPipeline(
        extractor=extractor,
        router=router,
        redesign_ensemble=redesign_ensemble,
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "pipeline.yaml"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a mock LLM client; no API calls are made.",
    )
    args = parser.parse_args()

    pipeline_path = Path(args.config)
    providers_path = pipeline_path.parent / "providers.yaml"

    cfg = load_pipeline_config(
        pipeline_path=pipeline_path,
        providers_path=providers_path,
        dry_run=args.dry_run,
    )

    files_cfg = load_files_config(ROOT / cfg.paths.files_registry)
    file_registry = {
        key: str(ROOT / entry.path)
        for key, entry in files_cfg.files.items()
    }

    pipeline = build_pipeline(cfg)

    print(f"[pipeline] dry_run={cfg.dry_run}")
    print(f"[pipeline] extraction voters:")
    for v in cfg.extraction_voters:
        print(f"    - {v.name} -> {v.model} @ {v.base_url}")
    if cfg.redesign_voters:
        print(f"[pipeline] redesign voters:")
        for v in cfg.redesign_voters:
            print(f"    - {v.name} -> {v.model} @ {v.base_url}")
    print(f"[pipeline] files: {list(file_registry)}")

    updated = pipeline.run_from_paths(
        checklist_path=ROOT / cfg.paths.checklist,
        operational_path=ROOT / cfg.paths.operational,
        file_registry=file_registry,
        sheet_descriptions=files_cfg.sheet_descriptions,
        output_path=ROOT / cfg.paths.output,
    )

    print(f"[pipeline] wrote {len(updated)} entries to {cfg.paths.output}")


if __name__ == "__main__":
    main()