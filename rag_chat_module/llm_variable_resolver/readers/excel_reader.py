"""
Reads Excel workbooks into SheetData objects.

Each worksheet becomes one SheetData; its `name` is the sheet's own
name. Header rows are NOT stripped - real audit files put title rows
above the actual table (e.g. "پارک علم و فناوری خراسان"), and the
LLM prompt needs to see them as part of the sheet.
"""

from pathlib import Path
from typing import List, Union

import pandas as pd

from ..models import SheetData
from .base import BaseReader


class ExcelReader(BaseReader):
    def load(self, source: Union[str, Path]) -> List[SheetData]:
        path = Path(source)
        # sheet_name=None -> {sheet_name: DataFrame} for all sheets.
        # header=None keeps every physical row as data, which is what
        # audit workbooks need (their "headers" are multiline merged cells).
        sheets = pd.read_excel(path, sheet_name=None, header=None)
        return [
            SheetData(name=str(name), dataframe=df)
            for name, df in sheets.items()
        ]