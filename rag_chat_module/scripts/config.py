"""
Loads pipeline.yaml + providers.yaml into typed dataclasses.

Design:
  * `config/providers.yaml` lists provider presets (base_url,
    api_key_env, default_model).
  * `config/pipeline.yaml` lists voters; each references a preset
    via `provider_ref` and may override model / temperature /
    base_url / api_key_env.
  * This module resolves each voter into a fully-typed
    ResolvedVoter with the API key already read from the
    environment.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


# ---------------------------------------------------------------------------
# providers.yaml
# ---------------------------------------------------------------------------

@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key_env: Optional[str] = None
    api_key: Optional[str] = None
    default_model: str = "gpt-4o-mini"
    default_max_tokens: int = 4096
    supports_json_mode: bool = True    


# ---------------------------------------------------------------------------
# pipeline.yaml
# ---------------------------------------------------------------------------

@dataclass
class VoterConfig:
    """Raw voter entry as written in pipeline.yaml."""
    name: str
    provider_ref: str
    model: Optional[str] = None
    temperature: float = 0.0
    max_tokens: Optional[int] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    api_key: Optional[str] = None
    json_mode: bool = True          # ← جدید

    def resolve(self, providers, dry_run: bool = False) -> "ResolvedVoter":
        provider = providers.get(self.provider_ref)
        if provider is None:
            raise ValueError(
                f"Voter '{self.name}' references unknown provider_ref "
                f"'{self.provider_ref}'. Known: {sorted(providers)}"
            )

        api_key = (
            self.api_key
            or provider.api_key
            or _read_env(self.api_key_env or provider.api_key_env)
        )

        if not api_key and not dry_run:
            env_name = self.api_key_env or provider.api_key_env or "(none)"
            raise RuntimeError(
                f"Voter '{self.name}' (provider '{self.provider_ref}') "
                f"needs an API key but env var '{env_name}' is empty."
            )

        return ResolvedVoter(
            name=self.name,
            base_url=self.base_url or provider.base_url,
            api_key=api_key,
            model=self.model or provider.default_model,
            temperature=self.temperature,
            max_tokens=self.max_tokens or provider.default_max_tokens,
            json_mode=self.json_mode and provider.supports_json_mode,
        )


@dataclass
class ResolvedVoter:
    name: str
    base_url: str
    api_key: Optional[str]
    model: str
    temperature: float
    max_tokens: int
    json_mode: bool = True          # ← جدید

# ---------------------------------------------------------------------------
# other config blocks
# ---------------------------------------------------------------------------

@dataclass
class ThresholdsConfig:
    top_k: int = 3
    min_similarity: float = 0.05
    min_confidence_margin: float = 0.1
    slice_accept_threshold: int = 8000
    full_file_threshold: int = 20000


@dataclass
class VectorStoreConfig:
    backend: str = "in_memory"
    chroma_collection: str = "llm_variable_resolver"
    chroma_persist_directory: Optional[str] = None


@dataclass
class EmbedderConfig:
    backend: str = "tfidf"
    sentence_transformer_model: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    cache_dir: Optional[str] = None
    device: Optional[str] = None


@dataclass
class NormalizeConfig:
    enabled: bool = True


@dataclass
class PathsConfig:
    checklist: str
    operational: str
    output: str
    files_registry: str
    providers: str = "config/providers.yaml"


@dataclass
class PipelineConfig:
    extraction_voters: List[ResolvedVoter]
    redesign_voters: List[ResolvedVoter] = field(default_factory=list)
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    vector_store: VectorStoreConfig = field(default_factory=VectorStoreConfig)
    embedder: EmbedderConfig = field(default_factory=EmbedderConfig)
    normalize: NormalizeConfig = field(default_factory=NormalizeConfig)
    paths: Optional[PathsConfig] = None
    dry_run: bool = False


# ---------------------------------------------------------------------------
# files.yaml
# ---------------------------------------------------------------------------

@dataclass
class FileEntry:
    path: str


@dataclass
class FilesConfig:
    files: Dict[str, FileEntry]
    sheet_descriptions: Dict[str, Dict[str, str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------

def load_providers_config(path) -> Dict[str, ProviderConfig]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    providers: Dict[str, ProviderConfig] = {}
    for name, spec in (raw.get("providers") or {}).items():
        providers[name] = ProviderConfig(name=name, **spec)
    return providers


def load_pipeline_config(
    pipeline_path,
    providers_path,
    dry_run: bool = False,
) -> PipelineConfig:
    raw = yaml.safe_load(Path(pipeline_path).read_text(encoding="utf-8"))
    providers = load_providers_config(providers_path)

    extraction_voters = [
        VoterConfig(**v).resolve(providers, dry_run=dry_run)
        for v in raw.get("extraction_voters", [])
    ]
    redesign_voters = [
        VoterConfig(**v).resolve(providers, dry_run=dry_run)
        for v in raw.get("redesign_voters", [])
    ]

    return PipelineConfig(
        extraction_voters=extraction_voters,
        redesign_voters=redesign_voters,
        thresholds=ThresholdsConfig(**raw.get("thresholds", {})),
        vector_store=VectorStoreConfig(**raw.get("vector_store", {})),
        embedder=EmbedderConfig(**raw.get("embedder", {})),
        normalize=NormalizeConfig(**raw.get("normalize", {})),
        paths=PathsConfig(**raw["paths"]) if raw.get("paths") else None,
        dry_run=dry_run or bool(raw.get("dry_run", False)),
    )


def load_files_config(path) -> FilesConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    files = {key: FileEntry(**spec) for key, spec in raw.get("files", {}).items()}
    return FilesConfig(
        files=files,
        sheet_descriptions=raw.get("sheet_descriptions", {}) or {},
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read_env(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    value = os.environ.get(name)
    return value or None