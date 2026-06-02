"""LLM adapter — provider-agnostic interface.

A single LLM class with implementations for Anthropic, OpenAI, Azure OpenAI,
AWS Bedrock, Google Vertex, and local Ollama / vLLM (OpenAI-compatible).

Selection happens in config:

    [llm]
    provider = "anthropic"
    model = "claude-sonnet-4-6"
    api_key_ref = "env://ANTHROPIC_API_KEY"
    # provider-specific knobs go in [llm.extra]
    extra = { max_tokens = 1024 }

Or:

    [llm]
    provider = "azure_openai"
    model = "gpt-4o"
    api_key_ref = "azurekv://my-vault.vault.azure.net/openai-key"

    [llm.extra]
    azure_endpoint = "https://acme.openai.azure.com/"
    api_version = "2024-07-01-preview"

Or for fully air-gapped:

    [llm]
    provider = "ollama"
    model = "llama3.1:70b"

    [llm.extra]
    base_url = "http://ollama.internal:11434"
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from touchstone.secrets import resolve


Role = Literal["system", "user", "assistant"]


@dataclass(slots=True)
class LLMMessage:
    role: Role
    content: str


@dataclass(slots=True)
class LLMResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class LLM(ABC):
    """Provider-agnostic chat-completion interface.

    Implementations should:
      - Resolve api_key_ref via touchstone.secrets at construction time.
      - Honor `max_tokens` and `temperature` from config or per-call kwargs.
      - Wrap provider errors in `LLMError` with a useful message.
      - Never log the API key or the full prompt content at INFO level.
    """

    def __init__(self, model: str, *, api_key: str | None = None, extra: dict[str, Any] | None = None) -> None:
        self.model = model
        self._api_key = api_key
        self.extra = extra or {}

    @abstractmethod
    def chat(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        **kwargs: Any,
    ) -> LLMResponse:
        ...


class LLMError(Exception):
    pass


# ----------------------- Concrete adapters --------------------------------

class AnthropicLLM(LLM):
    def chat(self, messages, *, max_tokens=1024, temperature=0.2, **kwargs):
        try:
            import anthropic
        except ImportError as e:
            raise LLMError("anthropic SDK not installed. pip install anthropic") from e
        client = anthropic.Anthropic(api_key=self._api_key)
        system = next((m.content for m in messages if m.role == "system"), None)
        msgs = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        try:
            r = client.messages.create(
                model=self.model, max_tokens=max_tokens, temperature=temperature,
                system=system or "You are a helpful QA assistant.",
                messages=msgs,
            )
        except anthropic.APIError as e:
            raise LLMError(str(e)) from None
        text = "".join(b.text for b in r.content if getattr(b, "type", None) == "text")
        return LLMResponse(
            content=text, model=r.model,
            input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens,
            stop_reason=r.stop_reason or "",
        )


class OpenAILLM(LLM):
    def chat(self, messages, *, max_tokens=1024, temperature=0.2, **kwargs):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise LLMError("openai SDK not installed. pip install openai") from e
        client = OpenAI(api_key=self._api_key, base_url=self.extra.get("base_url"))
        try:
            r = client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                max_tokens=max_tokens, temperature=temperature,
            )
        except Exception as e:
            raise LLMError(str(e)) from None
        choice = r.choices[0]
        return LLMResponse(
            content=choice.message.content or "", model=r.model,
            input_tokens=r.usage.prompt_tokens if r.usage else 0,
            output_tokens=r.usage.completion_tokens if r.usage else 0,
            stop_reason=choice.finish_reason or "",
        )


class AzureOpenAILLM(LLM):
    def chat(self, messages, *, max_tokens=1024, temperature=0.2, **kwargs):
        try:
            from openai import AzureOpenAI
        except ImportError as e:
            raise LLMError("openai SDK not installed. pip install openai") from e
        client = AzureOpenAI(
            api_key=self._api_key,
            azure_endpoint=self.extra["azure_endpoint"],
            api_version=self.extra.get("api_version", "2024-07-01-preview"),
        )
        try:
            r = client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                max_tokens=max_tokens, temperature=temperature,
            )
        except Exception as e:
            raise LLMError(str(e)) from None
        choice = r.choices[0]
        return LLMResponse(
            content=choice.message.content or "", model=r.model,
            input_tokens=r.usage.prompt_tokens if r.usage else 0,
            output_tokens=r.usage.completion_tokens if r.usage else 0,
            stop_reason=choice.finish_reason or "",
        )


class BedrockLLM(LLM):
    def chat(self, messages, *, max_tokens=1024, temperature=0.2, **kwargs):
        try:
            import boto3
        except ImportError as e:
            raise LLMError("boto3 not installed. pip install boto3") from e
        client = boto3.client("bedrock-runtime", region_name=self.extra.get("region", "us-east-1"))
        import json
        system = next((m.content for m in messages if m.role == "system"), "")
        conv = [{"role": m.role, "content": [{"text": m.content}]}
                for m in messages if m.role != "system"]
        try:
            r = client.converse(
                modelId=self.model,
                messages=conv,
                system=[{"text": system}] if system else [],
                inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            )
        except Exception as e:
            raise LLMError(str(e)) from None
        text = "".join(b["text"] for b in r["output"]["message"]["content"] if "text" in b)
        usage = r.get("usage", {})
        return LLMResponse(
            content=text, model=self.model,
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            stop_reason=r.get("stopReason", ""),
        )


class VertexLLM(LLM):
    def chat(self, messages, *, max_tokens=1024, temperature=0.2, **kwargs):
        try:
            from google import genai
        except ImportError as e:
            raise LLMError("google-genai not installed. pip install google-genai") from e
        client = genai.Client(
            vertexai=True,
            project=self.extra["project"],
            location=self.extra.get("location", "us-central1"),
        )
        from google.genai import types
        contents = []
        system = ""
        for m in messages:
            if m.role == "system":
                system = m.content
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append(types.Content(role=role, parts=[types.Part(text=m.content)]))
        try:
            r = client.models.generate_content(
                model=self.model, contents=contents,
                config=types.GenerateContentConfig(
                    max_output_tokens=max_tokens, temperature=temperature,
                    system_instruction=system or None,
                ),
            )
        except Exception as e:
            raise LLMError(str(e)) from None
        return LLMResponse(content=r.text or "", model=self.model)


class OllamaLLM(LLM):
    """Talks to anything OpenAI-API-compatible: Ollama, vLLM, LM Studio,
    text-generation-webui's openai endpoint, llama.cpp server."""

    def chat(self, messages, *, max_tokens=1024, temperature=0.2, **kwargs):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise LLMError("openai SDK not installed (used as a generic http client).") from e
        client = OpenAI(
            api_key=self._api_key or "ollama",
            base_url=self.extra.get("base_url", "http://localhost:11434/v1"),
        )
        try:
            r = client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                max_tokens=max_tokens, temperature=temperature,
            )
        except Exception as e:
            raise LLMError(str(e)) from None
        choice = r.choices[0]
        return LLMResponse(
            content=choice.message.content or "", model=r.model,
            input_tokens=(r.usage.prompt_tokens if r.usage else 0),
            output_tokens=(r.usage.completion_tokens if r.usage else 0),
            stop_reason=choice.finish_reason or "",
        )


_PROVIDERS: dict[str, type[LLM]] = {
    "anthropic": AnthropicLLM,
    "openai": OpenAILLM,
    "azure_openai": AzureOpenAILLM,
    "bedrock": BedrockLLM,
    "vertex": VertexLLM,
    "ollama": OllamaLLM,
    "openai_compat": OllamaLLM,
}


def build_llm(config: dict[str, Any] | None) -> LLM | None:
    """Construct an LLM from a config dict. Returns None when `provider` is
    `"none"` — callers should fall back to non-LLM heuristics in that case."""
    if not config or config.get("provider") in (None, "none"):
        return None
    provider = config["provider"]
    cls = _PROVIDERS.get(provider)
    if cls is None:
        raise LLMError(f"unknown llm provider: {provider!r}")
    api_key = resolve(config["api_key_ref"]) if config.get("api_key_ref") else None
    return cls(model=config["model"], api_key=api_key, extra=config.get("extra") or {})
