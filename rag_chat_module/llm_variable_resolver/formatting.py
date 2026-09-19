"""
Shared helper for rendering a DataFrame as LLM-ready text.

Markdown tables are used everywhere in this package: they are compact,
keep row/column alignment legible for both humans and the model, and
need no extra escaping compared to JSON or raw CSV.

NaN cells are rendered as empty (not the literal string "nan") because
audit spreadsheets have many blank cells; the "nan" text both wastes
context and confuses the model about which values are meaningful.
"""

import pandas as pd


def dataframe_to_labeled_text(df: pd.DataFrame, label: str) -> str:
    # Replace NaN with empty strings BEFORE to_markdown, so pandas does
    # not print the literal "nan" for every empty cell.
    cleaned = df.fillna("")
    body = cleaned.to_markdown(index=False, headers=[])
    return f"### {label}\n{body}\n"