"""LLM provider adapters.

Touchstone uses an LLM internally for: test-case suggestions, summarizing
audit anomalies, drafting Slack messages, etc. Operators pick the provider
in `touchstone.toml` — Touchstone does not hard-wire any vendor.

CRITICAL: API keys come from the secret manager. The AI assistant calling
Touchstone NEVER sees provider API keys; only Touchstone's own internal
code calls these adapters, and the assistant sees only the LLM-derived
output.
"""

from touchstone.llm.adapter import LLM, LLMMessage, LLMResponse, build_llm

__all__ = ["LLM", "LLMMessage", "LLMResponse", "build_llm"]
