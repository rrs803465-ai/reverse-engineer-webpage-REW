# Provider Configuration

## OpenRouter

Best choice for model variety. Single API key, access to hundreds of models.

```bash
python3 ccopecode.py config
# Choose: OpenRouter
# Key format: sk-or-v1-...
```

Environment variable: `REW_OPENROUTER_API_KEY`

Model discovery uses the `/api/v1/models` catalogue endpoint, which returns
context windows, pricing, and capability metadata.

---

## OpenAI

```bash
python3 ccopecode.py config
# Choose: OpenAI
# Key format: sk-...
```

Environment variable: `REW_OPENAI_API_KEY`

Tool calling is supported on all GPT-4 class models. GPT-4o is recommended
for the best balance of speed and quality.

---

## Anthropic

Uses the native `/v1/messages` API, not an OpenAI-compatible wrapper.
Tool use uses Anthropic's native format internally.

```bash
python3 ccopecode.py config
# Choose: Anthropic
# Key format: sk-ant-api03-...
```

Environment variable: `REW_ANTHROPIC_API_KEY`

Claude 3.5 Sonnet or Claude 3 Haiku are recommended for cost efficiency.

---

## OpenAI-compatible endpoints

For Ollama, LM Studio, vLLM, llama.cpp server, and similar:

```bash
python3 ccopecode.py config
# Choose: OpenAI-compatible endpoint
# Base URL: http://localhost:11434/v1   (Ollama example)
# Model ID: llama3.2 (or whichever is loaded)
```

If the endpoint does not support `/models` listing, enter the model ID manually.
Not all compatible servers support tool calling — REW falls back to a non-tool
completion loop when tool calls are unavailable.

---

## Multiple providers

You can configure multiple providers and switch between them:

```bash
# Add a second provider
python3 ccopecode.py config

# Set a different default
# (via Settings → default_provider in the interactive menu)
```

Per-project provider is recorded at project creation time and used on resume.

---

## Fallback model

Configure an optional fallback in `~/.config/rew/config.json`:

```json
{
  "providers": {
    "openrouter": {
      "type": "openrouter",
      "name": "openrouter",
      "model_id": "anthropic/claude-3.5-sonnet",
      "fallback_model_id": "anthropic/claude-3-haiku"
    }
  }
}
```

The fallback is used only when the primary model hits a rate limit and the task
is compatible with the fallback's capabilities.
