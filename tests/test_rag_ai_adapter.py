"""تست‌های ``api/services/rag_ai_adapter.py``.

هیچ‌کدام از این تست‌ها به شبکه، GPU یا حتی نصب واقعی ``rag_chat_module``
(``chromadb``/``sentence-transformers``/``openai``) نیاز ندارند: هر سه شیءِ آن
پکیج (``OpenAIClient``، ``SentenceTransformerEmbedder``، ``ChromaVectorStore``)
با کلاس‌های جعلی جایگزین می‌شوند (پارامترهای ``client_cls``/``embedder_cls``/
``store_cls`` که فقط برای تست وجود دارند).
"""
from __future__ import annotations

import sys
import types

import pytest

from api.services import ai_settings as ai_settings_module
from api.services import kb_storage, rag_ai_adapter


class _FakeOpenAIClient:
    def __init__(self, *, model, api_key, base_url, temperature, max_tokens):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens


class _FakeEmbedder:
    def __init__(self, *, model_name, cache_dir, device):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.device = device
        self.resolved_device = device


class _FakeEmbedderThatFallsBackToCPU(_FakeEmbedder):
    def __init__(self, *, model_name, cache_dir, device):
        super().__init__(model_name=model_name, cache_dir=cache_dir, device=device)
        self.resolved_device = "cpu"  # شبیه‌سازی «درخواست cuda شد، اما CPU واقعاً استفاده شد»


class _FakeChromaVectorStore:
    def __init__(self, *, collection_name, persist_directory):
        self.collection_name = collection_name
        self.persist_directory = persist_directory


@pytest.fixture()
def isolated_vector_root(tmp_path, monkeypatch):
    fake_settings = types.SimpleNamespace(
        vector_store_root=tmp_path / "vector_stores",
        embedding_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    monkeypatch.setattr(kb_storage, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(rag_ai_adapter, "get_settings", lambda: fake_settings)
    return tmp_path / "vector_stores"


def test_build_llm_client_uses_resolved_ai_settings(db_session, project, monkeypatch):
    captured = {}

    def _fake_resolve(session, project_id, workshop_slug):
        captured["project_id"] = project_id
        captured["workshop_slug"] = workshop_slug
        return ai_settings_module.AISettings(
            api_key="secret-key",
            api_url="https://example.test/v1",
            model="gpt-test",
            temperature=0.3,
            max_output_tokens=1234,
            request_timeout=30.0,
            max_retries=2,
            retry_backoff_seconds=1.0,
        )

    monkeypatch.setattr(ai_settings_module, "resolve_ai_settings", _fake_resolve)

    client = rag_ai_adapter.build_llm_client(
        db_session, project.id, client_cls=_FakeOpenAIClient
    )

    assert isinstance(client, _FakeOpenAIClient)
    assert client.model == "gpt-test"
    assert client.api_key == "secret-key"
    assert client.base_url == "https://example.test/v1"
    assert client.temperature == 0.3
    assert client.max_tokens == 1234
    assert captured["project_id"] == project.id
    assert captured["workshop_slug"] == rag_ai_adapter.FINANCIAL_CHATBOT_WORKSHOP_SLUG


def test_build_llm_client_defaults_to_the_financial_chatbot_slug(db_session, project, monkeypatch):
    seen_slugs = []

    def _fake_resolve(session, project_id, workshop_slug):
        seen_slugs.append(workshop_slug)
        return ai_settings_module.default_ai_settings()

    monkeypatch.setattr(ai_settings_module, "resolve_ai_settings", _fake_resolve)
    rag_ai_adapter.build_llm_client(db_session, project.id, client_cls=_FakeOpenAIClient)

    assert seen_slugs == ["financial_chatbot"]


def test_build_embedder_uses_configured_model_and_cache_dir(isolated_vector_root):
    embedder, device = rag_ai_adapter.build_embedder(
        device="cpu", embedder_cls=_FakeEmbedder
    )

    assert isinstance(embedder, _FakeEmbedder)
    assert embedder.model_name == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert embedder.cache_dir == str(isolated_vector_root / ".cache" / "models")
    assert device == "cpu"


def test_build_embedder_honours_explicit_model_name_override(isolated_vector_root):
    embedder, _ = rag_ai_adapter.build_embedder(
        model_name="custom/model", device="cpu", embedder_cls=_FakeEmbedder
    )
    assert embedder.model_name == "custom/model"


def test_build_embedder_reports_actual_device_when_it_differs_from_the_request(
    isolated_vector_root,
):
    _, device = rag_ai_adapter.build_embedder(
        device="cuda", embedder_cls=_FakeEmbedderThatFallsBackToCPU
    )
    assert device == "cpu"


def test_resolve_embedding_device_returns_cpu_when_cuda_is_unavailable(monkeypatch):
    monkeypatch.setattr(rag_ai_adapter, "_torch_cuda_available", lambda: False)
    assert rag_ai_adapter.resolve_embedding_device() == "cpu"


def test_resolve_embedding_device_returns_cuda_when_available(monkeypatch):
    monkeypatch.setattr(rag_ai_adapter, "_torch_cuda_available", lambda: True)
    assert rag_ai_adapter.resolve_embedding_device() == "cuda"


def test_torch_cuda_available_falls_back_to_cpu_on_a_cpu_only_host(monkeypatch):
    """روی میزبان بدون GPU (``torch.cuda.is_available()`` = ``False``) دستگاه CPU است.

    این تست عمداً یک ماژول ``torch`` جعلی را در ``sys.modules`` می‌نشاند تا خودِ
    منطق ``_torch_cuda_available`` (نه یک جایگزین آن) اجرا شود -- همان مسیری که
    در استقرار واقعیِ بدون CUDA طی می‌شود. با این کار، «تشخیص CPU» بدون نیاز به
    نصب/بارگذاری torch واقعی هم پوشش داده می‌شود و رشته‌ی نهایی ``"cpu"`` است.
    """
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False)
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    assert rag_ai_adapter._torch_cuda_available() is False
    assert rag_ai_adapter.resolve_embedding_device() == "cpu"


def test_build_embedder_runs_on_cpu_when_cuda_is_unavailable(isolated_vector_root, monkeypatch):
    """کل زنجیره‌ی ``build_embedder`` روی یک میزبان بدون CUDA: دستگاه ``"cpu"`` است.

    این مهم‌ترین تضمین «محیط CPU» است: بدون پاس‌دادن ``device`` صریح، خودِ
    ``build_embedder`` باید با تشخیص واقعی (که این‌جا CUDA=False است) دستگاه را
    ``"cpu"`` انتخاب کند و همان را در خروجی (که در ``knowledge_bases.embedding_device``
    و ``manifest.json`` ذخیره می‌شود) برگرداند.
    """
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False)
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    embedder, device = rag_ai_adapter.build_embedder(embedder_cls=_FakeEmbedder)

    assert device == "cpu"
    assert embedder.device == "cpu"


def test_build_vector_store_factory_builds_a_deterministic_collection_name_and_path(
    isolated_vector_root,
):
    factory = rag_ai_adapter.build_vector_store_factory(
        user_id=1, project_id=2, kb_id="kb-123", store_cls=_FakeChromaVectorStore
    )
    store = factory()

    assert isinstance(store, _FakeChromaVectorStore)
    assert store.collection_name == rag_ai_adapter.chroma_collection_name_for("kb-123")
    assert store.persist_directory == str(isolated_vector_root / "1" / "2" / "kb-123")


def test_build_vector_store_factory_returns_a_fresh_store_on_every_call(isolated_vector_root):
    factory = rag_ai_adapter.build_vector_store_factory(
        user_id=1, project_id=2, kb_id="kb-1", store_cls=_FakeChromaVectorStore
    )
    assert factory() is not factory()


def test_different_kb_ids_never_share_a_collection_name_or_directory(isolated_vector_root):
    factory_a = rag_ai_adapter.build_vector_store_factory(
        user_id=1, project_id=2, kb_id="kb-a", store_cls=_FakeChromaVectorStore
    )
    factory_b = rag_ai_adapter.build_vector_store_factory(
        user_id=1, project_id=2, kb_id="kb-b", store_cls=_FakeChromaVectorStore
    )
    store_a, store_b = factory_a(), factory_b()

    assert store_a.collection_name != store_b.collection_name
    assert store_a.persist_directory != store_b.persist_directory
