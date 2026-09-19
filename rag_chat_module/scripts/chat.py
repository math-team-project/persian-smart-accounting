"""
Interactive chat over the indexed knowledge base.

Usage:
    uv run scripts/chat.py                       # one-shot from arg
    uv run scripts/chat.py -q "موجودی نقد چقدر است؟"
    uv run scripts/chat.py --repl                # interactive loop
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

from llm_variable_resolver import KnowledgeBase, chat_ask
from llm_variable_resolver.llm import OpenAIClient
from llm_variable_resolver.retrieval import (
    ChromaVectorStore, InMemoryVectorStore,
    LocalTfidfEmbedder, SentenceTransformerEmbedder,
)

from config import load_files_config, load_pipeline_config


def _build_kb(cfg, files_cfg):
    if cfg.embedder.backend == "sentence_transformer":
        embedder = SentenceTransformerEmbedder(
            model_name=cfg.embedder.sentence_transformer_model,
            cache_dir=str((ROOT / cfg.embedder.cache_dir).resolve())
                if cfg.embedder.cache_dir else None,
            device=cfg.embedder.device,
        )
    else:
        embedder = LocalTfidfEmbedder()

    if cfg.vector_store.backend == "chroma":
        persist = str((ROOT / cfg.vector_store.chroma_persist_directory).resolve())
        vs_factory = lambda: ChromaVectorStore(
            collection_name=cfg.vector_store.chroma_collection,
            persist_directory=persist,
        )
    else:
        vs_factory = InMemoryVectorStore

    files = {k: str(ROOT / v.path) for k, v in files_cfg.files.items()}
    kb = KnowledgeBase(embedder=embedder, vector_store_factory=vs_factory)
    kb.index_files(files, files_cfg.sheet_descriptions)
    return kb


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-q", "--question")
    p.add_argument("--repl", action="store_true")
    p.add_argument("--top-k", type=int, default=3)
    args = p.parse_args()

    cfg = load_pipeline_config(
        ROOT / "config" / "pipeline.yaml",
        ROOT / "config" / "providers.yaml",
    )
    files_cfg = load_files_config(ROOT / "config" / "files.yaml")
    kb = _build_kb(cfg, files_cfg)

    voter = cfg.extraction_voters[0]
    llm = OpenAIClient(
        model=voter.model, api_key=voter.api_key,
        base_url=voter.base_url, temperature=0.0,
    )

    def _one(q):
        ans = chat_ask(
            question=q, knowledge_base=kb, llm_client=llm,
            sheet_catalog=files_cfg.sheet_descriptions,
            top_k=args.top_k,
        )
        print("\nپاسخ:", ans.answer)
        print("اعتماد:", ans.confidence)
        print("منابع:")
        for s in ans.sources:
            print(f"  - {s.file_key} / {s.sheet_names}  (sim={s.similarity:.3f})")

    if args.question:
        _one(args.question)
        return

    if args.repl:
        print("چت آماده است. برای خروج Ctrl-C بزنید.")
        while True:
            try:
                q = input("\nسؤال شما: ").strip()
                if q:
                    _one(q)
            except (KeyboardInterrupt, EOFError):
                print("\nخداحافظ.")
                break
        return

    p.print_help()


if __name__ == "__main__":
    main()