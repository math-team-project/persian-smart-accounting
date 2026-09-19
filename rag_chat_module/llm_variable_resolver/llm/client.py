"""
Pluggable LLM backends.

Mirrors the Embedder / VectorStore strategy pattern used by the
retrieval package: the pipeline depends only on the LLMClient
interface, so swapping the provider is a one-line change at the call
site. SDKs are imported lazily so this module stays importable even
when a given provider is not installed.
"""

from abc import ABC, abstractmethod
from typing import List, Optional


class LLMClient(ABC):
    """One text-in / text-out LLM endpoint."""

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: Optional[int] = None) -> str:
        """Send a system+user prompt; return the raw text response."""
        raise NotImplementedError


class AnthropicClient(LLMClient):
    """Claude via the official SDK."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-5",
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ):
        from anthropic import Anthropic  # deferred heavy import
        self._client = Anthropic(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return f"anthropic:{self._model}"

    def complete(self, system: str, user: str, max_tokens: Optional[int] = None) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens or self._max_tokens,
            temperature=self._temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            block.text for block in response.content if hasattr(block, "text")
        )


class OpenAIClient(LLMClient):
    """OpenAI-compatible chat client with verbose diagnostics.

    Any endpoint that speaks the OpenAI chat-completions protocol works:
    OpenAI, DeepSeek, OpenRouter, Groq, Together, Mistral, Azure, and
    local Ollama / vLLM.

    Diagnostics:
      * Every request and response is logged at DEBUG level, including
        the raw text, finish_reason, and token usage.
      * When `verbose=True`, the same information is printed to stderr
        directly, so it is visible even without configuring logging.
      * If `json_mode=True` and the endpoint rejects response_format,
        the client retries once without it and reports why.
      * Empty completions raise a clear RuntimeError instead of
        silently returning "", so downstream parse errors are not
        misleading.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 10000,
        json_mode: bool = True,
        verbose: bool = True,
        timeout: float = 120.0,
    ):
        from openai import OpenAI  # deferred heavy import

        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._json_mode = json_mode
        self._verbose = verbose
        self._base_url = base_url or "https://api.openai.com/v1"

    @property
    def name(self) -> str:
        return f"openai:{self._model}"

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
    ) -> str:
        effective_max = max_tokens or self._max_tokens
        payload = {
            "model": self._model,
            "temperature": self._temperature,
            "max_tokens": effective_max,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

        self._log(f"\n{'='*70}")
        self._log(f"[OpenAIClient] model={self._model}")
        self._log(f"[OpenAIClient] base_url={self._base_url}")
        self._log(f"[OpenAIClient] max_tokens={effective_max}  "
                  f"temperature={self._temperature}  "
                  f"json_mode={self._json_mode}")
        self._log(f"[OpenAIClient] system prompt: {len(system)} chars")
        self._log(f"[OpenAIClient] user prompt:   {len(user)} chars")

        # ---- Attempt 1: with response_format=json_object (if enabled)
        if self._json_mode:
            try:
                response = self._client.chat.completions.create(
                    **payload,
                    response_format={"type": "json_object"},
                )
                # print("##main: ", response, " ##main")
                text = self._extract_text(response)
                if text:
                    self._report_response(response, text, attempt="json_mode")
                    return text
                self._log("[OpenAIClient] json_mode returned empty text; "
                          "falling back to plain call")
            except Exception as exc:
                self._log(f"[OpenAIClient] json_mode request failed: "
                          f"{type(exc).__name__}: {exc}")
                self._log("[OpenAIClient] retrying without response_format")

        # ---- Attempt 2: plain call (no response_format)
        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as exc:
            self._log(f"[OpenAIClient] plain request ALSO failed: "
                      f"{type(exc).__name__}: {exc}")
            raise

        text = self._extract_text(response)
        self._report_response(response, text, attempt="plain")

        if not text:
            finish = getattr(response.choices[0], "finish_reason", "?")
            raise RuntimeError(
                f"LLM returned an empty completion. "
                f"finish_reason={finish!r}. "
                f"This usually means the model hit max_tokens "
                f"({effective_max}) before emitting any content, or the "
                f"endpoint rejected the request silently. "
                f"Increase max_tokens or switch to a non-reasoning model."
            )

        return text

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _extract_text(response) -> str:
        if not getattr(response, "choices", None):
            return ""
        choice = response.choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            return ""
        content = getattr(message, "content", None)
        # Some providers put the answer inside "reasoning_content" when
        # they think they are reasoning; refuse it silently rather than
        # return something the parser cannot use.
        return content or ""

    def _report_response(self, response, text: str, attempt: str) -> None:
        choice = response.choices[0] if response.choices else None
        finish = getattr(choice, "finish_reason", None) if choice else None
        usage = getattr(response, "usage", None)

        self._log(f"[OpenAIClient] attempt={attempt}  "
                  f"finish_reason={finish}")
        if usage is not None:
            self._log(
                f"[OpenAIClient] tokens: prompt={getattr(usage, 'prompt_tokens', '?')} "
                f"completion={getattr(usage, 'completion_tokens', '?')} "
                f"total={getattr(usage, 'total_tokens', '?')}"
            )

        self._log(f"[OpenAIClient] response text ({len(text)} chars):")
        if len(text) <= 2000:
            self._log(text)
        else:
            self._log(text[:1500])
            self._log(f"... [{len(text) - 2000} chars omitted] ...")
            self._log(text[-500:])
        self._log(f"{'='*70}\n")

        # A finish_reason of "length" almost always means the model was
        # truncated before it could finish, which for our JSON-only use
        # case is fatal. Warn loudly so the caller can raise max_tokens.
        if finish == "length":
            self._log(
                "[OpenAIClient] WARNING: finish_reason=length - the model "
                "was truncated by max_tokens. Raise max_tokens or use a "
                "shorter prompt / non-reasoning model."
            )

    def _log(self, message: str) -> None:
        if not self._verbose:
            return
        import sys
        print(message, file=sys.stderr, flush=True)
        

class MockLLMClient(LLMClient):
    """Deterministic stub for tests: returns a canned response per call
    and records every prompt it was given."""

    def __init__(self, responses: List[str], name: str = "mock"):
        self._responses = list(responses)
        self._name = name
        self.calls: List[dict] = []

    @property
    def name(self) -> str:
        return self._name

    def complete(self, system: str, user: str, max_tokens: Optional[int] = None) -> str:
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if not self._responses:
            return "{}"
        return self._responses.pop(0)