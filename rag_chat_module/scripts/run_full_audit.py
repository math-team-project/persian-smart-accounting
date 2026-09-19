"""
End-to-end audit run with a full diagnostic report + knowledge base.

Usage:
    uv run scripts/run_full_audit.py                # uses config/pipeline.yaml
    uv run scripts/run_full_audit.py --dry-run      # mock LLM, no network
    uv run scripts/run_full_audit.py --out output/my_run.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

from llm_variable_resolver import FullRunOrchestrator, SizeRouter
from llm_variable_resolver.knowledge_base import KnowledgeBase
from llm_variable_resolver.llm import (
    EnsembleExtractor, ExtractionPromptBuilder, MockLLMClient,
    OpenAIClient, RedesignEnsemble, RedesignPromptBuilder, RedesignVoter, Voter,
)
from llm_variable_resolver.normalize_persian import normalize_persian_text
from llm_variable_resolver.retrieval import (
    ChromaVectorStore, ChunkRetriever, InMemoryVectorStore,
    LocalTfidfEmbedder, SentenceTransformerEmbedder,
)

from config import load_files_config, load_pipeline_config

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _build_client(voter_cfg, dry_run: bool):
    if dry_run:
        return MockLLMClient(
            responses=['{"value": null, "confidence": "needs_review"}'],
            name=f"mock:{voter_cfg.name}",
        )
    return OpenAIClient(
        model=voter_cfg.model,
        api_key=voter_cfg.api_key,
        base_url=voter_cfg.base_url,
        temperature=voter_cfg.temperature,
    )


def _build_embedder(cfg):
    if cfg.embedder.backend == "sentence_transformer":
        cache_dir = None
        if cfg.embedder.cache_dir:
            cache_dir = str((ROOT / cfg.embedder.cache_dir).resolve())
            os.makedirs(cache_dir, exist_ok=True)
            os.environ.setdefault("HF_HOME", cache_dir)
            os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", cache_dir)

        return SentenceTransformerEmbedder(
            model_name=cfg.embedder.sentence_transformer_model,
            cache_dir=cache_dir,
            device=cfg.embedder.device,
        )
    return LocalTfidfEmbedder()


def _build_vector_store_factory(cfg):
    if cfg.vector_store.backend == "chroma":
        persist = None
        if cfg.vector_store.chroma_persist_directory:
            persist = str(
                (ROOT / cfg.vector_store.chroma_persist_directory).resolve()
            )
        collection = cfg.vector_store.chroma_collection

        def _factory():
            return ChromaVectorStore(
                collection_name=collection,
                persist_directory=persist,
            )
        return _factory

    return InMemoryVectorStore


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Use a mock LLM client; no API calls are made.")
    parser.add_argument("--out", default="output/full_audit_report.json",
                        help="Where to write the full audit report.")
    parser.add_argument("--index-out", default="output/index_report.json",
                        help="Where to write the knowledge-base index report.")
    args = parser.parse_args()

    # --- load config -------------------------------------------------
    cfg = load_pipeline_config(
        pipeline_path=ROOT / "config" / "pipeline.yaml",
        providers_path=ROOT / "config" / "providers.yaml",
        dry_run=args.dry_run,
    )
    files_cfg = load_files_config(ROOT / "config" / "files.yaml")
    files = {k: str(ROOT / v.path) for k, v in files_cfg.files.items()}

    checklist = json.loads(
        (ROOT / "data" / "checklist.json").read_text(encoding="utf-8")
    )
    operational = json.loads(
        (ROOT / "data" / "operational.json").read_text(encoding="utf-8")
    )

    # --- build embedder + vector store factory ONCE ------------------
    embedder = _build_embedder(cfg)
    vs_factory = _build_vector_store_factory(cfg)

    # --- build the knowledge base ------------------------------------
    kb = KnowledgeBase(embedder=embedder, vector_store_factory=vs_factory)
    print(f"\n[KB] indexing {len(files)} file(s) ...")
    index_report = kb.index_files(files, files_cfg.sheet_descriptions)

    index_path = ROOT / args.index_out
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(index_report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[KB] indexed {index_report.total_chunks} chunks "
          f"across {index_report.total_files} files")
    if index_report.errors:
        print("[KB] errors:")
        for e in index_report.errors:
            print(f"     - {e}")
    print(f"[KB] report saved to {index_path.relative_to(ROOT)}")

    # --- build extractor ---------------------------------------------
    extractor = EnsembleExtractor([
        Voter(
            name=v.name,
            client=_build_client(v, cfg.dry_run),
            prompt_builder=ExtractionPromptBuilder(
                normalize_fn=normalize_persian_text,
            ),
        )
        for v in cfg.extraction_voters
    ])

    # --- build redesign ensemble (optional) --------------------------
    redesign = None
    if cfg.redesign_voters:
        redesign = RedesignEnsemble([
            RedesignVoter(
                name=v.name,
                client=_build_client(v, cfg.dry_run),
                prompt_builder=RedesignPromptBuilder(
                    normalize_fn=normalize_persian_text,
                ),
            )
            for v in cfg.redesign_voters
        ])

    # --- build router ------------------------------------------------
    router = SizeRouter(
        chunk_retriever=ChunkRetriever(
            embedder=embedder,
            vector_store_factory=vs_factory,
            top_k=cfg.thresholds.top_k,
            min_similarity=cfg.thresholds.min_similarity,
            min_confidence_margin=cfg.thresholds.min_confidence_margin,
        ),
        slice_accept_threshold=cfg.thresholds.slice_accept_threshold,
        full_file_threshold=cfg.thresholds.full_file_threshold,
    )

    # --- run the full audit ------------------------------------------
    orchestrator = FullRunOrchestrator(
        extractor=extractor,
        router=router,
        redesign_ensemble=redesign,
        knowledge_base=kb,
    )
    report = orchestrator.run(
        checklist=checklist,
        operational=operational,
        file_registry=files,
        sheet_descriptions=files_cfg.sheet_descriptions,
    )

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # --- console summary ---------------------------------------------
    print("\n=== Full run summary ===")
    print(json.dumps(report.summary, ensure_ascii=False, indent=2))
    print(f"\n[saved] {out_path.relative_to(ROOT)}")

    print("\n=== Per-question status ===")
    for q in report.questions:
        print(f"  {q.question_id}: {q.status} "
              f"(tasks={q.tasks_built}, vars={len(q.variables)})")
        for v in q.variables:
            marker = "OK" if v.error is None else "ERR"
            line = (
                f"    - {marker} {v.variable_name}: "
                f"method={v.slice_method}, "
                f"value={v.extracted_value}, "
                f"conf={v.extraction_confidence}"
            )
            if v.error:
                line += f"  error={v.error}"
            print(line)

    if report.summary.get("errors"):
        print("\n=== Errors ===")
        for e in report.summary["errors"]:
            print(f"  - {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())