"""Tests for local CSS and JS analysis."""

import pytest
from rea.analysis.css_analyzer import analyze_css
from rea.analysis.js_analyzer import analyze_js
from rea.analysis.html_parser import parse_html


def test_css_extracts_variables():
    css = """
    :root {
        --primary-color: #3b82f6;
        --font-size-base: 16px;
        --spacing-md: 1rem;
    }
    """
    result = analyze_css(css)
    assert "--primary-color" in result["css_variables"]
    assert result["css_variables"]["--primary-color"] == "#3b82f6"
    assert "--spacing-md" in result["css_variables"]


def test_css_extracts_colors():
    css = """
    body { background: #ffffff; color: #1a1a1a; }
    .btn { background: rgb(59, 130, 246); }
    """
    result = analyze_css(css)
    assert "#ffffff" in result["colors"]
    assert "#1a1a1a" in result["colors"]


def test_css_extracts_fonts():
    css = """
    @font-face { font-family: 'Inter'; src: url('/fonts/inter.woff2'); }
    body { font-family: 'Inter', sans-serif; }
    """
    result = analyze_css(css)
    assert "Inter" in result["font_families"]


def test_css_extracts_breakpoints():
    css = """
    @media (min-width: 768px) { .container { width: 750px; } }
    @media (max-width: 1200px) { .sidebar { display: none; } }
    """
    result = analyze_css(css)
    assert 768 in result["breakpoints_px"]
    assert 1200 in result["breakpoints_px"]


def test_css_detects_flex_grid():
    css = "main { display: flex; } .grid { display: grid; }"
    result = analyze_css(css)
    assert result["uses_flex"] is True
    assert result["uses_grid"] is True


def test_js_detects_react():
    js = "import React from 'react'; const App = () => { return React.createElement('div'); };"
    result = analyze_js(js)
    assert "react" in result["frameworks"]


def test_js_extracts_routes():
    js = """
    const routes = [
        { path: '/about', component: About },
        { path: '/products/:id', component: Product },
    ];
    """
    result = analyze_js(js)
    assert "/about" in result["routes"]
    assert "/products/:id" in result["routes"]


def test_js_detects_minified():
    # Minified JS has very long lines.
    js = "a" * 500 + ";" + "b" * 500
    result = analyze_js(js)
    assert result["is_minified"] is True


def test_js_not_minified():
    js = "\n".join(f"const var{i} = {i};" for i in range(20))
    result = analyze_js(js)
    assert result["is_minified"] is False


def test_js_extracts_api_endpoints():
    js = """
    const data = await fetch('https://api.example.com/v1/users');
    """
    result = analyze_js(js)
    assert "https://api.example.com/v1/users" in result["api_endpoints"]


def test_html_parser_title():
    html = "<html><head><title>Test Page</title></head><body></body></html>"
    result = parse_html(html)
    assert result["title"] == "Test Page"


def test_html_parser_headings():
    html = "<h1>Main</h1><h2>Sub</h2><h3>Sub-sub</h3>"
    result = parse_html(html)
    assert any("Main" in h for h in result["headings"])
    assert any("Sub" in h for h in result["headings"])


def test_html_parser_links():
    html = '<a href="/about">About</a><a href="https://external.com">External</a>'
    result = parse_html(html, base_url="https://example.com")
    assert any("about" in link for link in result["links"])


def test_html_parser_forms():
    html = """
    <form action="/submit" method="post">
        <input type="text" name="username" placeholder="Username">
        <input type="password" name="password">
        <button type="submit">Login</button>
    </form>
    """
    result = parse_html(html)
    assert len(result["forms"]) == 1
    assert result["forms"][0]["method"] == "POST"
    assert len(result["forms"][0]["fields"]) == 2


def test_html_semantic_structure():
    html = "<header></header><nav></nav><main><article></article></main><footer></footer>"
    result = parse_html(html)
    assert "header" in result["semantic_structure"]
    assert "nav" in result["semantic_structure"]
    assert "footer" in result["semantic_structure"]
