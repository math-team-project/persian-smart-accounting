"""Common interface shared by every reader."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Union

import pandas as pd

from ..models import SheetData


class BaseReader(ABC):
    """Every reader turns its source into a list of SheetData objects."""

    @abstractmethod
    def load(
        self,
        source: Union[str, Path, pd.DataFrame, Dict[str, pd.DataFrame]],
    ) -> List[SheetData]:
        raise NotImplementedError