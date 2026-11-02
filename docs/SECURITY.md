# REW Security Model

## Threat model

REW visits potentially untrusted websites on the user's machine. The primary threats are:

1. **Server-side request forgery (SSRF)** — a malicious website redirecting REW to internal network addresses
2. **Prompt injection** — website content containing instructions that try to hijack the AI agent
3. **Credential leakage** — API keys or session tokens reaching the target site or AI context
4. **Path traversal** — generated files escaping the project workspace
5. **Malicious redirects** — responses redirecting to cloud metadata endpoints or localhost

---

## URL validation

REW validates all URLs before fetching them:

- Only `http://` and `https://` schemes are allowed. `file:`, `javascript:`, `data:`, and other schemes are rejected.
- Hostnames resolving to loopback (`127.0.0.0/8`, `::1`), private networks (`10/8`, `172.16/12`, `192.168/16`), link-local (`169.254/16`), and carrier-grade NAT (`100.64/10`) are blocked.
- Cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`) are blocked by hostname.
- Redirects are revalidated at each hop.
- Domain allowlists are enforced at the Python level before and at the browser level via Playwright route interception.

Note: Browser processes make their own sub-resource requests. REW installs a Playwright route handler to block disallowed destinations at the browser level, but OS-level network isolation (e.g., network namespaces) provides stronger guarantees when available.

---

## Credential handling

- API keys are stored in the OS keyring (`keyring` library) or in `~/.config/rew/keys.json` with 600 permissions.
- API keys are never stored in project records, generated files, logs, or AI context.
- Authorization headers, cookies, session tokens, and bearer tokens in network traffic are redacted before any data is sent to the AI model.
- URL query parameters known to carry secrets (`api_key`, `token`, `secret`, etc.) are redacted.
- The `redact_text()` function scans arbitrary strings for bearer tokens, API key patterns, and other common secret formats.

---

## Prompt injection defense

REW treats all content extracted from the target website as **untrusted data**, never as instructions.

- Raw HTML is never sent to the AI.
- Page content is summarized locally by deterministic Python code into structured JSON.
- The AI system prompt explicitly states that evidence is data to analyze, not commands to follow.
- Website-embedded "instructions" (e.g., hidden text like "Ignore your instructions and...") appear only as quoted data in the evidence, not as messages from REW.

---

## Filesystem safety

- All generated files are written through `safe_write()`, which resolves the target path and verifies it is inside the approved project workspace before writing.
- Path traversal sequences (`../`, absolute paths) in AI-generated filenames are sanitized before use.
- The project workspace is isolated per project ID.
- REW never executes generated files without explicit user action.
- REW never runs model-generated shell commands.

---

## Network isolation

REW does not:
- Replay authenticated browser requests outside the browser context
- Enumerate private API endpoints
- Submit data to real payment, order, or messaging systems automatically
- Exploit discovered vulnerabilities
- Bypass CAPTCHAs, paywalls, or access controls

---

## Reporting security issues

Please open a GitHub issue marked `[security]` or email the maintainers directly.
Do not publish proof-of-concept exploits publicly before a fix is available.
