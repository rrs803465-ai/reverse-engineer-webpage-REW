# REW — Reverse Engineer Website

A terminal-based AI agent that analyzes websites, maps their client-side behavior,
and generates working frontend reconstructions.

---

## What REW does

REW launches a real browser (via Playwright), crawls a target website, and:

- Extracts page structure, headings, forms, links, and semantic HTML
- Discovers interactive elements (buttons, inputs, menus, dialogs)
- Records network traffic (sanitized — no credentials)
- Analyzes CSS for design tokens: colors, fonts, breakpoints, variables
- Analyzes JavaScript for framework signals, routes, and API patterns
- Sends compact, structured evidence to an AI model
- Generates a working frontend reconstruction from that evidence
- Tests the reconstruction with a real browser and visual comparison

---

## What REW cannot recover

| Cannot recover | Why |
|---|---|
| Backend source code | Not accessible from HTTP responses |
| Database schemas or content | Private, server-side |
| Authentication logic | Requires server-side access |
| Paywalled or authenticated content | REW does not bypass access controls |
| Private API keys or credentials | Never stored or transmitted |
| Exact pixel-perfect reproduction | Depends on evidence quality and model |

Results depend on the selected model, site complexity, and what is publicly accessible.

---

## Installation

**Requirements:** Python 3.11+, pip

```bash
# Clone the repo
git clone https://github.com/rew-project/rew.git
cd rew

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers (Chromium is the default)
python -m playwright install chromium

# Optional extras (token counting, image comparison, schema validation)
pip install tiktoken Pillow jsonschema
```

---

## Quick start

```bash
# Launch the interactive menu
python3 ccopecode.py

# Or run directly
python3 ccopecode.py scan https://example.com
```

---

## Provider configuration

REW supports OpenRouter, OpenAI, Anthropic, and any OpenAI-compatible endpoint.

### Interactive setup

```bash
python3 ccopecode.py config
```

Follow the prompts. API keys are stored in your OS keyring (via the `keyring` library)
or in `~/.config/rew/keys.json` with permissions set to 600 if keyring is unavailable.

### Environment variables

```bash
export REW_OPENROUTER_API_KEY="your-key-here"
export REW_OPENAI_API_KEY="your-key-here"
export REW_ANTHROPIC_API_KEY="your-key-here"

# For a custom provider named "local":
export REW_LOCAL_API_KEY="your-key-here"
```

### API key security

- Keys are **never** hardcoded
- Keys are **never** printed to terminal (input is masked)
- Keys are **never** included in logs, reports, or generated files
- Keys are **never** sent to the target website
- The OS keyring is preferred; file fallback uses 600 permissions

---

## Model discovery and selection

```bash
# Interactive model selector
python3 ccopecode.py models

# Or during initial setup via the menu
python3 ccopecode.py
```

The model selector shows context window, pricing, and tool-calling support where available.
You can also enter a model ID manually if discovery is unavailable.

---

## Scanning a website

```bash
# Standard scan (up to 10 pages)
python3 ccopecode.py scan https://example.com

# Shallow (homepage only)
python3 ccopecode.py scan https://example.com --depth shallow

# Deep (up to 40 pages)
python3 ccopecode.py scan https://example.com --depth deep

# Scan without reconstruction
python3 ccopecode.py scan https://example.com --no-reconstruct

# Named project
python3 ccopecode.py scan https://example.com --name "My Project"
```

---

## Project management

```bash
# List all projects
python3 ccopecode.py projects

# View a project report
python3 ccopecode.py report <project-id>

# Resume an interrupted scan
python3 ccopecode.py resume <project-id>
```

Projects survive restarts, API failures, and network interruptions.
Resuming skips already-completed pages and tasks.

---

## Token and cost management

```bash
python3 ccopecode.py usage
```

REW tracks:
- Input and output tokens (provider-reported where available, estimated otherwise)
- API request count and retry count
- Estimated cost (based on published pricing — not guaranteed to match your invoice)

### Budget limits

Configure in the interactive settings menu or edit `~/.config/rew/config.json`:

```json
{
  "input_token_budget": 500000,
  "output_token_budget": 100000,
  "cost_budget_usd": 1.00,
  "max_pages": 20,
  "max_iterations": 50,
  "max_tool_calls": 100
}
```

REW stops cleanly when a budget is reached and reports what remains unfinished.

---

## HTTP 429 handling

REW handles rate limits automatically:

- Respects `Retry-After` headers
- Exponential backoff with full jitter
- Configurable retry limit (default: 5)
- Never retries authentication failures
- Never repeats completed browser actions after an API failure
- Reduces concurrency when rate limited

---

## Custom API-compatible endpoints

For local models or self-hosted inference:

```bash
python3 ccopecode.py config
# Choose: OpenAI-compatible endpoint
# Enter base URL: http://localhost:11434/v1
# Enter model ID manually if discovery fails
```

Tested compatible with Ollama, LM Studio, vLLM, and similar OpenAI-compatible servers.

---

## Configuration file

Non-secret settings: `~/.config/rew/config.json`

```json
{
  "default_provider": "openrouter",
  "browser": "chromium",
  "headless": true,
  "viewport_width": 1280,
  "viewport_height": 800,
  "crawl_depth": 3,
  "max_pages": 20,
  "request_timeout_seconds": 30,
  "capture_screenshots": true,
  "output_workspace": "./projects",
  "log_verbosity": 0,
  "allowed_domains": []
}
```

---

## Security

- Private, loopback, and cloud metadata addresses are blocked by default
- `file:`, `javascript:`, and other unsafe URL schemes are rejected
- Domain allowlists are supported (`allowed_domains` setting)
- All network events are sanitized before AI context injection (no credentials)
- Generated files are confined to the project workspace (path traversal blocked)
- Website content is treated as untrusted data — never as instructions
- REW does not bypass CAPTCHAs, authentication, or access controls
- REW does not execute generated code without explicit user action

See [docs/SECURITY.md](docs/SECURITY.md) for the full security model.

---

## Development and testing

```bash
# Run the test suite
pytest tests/ -v

# With coverage
pytest tests/ --cov=rea --cov-report=term-missing

# Run a specific test file
pytest tests/test_url_validation.py -v
```

Tests cover: URL validation, secret redaction, token budgets, SQLite persistence,
CSS/JS analysis, filesystem boundaries, and the tool registry.

Browser tests and provider integration tests require Playwright and real credentials respectively.

---

## Known limitations

- JavaScript-heavy SPAs may not reveal all routes during a passive crawl
- Visual similarity scores are approximate — pixel differences don't always reflect functional quality
- Reconstruction quality depends heavily on the selected model
- Large minified JS bundles are truncated before analysis
- WebSocket traffic is detected but not deeply inspected
- REW does not support authenticated crawls (no login flows)
- tiktoken is optional; token estimates without it use a character heuristic (±15%)

---

## License

MIT — see [LICENSE](LICENSE)
