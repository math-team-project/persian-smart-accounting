"""
Data model for one retrievable unit inside the RAG-based fallback
(level 3): a single logical table, which may be a whole sheet, a
sub-section of a sheet, or several sheets merged together - see
segmenter.py for why that splitting/merging is necessary.
"""

from dataclasses import dataclass
from typing import List

import pandas as pd


@dataclass
class Chunk:
    """One embeddable, retrievable logical table.

    `dataframe` always holds the FULL logical table - never a
    truncated slice of it - so row/column integrity is preserved when
    it is later sent to the LLM.
    """

    chunk_id: str
    file_key: str
    source_sheet_names: List[str]
    dataframe: pd.DataFrame
    metadata_text: str = ""  # filled in by metadata_builder before embedding
