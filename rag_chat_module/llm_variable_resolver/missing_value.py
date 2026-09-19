"""
Strategy for deciding whether a variable's value counts as "missing".

Extracted from InputMerger so the rule can evolve (e.g. the future
M7 "recheck whole condition" flow) without touching merge logic.
"""

from abc import ABC, abstractmethod
from typing import Optional

# Values the caller's own code uses to mean "nothing was extracted".
# Kept narrow on purpose: "0" is a real value, not a placeholder.
_PLACEHOLDERS = {"", "—", "-", "--", "N/A", "None", "null", "nan"}


class MissingValueStrategy(ABC):
    @abstractmethod
    def is_missing(self, raw_value: Optional[str]) -> bool:
        raise NotImplementedError


class DefaultMissingValueStrategy(MissingValueStrategy):
    """Empty/placeholder strings count as missing; everything else does not."""

    def is_missing(self, raw_value: Optional[str]) -> bool:
        if raw_value is None:
            return True
        return str(raw_value).strip() in _PLACEHOLDERS