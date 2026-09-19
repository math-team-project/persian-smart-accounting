"""تست‌های ``api/services/kb_storage.py``: مسیر امن، manifest، حذف idempotent."""
from __future__ import annotations

import types

import pytest

from api.services import kb_storage


@pytest.fixture()
def isolated_root(tmp_path, monkeypatch):
    """``vector_store_root()`` را به یک پوشه‌ی موقت مستقل از ``data/`` واقعی هدایت می‌کند."""
    fake_settings = types.SimpleNamespace(vector_store_root=tmp_path / "vector_stores")
    monkeypatch.setattr(kb_storage, "get_settings", lambda: fake_settings)
    return tmp_path / "vector_stores"


def test_path_for_creates_the_expected_nested_layout(isolated_root):
    directory = kb_storage.path_for(1, 2, "kb-uuid-1")
    assert directory == isolated_root / "1" / "2" / "kb-uuid-1"
    assert directory.is_dir()


def test_relative_path_for_matches_path_for_relative_to_the_root(isolated_root):
    absolute = kb_storage.path_for(1, 2, "kb-uuid-1")
    relative = kb_storage.relative_path_for(1, 2, "kb-uuid-1")
    assert relative == "1/2/kb-uuid-1"
    assert (isolated_root / relative) == absolute


def test_path_for_rejects_unsafe_segments(isolated_root):
    with pytest.raises(kb_storage.UnsafePathSegmentError):
        kb_storage.path_for("../etc", 2, "kb-1")
    with pytest.raises(kb_storage.UnsafePathSegmentError):
        kb_storage.path_for(1, "2/../../3", "kb-1")


def test_delete_kb_directory_removes_only_the_target_directory(isolated_root):
    target = kb_storage.path_for(1, 2, "kb-1")
    sibling = kb_storage.path_for(1, 2, "kb-2")
    (target / "chroma.sqlite3").write_text("x")
    (sibling / "chroma.sqlite3").write_text("y")

    kb_storage.delete_kb_directory(1, 2, "kb-1")

    assert not target.exists()
    assert sibling.exists()


def test_delete_kb_directory_is_idempotent_when_missing(isolated_root):
    # هرگز نباید استثنا بیندازد، حتی اگر پوشه از قبل وجود نداشته باشد.
    kb_storage.delete_kb_directory(1, 2, "kb-does-not-exist")
    kb_storage.delete_kb_directory(1, 2, "kb-does-not-exist")


def test_delete_kb_directory_refuses_path_traversal_identifiers(isolated_root):
    # شناسه‌های مخرب باید بی‌خطر نادیده گرفته شوند -- نه اینکه به بیرون از ریشه اشاره کنند.
    outside_marker = isolated_root.parent / "outside.txt"
    outside_marker.write_text("do-not-touch")

    kb_storage.delete_kb_directory("..", "..", "..")

    assert outside_marker.exists()


def test_write_and_read_manifest_round_trip(isolated_root):
    kb_storage.write_manifest(
        1,
        2,
        "kb-1",
        embedding_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        embedding_device="cpu",
        chroma_collection_name="kb-kb-1",
        created_at="2024-01-01T00:00:00+00:00",
    )
    manifest = kb_storage.read_manifest(1, 2, "kb-1")
    assert manifest == {
        "embedding_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "embedding_device": "cpu",
        "chroma_collection_name": "kb-kb-1",
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def test_read_manifest_returns_none_when_missing(isolated_root):
    assert kb_storage.read_manifest(1, 2, "kb-never-created") is None


def test_models_cache_dir_is_shared_and_created(isolated_root):
    cache_dir = kb_storage.models_cache_dir()
    assert cache_dir.is_dir()
    assert cache_dir == isolated_root / ".cache" / "models"
