from pathlib import Path
from openpyxl import load_workbook
import re
from pyprojroot import here
import pandas as pd
import argparse
import sys

def clean_cell(value):
    """
    Clean and normalize a cell value for Markdown output.
    """
    if pd.isna(value) or value is None:
        return ""

    value = str(value)

    # Replace multiple whitespaces with a single space
    value = re.sub(r"\s+", " ", value.strip())

    # Escape Markdown table separator
    value = value.replace("|", "\\|")
    return value


def df_to_markdown(df, title):
    """
    Convert a DataFrame (worksheet) into Markdown table.
    """
    if df.empty:
        return ""

    # Clean header and rows
    headers = [clean_cell(col) for col in df.columns]

    rows = []
    for _, row in df.iterrows():
        cleaned = [clean_cell(cell) for cell in row]
        # Remove trailing empty cells
        while cleaned and cleaned[-1] == "":
            cleaned.pop()
        if any(cleaned):  # check if row is not completely empty
            rows.append(cleaned)

    if not rows and not any(headers):
        return ""

    markdown = []
    markdown.append(f"## {title}\n")

    max_cols = max([len(headers)] + [len(r) for r in rows]) if rows else len(headers)

    # Pad headers and rows to align columns correctly
    headers += [""] * (max_cols - len(headers))
    markdown.append("| " + " | ".join(headers) + " |")
    markdown.append("| " + " | ".join(["---"] * max_cols) + " |")

    for r in rows:
        r += [""] * (max_cols - len(r))
        markdown.append("| " + " | ".join(r) + " |")

    markdown.append("")
    return "\n".join(markdown)


def excel_to_markdown(excel_path, output_path):
    """
    Convert an Excel workbook into a Markdown document.
    """

    # Choose engine based on file extension
    engine = "xlrd" if excel_path.suffix.lower() == ".xls" else "openpyxl"

    # Read all sheets into a dictionary of DataFrames
    excel_file = pd.read_excel(excel_path, sheet_name=None, header=0, engine=engine)

    md = []
    md.append(f"# {Path(excel_path).stem}\n")
    md.append("## Table of Contents\n")

    for sheet_name in excel_file.keys():
            anchor = sheet_name.lower().replace(" ", "-")
            md.append(f"- [{sheet_name}](#{anchor})")

    md.append("\n---\n")

    for sheet_name, df in excel_file.items():
        md.append(df_to_markdown(df, sheet_name))
        md.append("\n---\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="xlsx to md file")
    parser.add_argument("xlsx")
    parser.add_argument("-o", "--output", default=None)
    args = parser.parse_args()

    out_path = args.output or (args.xlsx.rsplit(".", 1)[0] + ".md")
    excel_to_markdown(args.xlsx, out_path)
