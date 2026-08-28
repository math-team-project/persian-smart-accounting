from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union
import pandas as pd
import warnings
warnings.filterwarnings('ignore')  # Suppress all warnings

# logger = logging.getLogger("year_relabeler")
# if not logger.handlers:
#     _handler = logging.StreamHandler()
#     _handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
#     logger.addHandler(_handler)
#     logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Configuration (plain module-level constants, not a class -> easy to tweak)
# ---------------------------------------------------------------------------

# Plausible range for a 4-digit Jalali year. Wide on purpose: we rely on
# structural parsing (below) rather than a fixed list of "known" years.
PLAUSIBLE_YEAR_MIN = 1380
PLAUSIBLE_YEAR_MAX = 1410

# A year must show up in at least this many *distinct* column headers
# (pooled across every dataframe given to us) to be treated as a "core"
# year when picking the anchor (= current year). Years appearing fewer
# times are still relabeled (via arithmetic offset), they simply don't get
# a vote in deciding which year is the anchor.
DEFAULT_MIN_OCCURRENCES_FOR_ANCHOR = 2

# Regex used ONLY for structural tokenization (splitting a header into a
# date-shaped run of digits vs. everything else). This is not a list of
# keywords to match against -- it is generic digit-run detection.
_DATE_SHAPED_RUN = re.compile(r"(?<!\d)(\d{2,4})/(\d{1,2})/(\d{1,2})(?!\d)")
_DIGIT_RUN = re.compile(r"\d+")

# Pattern pandas appends to de-duplicate repeated column names on read_csv /
# read_excel, e.g. "1398/12/29 _ ریال.1", "...×.2". We strip this before
# trying to interpret the header text, and we don't need to preserve it when
# writing the new label back (duplicate labels are fine in a DataFrame).
_PANDAS_DEDUP_SUFFIX = re.compile(r"\.\d+$")


# ---------------------------------------------------------------------------
# Low level: turning a single header string into candidate year(s)
# ---------------------------------------------------------------------------

def _is_plausible_year(year: int) -> bool:
    return PLAUSIBLE_YEAR_MIN <= year <= PLAUSIBLE_YEAR_MAX


def _digits_to_year(token: str) -> Optional[int]:
    """
    Classify a run of digits by its LENGTH, not by keywords around it.
      length 4 -> already a full year candidate (e.g. "1398", "1405")
      length 3 -> the leading "1" (thousands digit) was dropped
                  (e.g. "398" -> 1398, "405" -> 1405)
      length 2 -> ambiguous: could belong to the 1300s or the 1400s
                  (resolved later using an anchor/hint, see _resolve_short_year)
      anything else -> not a year (amounts, note/footnote numbers, etc.)
    """
    n = len(token)
    if n == 4:
        year = int(token)
        return year if _is_plausible_year(year) else None
    if n == 3:
        year = 1000 + int(token)
        return year if _is_plausible_year(year) else None
    return None  # length 2 handled separately (needs a hint), others ignored


def _resolve_short_year(two_digit_token: str, hints: Optional[Iterable[int]]) -> Optional[int]:
    """
    Resolve a 2-digit token (e.g. "98", "05") into a full year using whichever
    of the two mathematically possible centuries (1300+n or 1400+n) is
    plausible AND closest to the given hints (already-known years from the
    same batch of headers, or -- once known -- the anchor itself).
    """
    n = int(two_digit_token)
    candidates = [y for y in (1300 + n, 1400 + n) if _is_plausible_year(y)]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    hints = list(hints) if hints else []
    if hints:
        target = sorted(hints)[len(hints) // 2]  # median of hints
        return min(candidates, key=lambda c: abs(c - target))
    return max(candidates)  # no information at all -> prefer the more recent century


def extract_years_from_header(text: object, hints: Optional[Iterable[int]] = None) -> List[Tuple[int, Tuple[int, int]]]:
    """
    Extract every year found in ONE header string, returning (year, span)
    pairs where span=(start, end) is the character range in `text` that
    represents that year (so callers can surgically replace just that part
    of the header and keep the rest, e.g. the " _ ریال" suffix, intact).

    Two structural passes, no keyword list:
      1) date-shaped runs like "1398/12/29" or "97/12/29" -> the first
         digit-run is the year, the rest (month/day) is deliberately ignored
         so it can never be mistaken for a second year.
      2) whatever digit-runs are left in the string, classified by length.
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return []
    text = str(text)
    found: List[Tuple[int, Tuple[int, int]]] = []
    consumed = [False] * len(text)

    # Pass 1: date-shaped runs (structural, not keyword based)
    for m in _DATE_SHAPED_RUN.finditer(text):
        token = m.group(1)
        year = _digits_to_year(token) if len(token) in (3, 4) else _resolve_short_year(token, hints)
        if year is not None:
            # Replace the ENTIRE date (year/month/day), not just the year
            # part -- once a date becomes "سال جاری" the day/month digits
            # (e.g. "/12/29", always the fiscal year-end anyway) no longer
            # carry information and would otherwise be left dangling.
            found.append((year, m.span()))
            for i in range(*m.span()):
                consumed[i] = True

    # Pass 2: remaining standalone digit-runs
    for m in _DIGIT_RUN.finditer(text):
        start, end = m.span()
        if any(consumed[start:end]):
            continue  # already part of a date-shaped run handled above
        token = m.group(0)
        if len(token) in (3, 4):
            year = _digits_to_year(token)
        elif len(token) == 2:
            year = _resolve_short_year(token, hints)
        else:
            year = None
        if year is not None:
            found.append((year, (start, end)))

    return found


# ---------------------------------------------------------------------------
# Scanning many dataframes' COLUMN HEADERS ONLY (per requirement)
# ---------------------------------------------------------------------------

DataFrames = Union[Sequence[pd.DataFrame], Dict[str, pd.DataFrame]]


def _iter_dataframes(dataframes: DataFrames) -> Iterable[Tuple[str, pd.DataFrame]]:
    if isinstance(dataframes, pd.DataFrame):
        yield "dataframe_0", dataframes
    elif isinstance(dataframes, dict):
        for name, df in dataframes.items():
            if isinstance(df, pd.DataFrame):
                yield str(name), df
    elif isinstance(dataframes, (list, tuple)):
        for i, df in enumerate(dataframes):
            if isinstance(df, pd.DataFrame):
                yield f"dataframe_{i}", df
            # else:
            #     logger.warning("Item at index %d is not a pandas DataFrame, got %s", i, type(df))

def _clean_header(col: object) -> str:
    """Strip the pandas de-duplication suffix (".1", ".2", ...) so the
    underlying text can be interpreted the same way regardless of how many
    times it repeats across a wide table."""
    text = str(col)
    return _PANDAS_DEDUP_SUFFIX.sub("", text)


def collect_header_years(dataframes: DataFrames) -> Counter:
    """
    First pass over every dataframe's df.columns (headers only -- row values
    are never inspected here). Returns a Counter of {year: how many distinct
    headers mentioned it}, pooled across all dataframes.

    This runs in two internal rounds so 2-digit headers can be resolved
    using whatever 3/4-digit years were found unambiguously elsewhere in the
    same batch (e.g. a mix of "1398" and "98" headers across sheets).
    """
    all_headers: List[str] = []
    for name, df in _iter_dataframes(dataframes):
        headers = [_clean_header(c) for c in df.columns]
        # logger.debug("Sheet '%s' has %d columns: %s", name, len(headers), headers)
        all_headers.extend(headers)

    # Round 1: unambiguous years only (4 and 3 digit forms), used as hints.
    unambiguous_hints: List[int] = []
    for header in all_headers:
        for year, _span in extract_years_from_header(header, hints=None):
            unambiguous_hints.append(year)

    # Round 2: full extraction, now letting 2-digit headers use the hints.
    year_counter: Counter = Counter()
    for header in all_headers:
        years_found = {y for y, _span in extract_years_from_header(header, hints=unambiguous_hints)}
        for year in years_found:
            year_counter[year] += 1  # count distinct headers mentioning the year

    # logger.info("Collected year frequency across headers: %s", dict(year_counter))
    return year_counter


def determine_anchor_year(
    year_counter: Counter,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES_FOR_ANCHOR,
) -> Tuple[Optional[int], dict]:
    """
    Pick the anchor ("current year") purely from header evidence:
      - years that appear in fewer than `min_occurrences` distinct headers
        are treated as incidental mentions (footnote-like), not part of the
        report's own year axis, and are excluded from the vote -- but they
        are NOT discarded from later relabeling; they just don't get a say
        in choosing the anchor.
      - among the remaining "core" years, the anchor is the largest one
        (a financial statement's own header year is, by convention, never
        smaller than the comparison/prior years shown next to it).

    Returns (anchor_year_or_None, diagnostics_dict) so the caller can log or
    inspect exactly why a given year was chosen.
    """
    if not year_counter:
        # logger.warning("No years found in any header; cannot determine an anchor.")
        return None, {"core_years": [], "excluded_as_noise": [], "reason": "no years found"}

    core_years = {y: c for y, c in year_counter.items() if c >= min_occurrences}
    excluded = {y: c for y, c in year_counter.items() if c < min_occurrences}

    if core_years:
        anchor = max(core_years)
        reason = f"max of core years (seen in >= {min_occurrences} headers)"
    else:
        # Nothing repeated enough; fall back to the overall max so the
        # function still returns something usable, but flag it clearly.
        anchor = max(year_counter)
        reason = "fallback: no year met min_occurrences, used overall max"
        # logger.warning(
        #     "No year reached min_occurrences=%d; falling back to max(%s)=%s",
        #     min_occurrences, dict(year_counter), anchor,
        # )

    diagnostics = {
        "core_years": sorted(core_years),
        "excluded_as_noise": sorted(excluded),
        "anchor": anchor,
        "reason": reason,
        "full_frequency": dict(year_counter),
    }
    # logger.info("Anchor year determined: %s (%s)", anchor, reason)
    # if excluded:
    #     logger.info("Years excluded from the anchor vote as noise: %s", diagnostics["excluded_as_noise"])
    return anchor, diagnostics


# ---------------------------------------------------------------------------
# Turning (year, anchor) into a label -- pure arithmetic, no fixed list
# ---------------------------------------------------------------------------

def relative_year_label(year: int, anchor: int) -> str:
    """
    Purely arithmetic relative label. Handles ANY offset in either
    direction, so a year 5 years before or 3 years after the anchor is
    labeled correctly without needing to extend any hardcoded list:
      offset == 0   -> "سال جاری"
      offset == 1   -> "سال قبل"
      offset  > 1   -> "{offset} سال قبل"
      offset == -1  -> "سال بعد"
      offset  < -1  -> "{abs(offset)} سال بعد"
    """
    offset = anchor - year
    if offset == 0:
        return "سال جاری"
    if offset == 1:
        return "سال قبل"
    if offset > 1:
        return f"{offset} سال قبل"
    if offset == -1:
        return "سال بعد"
    return f"{abs(offset)} سال بعد"


# ---------------------------------------------------------------------------
# Building the replacement plan (TEXT -> TEXT, location independent)
# ---------------------------------------------------------------------------

def build_header_replacement_map(dataframes: DataFrames, anchor: int) -> Dict[str, str]:
    """
    Second pass: for every DISTINCT header text seen across all dataframes,
    if it contains a year, compute the new text with the year portion
    replaced by its relative label (keeping everything else in the header,
    e.g. " _ ریال", untouched).

    Returns a plain dict {original_header_text: new_header_text}. This is
    intentionally location-agnostic (no sheet/row/col info) so it can be
    reviewed, edited, or persisted independently of which dataframe it came
    from, and reused across any dataframe that happens to share the same
    header text.
    """
    replacement_map: Dict[str, str] = {}

    for name, df in _iter_dataframes(dataframes):
        for raw_col in df.columns:
            header = _clean_header(raw_col)
            if header in replacement_map:
                continue  # already resolved from another sheet/column

            years_with_spans = extract_years_from_header(header, hints=[anchor])
            if not years_with_spans:
                continue

            # Replace right-to-left so earlier spans' indices stay valid.
            new_header = header
            for year, (start, end) in sorted(years_with_spans, key=lambda t: t[1][0], reverse=True):
                label = relative_year_label(year, anchor)
                new_header = new_header[:start] + label + new_header[end:]

            # Collapse artefacts like a stray leading/trailing separator or
            # doubled spaces left behind after cutting out a full date.
            new_header = re.sub(r"\s{2,}", " ", new_header).strip(" _-")

            if new_header != header:
                replacement_map[header] = new_header
                # logger.debug("Planned rename: %r -> %r", header, new_header)

    # logger.info("Built replacement map with %d entries: %s", len(replacement_map))
    return replacement_map


# ---------------------------------------------------------------------------
# The orchestrating function (runs the whole detection pipeline once)
# ---------------------------------------------------------------------------

def analyze_dataframes(
    dataframes: DataFrames,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES_FOR_ANCHOR,
) -> dict:
    """
    Run the full pipeline over many dataframes and return everything the
    caller needs to inspect BEFORE touching any data:
      {
        "anchor": int | None,
        "diagnostics": {...},           # why that anchor was picked
        "replacement_map": {old: new},  # text -> text, ready for review
      }
    Nothing is mutated here -- see apply_header_relabeling() for that.
    """
    # logger.info("Starting analysis over dataframes...")
    year_counter = collect_header_years(dataframes)
    anchor, diagnostics = determine_anchor_year(year_counter, min_occurrences=min_occurrences)

    if anchor is None:
        # logger.warning("No anchor could be determined; returning an empty replacement map.")
        return {"anchor": None, "diagnostics": diagnostics, "replacement_map": {}}

    replacement_map = build_header_replacement_map(dataframes, anchor)
    return {"anchor": anchor, "diagnostics": diagnostics, "replacement_map": replacement_map}


# ---------------------------------------------------------------------------
# The applier -- the ONLY function that actually mutates a dataframe's
# headers, and it does so strictly from an externally supplied map so the
# caller stays in full control (can inspect/edit the map before applying it,
# reuse it across dataframes, log every change made, etc.)
# ---------------------------------------------------------------------------

def apply_header_relabeling(
    df: pd.DataFrame,
    replacement_map: Dict[str, str],
    inplace: bool = False,
):
    """
    Rename df's columns according to replacement_map. Any column whose
    (cleaned) header is not a key in replacement_map is left untouched.
    Every actual change is logged individually for auditability.
    """
    if isinstance(df, pd.DataFrame):
        target_df = df if inplace else df.copy()
        new_columns = []
        for raw_col in target_df.columns:
            cleaned = _clean_header(raw_col)
            new_name = replacement_map.get(cleaned)
            if new_name is not None:
                # logger.info("Renaming column %r -> %r", raw_col, new_name)
                new_columns.append(new_name)
            else:
                new_columns.append(raw_col)
        target_df.columns = new_columns
        return target_df
    return None

def apply_header_relabeling_to_many(
    dataframes: DataFrames,
    replacement_map: Dict[str, str],
) -> DataFrames:
    """Convenience wrapper: apply the same map to a whole batch of dataframes
    (list or dict), returning the same container shape with new dataframes."""
    if isinstance(dataframes, dict):
        return {name: apply_header_relabeling(df, replacement_map) for name, df in dataframes.items()}
    return [apply_header_relabeling(df, replacement_map) for df in dataframes]
