from pathlib import Path
import sys
import re
from typing import Dict, List, Any, Tuple
import numpy as np
from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer
from signatures import SIGNATURES
from normalize_persian import normalize_persian_text
from tqdm import tqdm
import warnings
warnings.filterwarnings('ignore')  # Suppress all warnings

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
_PATHS_TO_ADD = [
    ROOT_DIR,
    ROOT_DIR / "checklist",
]
for _p in _PATHS_TO_ADD:
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)
_MODEL_CACHE_DIR = ROOT_DIR / "models_cache"
_MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
 
# Hybrid matching configuration
HYBRID_ALPHA = 0.5           # α for fuzzy; (1-α) for semantic
HYBRID_MATCH_THRESHOLD = 0.55   # minimum hybrid score to count an item as "matched"
# EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-small"
# EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
# EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-base"
EMBEDDING_MODEL_NAME = "PartAI/Tooka-SBERT-V2-Small"
_MODEL_LOCAL_DIR = _MODEL_CACHE_DIR / EMBEDDING_MODEL_NAME.replace("/", "__")

_embedding_model = None         # lazy singleton
_SIG_EMB_CACHE: Dict[Tuple[str, str], Tuple[List[str], np.ndarray]] = {}


def _get_model() -> SentenceTransformer:
    """
    Lazy singleton for the multilingual embedding model.
    Loads from a local cache directory if it exists, otherwise downloads
    from HuggingFace and saves a copy locally for next runs.
    """
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model

    # ---- 1. If local copy exists, load from disk ----
    if _MODEL_LOCAL_DIR.exists() and any(_MODEL_LOCAL_DIR.iterdir()):
        try:
            with tqdm(total=1, desc="loading model from cache", ncols=100) as pbar:
                _embedding_model = SentenceTransformer(str(_MODEL_LOCAL_DIR))
                pbar.update(1)
            print(f"[model] loaded from: {_MODEL_LOCAL_DIR}")
            return _embedding_model
        except Exception as e:
            print(f"[model] cache broken ({e}); re-downloading...")
                    
    # ---- 2. Otherwise download from HuggingFace ----
    print(f"[model] downloading: {EMBEDDING_MODEL_NAME}")
    with tqdm(total=1, desc="downloading model", ncols=100) as pbar:
        model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        pbar.update(1)

    # ---- 3. Save local copy for next runs ----
    try:
        model.save(str(_MODEL_LOCAL_DIR))
        print(f"[model] saved to local cache: {_MODEL_LOCAL_DIR}")
    except Exception as e:
        print(f"[model] could not save local copy: {e}")

    _embedding_model = model
    return _embedding_model

def _get_sig_embeddings(sig_key: str, list_key: str, items: List[str]) -> Tuple[List[str], np.ndarray]:
    """
    Cache embeddings for a signature list (e.g. ('ع', 'core_rows')).
    Returns (items, normalized_embeddings_matrix).
    """
    cache_key = (sig_key, list_key)
    if cache_key not in _SIG_EMB_CACHE:
        model = _get_model()
        if items:
            embs = model.encode(items, normalize_embeddings=True, show_progress_bar=True)
            embs = np.asarray(embs, dtype=np.float32)
        else:
            embs = np.zeros((0, model.get_embedding_dimension()), dtype=np.float32)
        _SIG_EMB_CACHE[cache_key] = (items, embs)
    return _SIG_EMB_CACHE[cache_key]


def _hybrid_best_score(
    target_text: str,
    target_emb: np.ndarray,
    candidates_text: List[str],
    candidates_emb: np.ndarray,
    alpha: float = HYBRID_ALPHA,
) -> float:
    """
    Compute the best hybrid score of one signature item against all sheet items.

    fuzzy  = max rapidfuzz.token_set_ratio(target, candidate) / 100
    seman  = max cosine(target_emb, candidate_emb)      # already in [0,1] because normalized

    Returns  alpha * fuzzy  +  (1 - alpha) * semantic
    """
    if not candidates_text or candidates_emb.shape[0] == 0:
        return 0.0

    # --- Fuzzy component ---
    best_fuzzy = 0.0
    for c in candidates_text:
        s = fuzz.token_set_ratio(target_text, c) / 100.0
        if s > best_fuzzy:
            best_fuzzy = s
            if best_fuzzy >= 0.999:  # early exit
                break

    # --- Semantic component ---
    sims = candidates_emb @ target_emb           # cosine, both normalized
    best_semantic = float(np.max(sims))
    best_semantic = max(0.0, min(1.0, best_semantic))

    return alpha * best_fuzzy + (1.0 - alpha) * best_semantic


def _score_signature_list(
    sig_key: str,
    list_key: str,
    sig_items: List[str],
    sheet_items_text: List[str],
    sheet_items_emb: np.ndarray,
    alpha: float = HYBRID_ALPHA,
    match_threshold: float = HYBRID_MATCH_THRESHOLD,
) -> float:
    """
    For a signature list (core_rows / core_cols / keywords / ...), compute
    the fraction of items whose best hybrid match exceeds `match_threshold`.
    """
    if not sig_items:
        return 0.0

    # Embed signature items (cached across calls)
    _, sig_embs = _get_sig_embeddings(sig_key, list_key, sig_items)

    matched = 0
    for i, item in enumerate(sig_items):
        score = _hybrid_best_score(
            target_text=item,
            target_emb=sig_embs[i],
            candidates_text=sheet_items_text,
            candidates_emb=sheet_items_emb,
            alpha=alpha,
        )
        if score >= match_threshold:
            matched += 1

    return matched / len(sig_items)

    
def classify_sheets_by_signature(sheets_data: List[Dict[str, Any]], similarity_threshold: float = 0.55) -> List[Dict[str, Any]]:
    """
    Task 2: Classifies Excel sheets based on dynamic header/content matching.
    Ignores sheet names and dynamically incorporates all signature elements (title, core/common rows/cols, keywords).
    """
    classified_results = []
    normalized_signatures = _get_normalized_signatures()

    for sheet in tqdm(sheets_data, desc="classifying sheets", ncols=100):
        raw_rows = sheet.get("row_labels", []) + sheet.get("title_blocks", [])
        raw_cols = sheet.get("col_labels", [])

        # Normalize + deduplicate sheet content
        sheet_rows_text = list(dict.fromkeys(
            normalize_persian_text(str(r)) for r in raw_rows if r
        ))
        sheet_cols_text = list(dict.fromkeys(
            normalize_persian_text(str(c)) for c in raw_cols if c
        ))
        all_sheet_text = " ".join(sheet_rows_text + sheet_cols_text)

        # Encode sheet content once (batched)
        model = _get_model()
        emb_dim = model.get_embedding_dimension()

        if sheet_rows_text:
            sheet_rows_emb = np.asarray(
                model.encode(sheet_rows_text, normalize_embeddings=True, show_progress_bar=True),
                dtype=np.float32,
            )
        else:
            sheet_rows_emb = np.zeros((0, emb_dim), dtype=np.float32)

        if sheet_cols_text:
            sheet_cols_emb = np.asarray(
                model.encode(sheet_cols_text, normalize_embeddings=True, show_progress_bar=True),
                dtype=np.float32,
            )
        else:
            sheet_cols_emb = np.zeros((0, emb_dim), dtype=np.float32)

        best_match_key = "unknown"
        best_score = 0.0

        scores: Dict[str, float] = {}
        for sig_key, sig_data in normalized_signatures.items():
            score = _calculate_dynamic_signature_score(
                sheet_rows_text=sheet_rows_text,
                sheet_rows_emb=sheet_rows_emb,
                sheet_cols_text=sheet_cols_text,
                sheet_cols_emb=sheet_cols_emb,
                all_sheet_text=all_sheet_text,
                signature=sig_data,
                sig_key=sig_key,
                alpha=HYBRID_ALPHA,
            )
        # ---- Margin check: best must be clearly ahead of second ----
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        best_match_key, best_score = ranked[0] if ranked else ("unknown", 0.0)
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = best_score - second_score

        MIN_MARGIN = 0.1

        if best_score < similarity_threshold or margin < MIN_MARGIN:
            final_type = "unknown"
        else:
            final_type = best_match_key

        classified_results.append({
            "original_sheet_name": sheet.get("sheet_name", ""),
            "predicted_type": final_type,
            "confidence_score": round(best_score, 4),
            "second_best": ranked[1][0] if len(ranked) > 1 else None,
            "second_score": round(second_score, 4),
            "margin": round(margin, 4),
            "signature_details": SIGNATURES.get(final_type, {}).get("comment", "شناسایی نشده")
        })
    return classified_results

def _calculate_dynamic_signature_score(
    sheet_rows_text: List[str],
    sheet_rows_emb: np.ndarray,
    sheet_cols_text: List[str],
    sheet_cols_emb: np.ndarray,
    all_sheet_text: str,
    signature: dict,
    sig_key: str,
    alpha: float = HYBRID_ALPHA,
) -> float:
    """
    Weighted hybrid score across all signature components.
    Base weights (same as before):
        core_rows   0.40
        title       0.20
        core_cols   0.20
        keywords    0.10
        common_cols 0.05
        common_rows 0.05
    """
    # ---- Reject signatures that are structurally too weak ----
    MIN_ACTIVE_WEIGHT = 0.20
    
    # اگر نه core_rows دارد نه title، اصلاً ارزش امتیاز دادن ندارد
    if not signature.get("core_rows") and not signature.get("title"):
        return 0.0
        
    base_weights = {
        "core_rows": 0.25,
        "title": 0.30,
        "core_cols": 0.20,
        "keywords": 0.05,
        "common_cols": 0.05,
        "common_rows": 0.05
        }

    component_scores: Dict[str, float] = {}
    active_weights_sum = 0.0

    # ---- 1. Title (exact substring in combined text) ----
    if signature.get("title"):
        title_text = signature["title"]
        component_scores["title"] = 1.0 if title_text in all_sheet_text else 0.0
        active_weights_sum += base_weights["title"]

    # ---- 2. Core Rows (hybrid) ----
    if signature.get("core_rows"):
        component_scores["core_rows"] = _score_signature_list(
            sig_key=sig_key,
            list_key="core_rows",
            sig_items=signature["core_rows"],
            sheet_items_text=sheet_rows_text,
            sheet_items_emb=sheet_rows_emb,
            alpha=alpha,
        )
        active_weights_sum += base_weights["core_rows"]

    # ---- 3. Core Cols (hybrid) ----
    if signature.get("core_cols"):
        component_scores["core_cols"] = _score_signature_list(
            sig_key=sig_key,
            list_key="core_cols",
            sig_items=signature["core_cols"],
            sheet_items_text=sheet_cols_text,
            sheet_items_emb=sheet_cols_emb,
            alpha=alpha,
        )
        active_weights_sum += base_weights["core_cols"]

    # ---- 4. Keywords (hybrid against combined text) ----
    if signature.get("keywords"):
        combined_text = sheet_rows_text + sheet_cols_text
        combined_emb = (
            np.vstack([sheet_rows_emb, sheet_cols_emb])
            if sheet_rows_emb.shape[0] + sheet_cols_emb.shape[0] > 0
            else np.zeros((0, sheet_rows_emb.shape[1]), dtype=np.float32)
        )
        component_scores["keywords"] = _score_signature_list(
            sig_key=sig_key,
            list_key="keywords",
            sig_items=signature["keywords"],
            sheet_items_text=combined_text,
            sheet_items_emb=combined_emb,
            alpha=alpha,
        )
        active_weights_sum += base_weights["keywords"]

    # ---- 5. Common Cols (hybrid) ----
    if signature.get("common_cols"):
        component_scores["common_cols"] = _score_signature_list(
            sig_key=sig_key,
            list_key="common_cols",
            sig_items=signature["common_cols"],
            sheet_items_text=sheet_cols_text,
            sheet_items_emb=sheet_cols_emb,
            alpha=alpha,
        )
        active_weights_sum += base_weights["common_cols"]

    # ---- 6. Common Rows (hybrid) ----
    if signature.get("common_rows"):
        component_scores["common_rows"] = _score_signature_list(
            sig_key=sig_key,
            list_key="common_rows",
            sig_items=signature["common_rows"],
            sheet_items_text=sheet_rows_text,
            sheet_items_emb=sheet_rows_emb,
            alpha=alpha,
        )
        active_weights_sum += base_weights["common_rows"]

    if active_weights_sum == 0:
        return 0.0

    final_score = sum(
        component_scores[k] * (base_weights[k] / active_weights_sum)
        for k in component_scores
    )
    # اگر وزن فعال خیلی کم بود، امتیاز را تعدیل کن
    if active_weights_sum < MIN_ACTIVE_WEIGHT:
        final_score *= (active_weights_sum / MIN_ACTIVE_WEIGHT)
    
    return final_score


def _contains_fuzzy(target_pattern: str, text_set: set) -> bool:
    """Checks if target canonical label exists within any extracted text label."""
    for text in text_set:
        if target_pattern in text or text in target_pattern:
            return True
    return False


def _get_normalized_signatures() -> Dict[str, Dict[str, Any]]:
    """Normalizes all string elements in SIGNATURES catalog dynamically."""
    normalized = {}
    for key, data in SIGNATURES.items():
        sig_dict = {}
        if "title" in data:
            sig_dict["title"] = normalize_persian_text(data["title"])
        
        for list_key in ["core_rows", "common_rows", "core_cols", "common_cols", "keywords"]:
            if list_key in data:
                sig_dict[list_key] = [normalize_persian_text(x) for x in data[list_key] if x]
                
        normalized[key] = sig_dict
    return normalized