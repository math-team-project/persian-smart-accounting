"""
M4: LLM-based variable extraction + question redesign.

Complements the M2/M3 retrieval layer. Takes a RelevantSlice and
resolves the variable's value via one or more independent LLMs; then,
only when the question's condition still fails, proposes a corrected
data-point spec using lightweight sheet templates (never full content).
"""

from .client import AnthropicClient, LLMClient, MockLLMClient, OpenAIClient
from .extractor import EnsembleAnswer, EnsembleExtractor, Voter
from .orchestrator import (
    QuestionInfo,
    QuestionOrchestrator,
    QuestionResolution,
    RedesignEnsemble,
    RedesignVoter,
)
from .parser import AnswerParseError, parse_extraction_answer, parse_redesign_answer
from .prompts import ExtractionPromptBuilder, RedesignPromptBuilder

__all__ = [
    "LLMClient", "AnthropicClient", "OpenAIClient", "MockLLMClient",
    "ExtractionPromptBuilder", "RedesignPromptBuilder",
    "parse_extraction_answer", "parse_redesign_answer", "AnswerParseError",
    "EnsembleExtractor", "EnsembleAnswer", "Voter",
    "QuestionOrchestrator", "QuestionInfo", "QuestionResolution",
    "RedesignEnsemble", "RedesignVoter",
]