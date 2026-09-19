"""
Builds the text that actually gets embedded for a Chunk.

Per the domain expert's guidance, embedding raw cell values directly
is unreliable here: these Persian financial tables mix free-text
labels with numbers in no fixed layout, so numeric noise would drown
out the signal. Instead, the embedded text is built only from the
file identity, sheet name(s), and every distinct non-numeric text
value found anywhere in the table (row labels, column headers,
wherever they happen to sit).
"""

from typing import List

import pandas as pd

from .chunk_models import Chunk


def build_metadata_text(chunk: Chunk) -> str:
    """
    Note: the file's own name/key is deliberately NOT included here.
    It is identical across every chunk of the same file, so it adds no
    discriminative signal - and when a query happens to mention the
    file's title (common, since report titles and sheet anchors often
    overlap), including it would inflate every chunk's similarity to
    that query equally, defeating the whole point of the search.
    """
    labels = collect_text_labels(chunk.dataframe)
    parts = [
        f"sheets: {', '.join(chunk.source_sheet_names)}",
        f"labels: {' | '.join(labels)}",
    ]
    return "\n".join(parts)


def collect_text_labels(df: pd.DataFrame) -> List[str]:
    """Every distinct non-numeric text value in a table - reused by the
    sheet-template builder (evaluation package) as well as here."""
    labels: List[str] = []
    for value in df.to_numpy().flatten():
        text = str(value).strip()
        if not text or text.lower() == "nan" or _looks_numeric(text):
            continue
        if text not in labels:
            labels.append(text)
    return labels


def _looks_numeric(text: str) -> bool:
    cleaned = text.replace(",", "").replace(".", "", 1).lstrip("-")
    return cleaned.isdigit()
