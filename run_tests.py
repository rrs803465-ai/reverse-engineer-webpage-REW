#!/usr/bin/env python3
"""
REW self-contained test runner — no pytest required.
Runs all tests that depend only on stdlib + installed packages.
"""

import asyncio
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS = 0
FAIL = 0
ERRORS = []


def chk(label: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}")
        FAIL += 1
        ERRORS.append(label)


def raises(fn, exc_type):
    try:
        result = fn()
        if asyncio.iscoroutine(result):
            asyncio.get_event_loop().run_until_complete(result)
        return False
    except exc_type:
        return True
    except Exception:
        return False


def section(name: str) -> None:
    print(f"\n{'─'*50}")
    print(f"  {name}")
    print(f"{'─'*50}")


# ─────────────────────────────────────────────────
# URL Validation
# ─────────────────────────────────────────────────
section("URL Validation")
from rea.security.url_validation import (
    URLValidationError, is_same_origin, normalize_url, validate_url,
)

def blocks(url):
    return raises(lambda: validate_url(url, resolve_dns=False), URLValidationError)

def allows(url):
    try:
        validate_url(url, resolve_dns=False)
        return True
    except URLValidationError:
        return False

chk("allows https://example.com",         allows("https://example.com"))
chk("allows http://example.com/path",     allows("http://example.com/path?q=1"))
chk("allows public IP 8.8.8.8",           allows("https://8.8.8.8/"))
chk("blocks file://",                      blocks("file:///etc/passwd"))
chk("blocks javascript:",                  blocks("javascript:alert(1)"))
chk("blocks 127.0.0.1",                   blocks("http://127.0.0.1/"))
chk("blocks 10.0.0.1",                    blocks("http://10.0.0.1/"))
chk("blocks 192.168.1.1",                 blocks("http://192.168.1.1/"))
chk("blocks 172.16.0.1",                  blocks("http://172.16.0.1/"))
chk("blocks 169.254.169.254",             blocks("http://169.254.169.254/"))
chk("blocks [::1] IPv6",                  blocks("http://[::1]/"))
chk("blocks fc00:: private IPv6",         blocks("http://[fc00::1]/"))
chk("allowed domain pass",                allows("https://sub.example.com/"))
chk("allowed domain block",               raises(
    lambda: validate_url("https://evil.com/", allowed_domains=["example.com"], resolve_dns=False),
    URLValidationError))
chk("same origin true",                   is_same_origin("https://ex.com/a", "https://ex.com/b"))
chk("same origin false",                  not is_same_origin("https://ex.com", "https://other.com"))
chk("normalize strips utm",               "utm_source" not in normalize_url("https://ex.com/?utm_source=g&id=5"))
chk("normalize keeps other params",       "id=5" in normalize_url("https://ex.com/?utm_source=g&id=5"))
chk("normalize removes fragment",         "#" not in normalize_url("https://ex.com/page#section"))

# ─────────────────────────────────────────────────
# Redaction
# ─────────────────────────────────────────────────
section("Redaction")
from rea.security.redaction import redact_dict, redact_headers, redact_text, redact_url

R = "[REDACTED]"
h = redact_headers({"Authorization": "Bearer sk-abc123", "Content-Type": "application/json"})
chk("auth header redacted",       h["Authorization"] == R)
chk("content-type kept",          h["Content-Type"] == "application/json")
chk("cookie redacted",            redact_headers({"Cookie": "s=abc"})["Cookie"] == R)

u = redact_url("https://api.example.com/v1/data?api_key=secret123&page=2")
chk("api_key stripped from url",  "secret123" not in u)
chk("page param kept in url",     "page=2" in u)

t = redact_text("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
chk("bearer stripped from text",  "eyJ" not in t)
chk("REDACTED inserted",          R in t)
chk("clean text unchanged",       redact_text("Hello world.") == "Hello world.")

d = redact_dict({"config": {"api_key": "supersecret", "timeout": 30}, "name": "test"})
chk("nested api_key redacted",    d["config"]["api_key"] == R)
chk("timeout int kept",           d["config"]["timeout"] == 30)
chk("name string kept",           d["name"] == "test")

d2 = redact_dict([{"token": "abc"}, {"value": "safe"}])
chk("list token redacted",        d2[0]["token"] == R)
chk("list safe value kept",       d2[1]["value"] == "safe")

# ─────────────────────────────────────────────────
# Filesystem Safety
# ─────────────────────────────────────────────────
section("Filesystem Safety")
from rea.security.filesystem import WorkspaceBoundaryError, enforce_workspace, safe_write

with tempfile.TemporaryDirectory() as tmp:
    ws = Path(tmp) / "workspace"
    ws.mkdir()

    chk("path inside workspace",
        enforce_workspace(ws / "out.html", ws) is not None)
    chk("nested path inside workspace",
        enforce_workspace(ws / "a" / "b" / "c.js", ws) is not None)
    chk("blocks path traversal",
        raises(lambda: enforce_workspace(ws / ".." / ".." / "etc" / "passwd", ws),
               WorkspaceBoundaryError))
    chk("blocks absolute escape",
        raises(lambda: enforce_workspace(Path("/etc/passwd"), ws),
               WorkspaceBoundaryError))

    p = safe_write(ws / "sub" / "test.html", "<html/>", ws)
    chk("safe_write creates file",     p.exists())
    chk("safe_write content correct",  p.read_text() == "<html/>")
    chk("safe_write blocks escape",
        raises(lambda: safe_write(Path(tmp) / "escaped.txt", "bad", ws),
               WorkspaceBoundaryError))

# ─────────────────────────────────────────────────
# Token Budget
# ─────────────────────────────────────────────────
section("Token Budget")
from rea.agent.token_budget import BudgetExceededError, TokenBudget

b = TokenBudget(max_input_tokens=1000, max_output_tokens=500,
                max_api_requests=3, max_cost_usd=0.01,
                max_iterations=2, max_tool_calls=3)

chk("initial used_input=0",        b.used_input == 0)
chk("remaining_input=1000",        b.remaining_input() == 1000)

b.record_request(100, 50, cost_usd=0.001)
chk("record_request: input",       b.used_input == 100)
chk("record_request: output",      b.used_output == 50)
chk("remaining after record",      b.remaining_input() == 900)

chk("blocks input over budget",
    raises(lambda: b.check_input(950), BudgetExceededError))
chk("allows input under budget",   (b.check_input(100), True)[1])
chk("blocks output over budget",
    raises(lambda: b.check_output(460), BudgetExceededError))

b.record_request(1, 1); b.record_request(1, 1)
chk("blocks too many requests",    raises(b.check_requests, BudgetExceededError))

b2 = TokenBudget(max_cost_usd=0.01)
b2.record_request(0, 0, cost_usd=0.009)
chk("blocks cost over limit",      raises(lambda: b2.check_cost(0.002), BudgetExceededError))

b3 = TokenBudget(max_cost_usd=None)
b3.check_cost(999.0)  # must not raise
chk("no cost limit: unlimited",    True)

b4 = TokenBudget(max_iterations=2)
b4.record_iteration(); b4.record_iteration()
chk("blocks iterations",           raises(b4.check_iterations, BudgetExceededError))

b5 = TokenBudget(max_tool_calls=2)
b5.record_tool_call(); b5.record_tool_call()
chk("blocks tool calls",           raises(b5.check_tool_calls, BudgetExceededError))

s = b.summary()
chk("summary has input_tokens",    "input_tokens" in s)
chk("summary has cost",            "estimated_cost_usd" in s)

# ─────────────────────────────────────────────────
# Tool Registry
# ─────────────────────────────────────────────────
section("Tool Registry")
from rea.agent.tool_registry import ToolCallError, ToolRegistry

reg = ToolRegistry()

async def echo_handler(args):
    return {"echo": args.get("message", "")}

reg.register("echo", "Echo a message.",
    {"type": "object",
     "properties": {"message": {"type": "string", "description": "msg"}},
     "required": ["message"]},
    echo_handler)

async def _async_raises(coro_fn, exc_type):
    try:
        await coro_fn()
        return False
    except exc_type:
        return True

async def _tool_tests():
    r = await reg.invoke("echo", {"message": "hello"})
    chk("invoke returns correct value",  r == {"echo": "hello"})
    chk("unknown tool raises ToolCallError",
        await _async_raises(lambda: reg.invoke("nope", {}), ToolCallError))
    chk("missing required arg raises",
        await _async_raises(lambda: reg.invoke("echo", {}), ToolCallError))

asyncio.run(_tool_tests())

s = reg.to_openai_schemas()
chk("openai schema type=function",   s[0]["type"] == "function")
chk("openai schema name",            s[0]["function"]["name"] == "echo")
chk("openai schema has properties",  "message" in s[0]["function"]["parameters"]["properties"])

a = reg.to_anthropic_schemas()
chk("anthropic schema name",         a[0]["name"] == "echo")
chk("anthropic has input_schema",    "input_schema" in a[0])
chk("names list contains echo",      "echo" in reg.names())

# ─────────────────────────────────────────────────
# CSS Analysis
# ─────────────────────────────────────────────────
section("CSS Analysis")
from rea.analysis.css_analyzer import analyze_css

css = """
:root { --primary: #3b82f6; --gap: 1rem; }
body { font-family: "Inter", sans-serif; background: #ffffff; color: #1a1a1a; }
@font-face { font-family: "Inter"; src: url("/inter.woff2"); }
@media (min-width: 768px) { .container { max-width: 750px; } }
@media (max-width: 1200px) { .sidebar { display: none; } }
main { display: flex; }
.grid { display: grid; }
.btn { background: rgb(59,130,246); }
"""
r = analyze_css(css)
chk("css var --primary extracted",    "--primary" in r["css_variables"])
chk("css var value correct",          r["css_variables"]["--primary"] == "#3b82f6")
chk("hex color #ffffff extracted",    "#ffffff" in r["colors"])
chk("hex color #1a1a1a extracted",    "#1a1a1a" in r["colors"])
chk("font Inter extracted",           "Inter" in r["font_families"])
chk("breakpoint 768px",               768 in r["breakpoints_px"])
chk("breakpoint 1200px",              1200 in r["breakpoints_px"])
chk("detects flex layout",            r["uses_flex"] is True)
chk("detects grid layout",            r["uses_grid"] is True)
chk("has content hash",               len(r["content_hash"]) > 0)

# ─────────────────────────────────────────────────
# JS Analysis
# ─────────────────────────────────────────────────
section("JS Analysis")
from rea.analysis.js_analyzer import analyze_js

js_react = "const [count, setCount] = useState(0); useEffect(() => {}, []); React.createElement('div');"
r = analyze_js(js_react)
chk("detects react framework",        "react" in r["frameworks"])

js_routes = """
const routes = [
  { path: "/about", component: About },
  { path: "/products/:id", component: Product },
];
"""
r2 = analyze_js(js_routes)
chk("extracts /about route",          "/about" in r2["routes"])
chk("extracts /products/:id route",   "/products/:id" in r2["routes"])

js_api = 'const data = await fetch("https://api.example.com/v1/users");'
r3 = analyze_js(js_api)
chk("extracts api endpoint",          "https://api.example.com/v1/users" in r3["api_endpoints"])

r4 = analyze_js("a" * 600)
chk("detects minified JS",            r4["is_minified"] is True)

r5 = analyze_js("\n".join(f"const x{i} = {i};" for i in range(30)))
chk("non-minified JS",                r5["is_minified"] is False)

chk("has_sourcemap false",            not analyze_js("normal code")["has_sourcemap"])
chk("has_sourcemap true",             analyze_js("code\n//# sourceMappingURL=app.js.map")["has_sourcemap"])

# ─────────────────────────────────────────────────
# HTML Parser
# ─────────────────────────────────────────────────
section("HTML Parser")
from rea.analysis.html_parser import parse_html

html = """<!DOCTYPE html>
<html><head>
  <title>Test Page</title>
  <meta name="description" content="A test page">
</head><body>
  <header></header><nav></nav>
  <main>
    <h1>Main Heading</h1>
    <h2>Sub Heading</h2>
    <a href="/about">About</a>
    <a href="https://external.com">External</a>
    <form action="/submit" method="post">
      <input type="text" name="username" placeholder="Username">
      <input type="password" name="password">
      <button type="submit">Login</button>
    </form>
  </main>
  <footer></footer>
</body></html>"""

r = parse_html(html, base_url="https://example.com")
chk("title extracted",                r["title"] == "Test Page")
chk("h1 heading extracted",           any("Main Heading" in h for h in r["headings"]))
chk("h2 heading extracted",           any("Sub Heading" in h for h in r["headings"]))
chk("about link extracted",           any("about" in l for l in r["links"]))
chk("form extracted",                 len(r["forms"]) == 1)
chk("form method POST",               r["forms"][0]["method"] == "POST")
chk("form fields count >= 2",         len(r["forms"][0]["fields"]) >= 2)
chk("header landmark",                "header" in r["semantic_structure"])
chk("nav landmark",                   "nav" in r["semantic_structure"])
chk("footer landmark",                "footer" in r["semantic_structure"])
chk("meta description",               r["meta"].get("description") == "A test page")
chk("has content hash",               len(r["content_hash"]) > 0)

# ─────────────────────────────────────────────────
# SQLite Storage
# ─────────────────────────────────────────────────
section("SQLite Storage")
from rea.storage.database import Database
from rea.storage.projects import ProjectStore
from rea.storage.usage import UsageStore
from rea.models import (
    Project, ProjectStatus, PageInfo, DiscoveredAction,
    NetworkEvent, AssetInfo, UsageRecord,
)

with tempfile.TemporaryDirectory() as tmp:
    db = Database(Path(tmp) / "test.db")
    db.connect()
    ps = ProjectStore(db)
    us = UsageStore(db)

    # Projects
    p = Project(id="proj001", name="Test", target_url="https://example.com")
    ps.create(p)
    got = ps.get("proj001")
    chk("create + get project",        got is not None and got.name == "Test")
    chk("project not found → None",    ps.get("nonexistent") is None)

    ps.update_status("proj001", ProjectStatus.SCANNING)
    chk("update_status persists",      ps.get("proj001").status == ProjectStatus.SCANNING)

    for i in range(3):
        ps.create(Project(name=f"P{i}", target_url=f"https://ex{i}.com"))
    chk("list_all returns 4",          len(ps.list_all()) == 4)

    # Pages
    page = PageInfo(url="https://example.com/about", title="About",
                    status_code=200, headings=["h1: About"], links=["https://example.com"])
    ps.upsert_page("proj001", page)
    pages = ps.get_pages("proj001")
    chk("upsert_page persists",        len(pages) == 1)
    chk("page title correct",          pages[0].title == "About")
    chk("page headings correct",       pages[0].headings == ["h1: About"])

    page.title = "About Updated"
    ps.upsert_page("proj001", page)
    chk("upsert is idempotent",        len(ps.get_pages("proj001")) == 1)
    chk("upsert updates title",        ps.get_pages("proj001")[0].title == "About Updated")

    ps.upsert_page("proj001", PageInfo(url="https://example.com/home"))
    urls = ps.page_urls("proj001")
    chk("page_urls contains about",    "https://example.com/about" in urls)
    chk("page_urls contains home",     "https://example.com/home" in urls)

    # Actions
    a = DiscoveredAction(id="A001", page_url="https://example.com/",
                         element_type="button", label="Submit")
    ps.save_action("proj001", a)
    acts = ps.get_actions("proj001")
    chk("save_action persists",        len(acts) == 1)
    chk("action label correct",        acts[0].label == "Submit")

    # Network events
    evts = [
        NetworkEvent(method="GET",  url="https://api.ex.com/v1/data", status=200, is_xhr=True),
        NetworkEvent(method="POST", url="https://api.ex.com/v1/sub",  status=201, is_xhr=True),
    ]
    ps.save_network_events("proj001", evts)
    stored = ps.get_network_events("proj001")
    chk("save network events count",   len(stored) == 2)
    chk("is_xhr flag persisted",       all(e.is_xhr for e in stored))

    # Assets
    asset = AssetInfo(url="https://example.com/style.css", asset_type="css",
                      file_size_bytes=1024, content_hash="abc123",
                      colors=["#ff0000"], fonts=["Inter"])
    ps.upsert_asset("proj001", asset)
    assets = ps.get_assets("proj001")
    chk("upsert_asset persists",       len(assets) == 1)
    chk("asset colors correct",        "#ff0000" in assets[0].colors)
    chk("asset fonts correct",         "Inter" in assets[0].fonts)

    # Idempotent asset upsert
    asset.file_size_bytes = 2048
    ps.upsert_asset("proj001", asset)
    chk("asset upsert idempotent",     len(ps.get_assets("proj001")) == 1)

    # Generated files manifest
    ps.record_generated_file("proj001", "index.html", "main page", "h001")
    ps.record_generated_file("proj001", "styles.css", "stylesheet", "h002")
    files = ps.get_generated_files("proj001")
    chk("generated files count",       len(files) == 2)
    paths = [f["relative_path"] for f in files]
    chk("index.html in manifest",      "index.html" in paths)
    chk("styles.css in manifest",      "styles.css" in paths)

    # Usage
    rec = UsageRecord(provider="openai", model_id="gpt-4o",
                      project_id="proj001", input_tokens=500, output_tokens=100)
    us.record(rec)
    s = us.summary("proj001")
    chk("usage input tokens",          s["total_input_tokens"] == 500)
    chk("usage output tokens",         s["total_output_tokens"] == 100)
    chk("usage requests",              s["total_api_requests"] == 1)
    chk("cost estimated (gpt-4o)",     s["estimated_cost_usd"] is not None and s["estimated_cost_usd"] > 0)

    # gpt-4o: $2.50/Mtok input + $10/Mtok output
    # 500 input = $0.00125, 100 output = $0.001 → total ≈ $0.00225
    chk("cost value reasonable",       0.001 < s["estimated_cost_usd"] < 0.01)

    ps.delete("proj001")
    chk("delete removes project",      ps.get("proj001") is None)

    db.close()

# ─────────────────────────────────────────────────
# Task State
# ─────────────────────────────────────────────────
section("Task State")
from rea.agent.task_state import TaskState

with tempfile.TemporaryDirectory() as tmp:
    db = Database(Path(tmp) / "tasks.db")
    db.connect()
    ps2 = ProjectStore(db)
    proj = Project(id="tproj", name="Task Test", target_url="https://example.com")
    ps2.create(proj)

    ts = TaskState(db, "tproj")
    task_id = ts.create_task("site_analysis", {"url": "https://example.com"})
    chk("create_task returns id",      len(task_id) > 0)

    ts.record_tool_call(task_id, "list_pages", {}, {"pages": []})
    t = ts.get_task(task_id)
    chk("task status is running",      t["status"] == "running")
    chk("tool call recorded",          len(t["tool_calls"]) == 1)
    chk("tool name correct",           t["tool_calls"][0]["tool"] == "list_pages")

    ts.complete_task(task_id, {"summary": "done"})
    chk("complete_task sets status",   ts.get_task(task_id)["status"] == "complete")

    ts2_id = ts.create_task("reconstruction", {})
    ts.fail_task(ts2_id, "provider error")
    chk("fail_task sets error status", ts.get_task(ts2_id)["status"] == "error")
    chk("fail_task sets message",      "provider error" in ts.get_task(ts2_id)["error"])

    done_types = ts.completed_task_types()
    chk("completed_task_types",        "site_analysis" in done_types)
    chk("failed not in completed",     "reconstruction" not in done_types)

    db.close()

# ─────────────────────────────────────────────────
# Reconstruction Workspace
# ─────────────────────────────────────────────────
section("Reconstruction Workspace")
from rea.reconstruction.workspace import Workspace

with tempfile.TemporaryDirectory() as tmp:
    ws = Workspace(Path(tmp), "proj_abc")

    ws.write("index.html", "<html><body>Hello</body></html>")
    chk("write creates file",          ws.exists("index.html"))
    chk("read returns content",        "Hello" in ws.read("index.html"))

    ws.write("js/app.js", "console.log('hi');")
    chk("write creates nested dirs",   ws.exists("js/app.js"))

    files = ws.list_files()
    chk("list_files returns files",    len(files) >= 2)

    h = ws.content_hash("index.html")
    chk("content_hash non-empty",      len(h) > 0)

    bak = ws.backup_if_exists("index.html")
    chk("backup created",              bak is not None and bak.exists())

    rel = ws.relative(ws.root / "index.html")
    chk("relative path correct",       rel == "index.html")

# ─────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────
total = PASS + FAIL
print(f"\n{'═'*50}")
print(f"  Results: {PASS}/{total} passed", end="")
if FAIL:
    print(f"  —  {FAIL} FAILED")
    print("\n  Failed tests:")
    for e in ERRORS:
        print(f"    • {e}")
else:
    print("  — all passed ✓")
print(f"{'═'*50}\n")

sys.exit(0 if FAIL == 0 else 1)
