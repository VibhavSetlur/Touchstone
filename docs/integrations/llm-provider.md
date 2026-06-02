# LLM provider configuration

Touchstone uses an LLM internally for test-case generation and a handful of
synthesis tasks (drafting stakeholder messages from playbook reports, for
example). The operator picks the provider. Touchstone does not bake in any
vendor.

## Supported providers

| Provider       | Config `provider`        | Auth                                       |
| -------------- | ------------------------ | ------------------------------------------ |
| Anthropic      | `"anthropic"`            | `api_key_ref`                              |
| OpenAI         | `"openai"`               | `api_key_ref`                              |
| Azure OpenAI   | `"azure_openai"`         | `api_key_ref` + `extra.azure_endpoint`     |
| AWS Bedrock    | `"bedrock"`              | AWS IAM (no api_key_ref needed)            |
| Google Vertex  | `"vertex"`               | ADC + `extra.project`                      |
| Ollama / vLLM  | `"ollama"` / `"openai_compat"` | `extra.base_url`, no key needed for local |
| Disabled       | `"none"`                 | —                                          |

## Examples

### Anthropic

```toml
[llm]
provider = "anthropic"
model = "claude-sonnet-4-6"
api_key_ref = "env://ANTHROPIC_API_KEY"

[llm.extra]
max_tokens = 1024
```

### Azure OpenAI

```toml
[llm]
provider = "azure_openai"
model = "gpt-4o"
api_key_ref = "azurekv://acme-vault.vault.azure.net/openai-key"

[llm.extra]
azure_endpoint = "https://acme.openai.azure.com/"
api_version = "2024-07-01-preview"
```

### AWS Bedrock (Claude)

```toml
[llm]
provider = "bedrock"
model = "us.anthropic.claude-sonnet-4-5-20251001-v1:0"

[llm.extra]
region = "us-east-1"
```

### Self-hosted (Ollama, vLLM, LM Studio, llama.cpp)

```toml
[llm]
provider = "ollama"
model = "llama3.1:70b"

[llm.extra]
base_url = "http://ollama.internal:11434/v1"
```

## The credential contract

The LLM API key is held by Touchstone — never exposed to the *calling* AI
assistant (the one chatting with the user through MCP). The internal LLM
adapter is invoked by Touchstone's own code (test-gen, playbooks), not by
MCP tools.

## Disabling LLM use entirely

```toml
[llm]
provider = "none"
```

When set to `"none"`, the test-gen module falls back to its
profile-derived heuristics (which are already pretty good). Other features
that benefit from LLM assistance gracefully degrade — they note in the
report that they ran without LLM enrichment.

## Cost & telemetry

Touchstone records input/output token counts per LLM call in the audit log
(when LLM use happens inside an audited flow). For cost monitoring, ship
the audit log to your usual cost dashboard.

## Why not just "use the AI assistant's LLM"

Because the AI assistant's LLM is reasoning *about* Touchstone's outputs;
making it also Touchstone's internal LLM creates an awkward dependency
(test-gen would have to call back through MCP, which is the wrong
direction). Keeping a separate adapter lets operators pick a faster/cheaper
model for internal use than the one their developers use interactively.
