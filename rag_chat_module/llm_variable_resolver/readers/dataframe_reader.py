"""
Reader for in-memory pandas DataFrames, or a dict of {name: DataFrame}.

Useful when the caller already has data loaded (e.g. from an upstream
step) and does not want to round-trip it through disk.
"""

from typing import Dict, List, Union

import pandas as pd

from ..models import SheetData
from .base import BaseReader


class DataFrameReader(BaseReader):
    def load(
        self,
        source: Union[pd.DataFrame, Dict[str, pd.DataFrame]],
    ) -> List[SheetData]:
        if isinstance(source, pd.DataFrame):
            return [SheetData(name="DataFrame", dataframe=source)]

        if isinstance(source, dict):
            return [
                SheetData(name=str(name), dataframe=df)
                for name, df in source.items()
            ]

        raise TypeError(
            f"DataFrameReader expects a DataFrame or dict of DataFrames, "
            f"got {type(source).__name__}"
        )