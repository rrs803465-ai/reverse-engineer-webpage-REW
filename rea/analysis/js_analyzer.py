"""JavaScript analysis: extract routes, component names, API patterns — locally.

Never sends entire JS bundles to the AI. Extracts bounded excerpts and symbols.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

# Route patterns (React Router, Vue Router, Next.js, etc.)
_ROUTE_PATTERNS = [
    re.compile(r"""path\s*:\s*['"`]([/][\w/:*-]*)['"`]"""),
    re.compile(r"""<Route[^>]+path\s*=\s*['"`]([/][\w/:*-]*)['"`]"""),
    re.compile(r"""router\.(?:get|post|put|delete|patch)\(['"`]([/][\w/:*-]*)['"`]"""),
    re.compile(r"""['"`](\/[\w-]+(?:\/[\w:*-]+)*)['"`]\s*:\s*(?:function|\()"""),
]

# API endpoint patterns
_API_PATTERNS = [
    re.compile(r"""fetch\(['"`](https?://[^'"` ]+)['"`]"""),
    re.compile(r"""axios\.\w+\(['"`](https?://[^'"` ]+|\/[\w/.-]+)['"`]"""),
    re.compile(r"""(?:baseURL|baseUrl)\s*[:=]\s*['"`]([^'"` ]+)['"`]"""),
    re.compile(r"""url\s*:\s*['"`](\/api\/[\w/.-]+)['"`]"""),
]

# Component/class names (PascalCase identifiers in JSX/function context)
_COMPONENT_PATTERN = re.compile(r"""(?:function|class|const|let)\s+([A-Z][A-Za-z0-9]+)\s*[=(]""")

# Environment variable references
_ENV_PATTERN = re.compile(r"""process\.env\.([A-Z_][A-Z0-9_]*)""")
_VITE_ENV = re.compile(r"""import\.meta\.env\.([A-Z_][A-Z0-9_]*)""")

# Framework detection
_FRAMEWORK_SIGNALS: dict[str, list[str]] = {
    "react": ["React.createElement", "useState", "useEffect", "jsx", "ReactDOM"],
    "vue": ["createApp", "defineComponent", "ref(", "reactive(", "Vue."],
    "angular": ["@Component", "NgModule", "@Injectable", "platformBrowserDynamic"],
    "svelte": ["$:", "onMount(", "onDestroy(", "writable("],
    "next.js": ["getServerSideProps", "getStaticProps", "NextPage", "__NEXT_DATA__"],
    "nuxt": ["defineNuxtConfig", "useNuxtApp", "__nuxt"],
    "remix": ["LoaderFunction", "ActionFunction", "useLoaderData"],
    "htmx": ["hx-get", "hx-post", "htmx."],
    "alpine.js": ["x-data", "x-bind", "x-on", "Alpine."],
}


def analyze_js(js: str, url: str = "") -> dict[str, Any]:
    """Extract bounded, useful signals from a JS bundle."""
    if len(js) > 2_000_000:
        # Truncate very large bundles to avoid spending time on minified noise.
        js = js[:2_000_000]
        truncated = True
    else:
        truncated = False

    content_hash = hashlib.sha256(js.encode()).hexdigest()[:16]

    return {
        "url": url,
        "content_hash": content_hash,
        "size_bytes": len(js.encode()),
        "truncated": truncated,
        "frameworks": _detect_frameworks(js),
        "routes": _extract_routes(js),
        "api_endpoints": _extract_api_endpoints(js),
        "components": _extract_components(js),
        "env_vars_referenced": _extract_env_vars(js),
        "is_minified": _is_minified(js),
        "has_sourcemap": "//# sourceMappingURL=" in js,
        "line_count": js.count("\n"),
    }


def _detect_frameworks(js: str) -> list[str]:
    detected = []
    for fw, signals in _FRAMEWORK_SIGNALS.items():
        if any(sig in js for sig in signals):
            detected.append(fw)
    return detected


def _extract_routes(js: str) -> list[str]:
    seen: set[str] = set()
    routes: list[str] = []
    for pattern in _ROUTE_PATTERNS:
        for match in pattern.finditer(js):
            route = match.group(1)
            if route and route not in seen and len(route) < 100:
                seen.add(route)
                routes.append(route)
    return sorted(routes)[:50]


def _extract_api_endpoints(js: str) -> list[str]:
    seen: set[str] = set()
    endpoints: list[str] = []
    for pattern in _API_PATTERNS:
        for match in pattern.finditer(js):
            ep = match.group(1)
            if ep and ep not in seen and len(ep) < 200:
                seen.add(ep)
                endpoints.append(ep)
    return endpoints[:30]


def _extract_components(js: str) -> list[str]:
    names = list(set(_COMPONENT_PATTERN.findall(js)))
    # Filter out common non-component PascalCase (Promise, Error, etc.)
    skip = {"Promise", "Error", "Array", "Object", "String", "Number",
            "Boolean", "Symbol", "Map", "Set", "Date", "Math", "JSON",
            "Event", "Function", "RegExp", "TypeError", "RangeError"}
    return [n for n in names if n not in skip][:40]


def _extract_env_vars(js: str) -> list[str]:
    found = set(_ENV_PATTERN.findall(js)) | set(_VITE_ENV.findall(js))
    # Exclude keys that commonly carry secrets — just surface the names.
    return sorted(found)[:20]


def _is_minified(js: str) -> bool:
    """Heuristic: minified JS has very long lines."""
    lines = js.split("\n")
    if not lines:
        return False
    avg_len = sum(len(l) for l in lines) / len(lines)
    return avg_len > 200
