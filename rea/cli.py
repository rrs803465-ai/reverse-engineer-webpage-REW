"""REW CLI — all commands live here."""

from __future__ import annotations

import asyncio
import getpass
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from rea import __version__
from rea.config import delete_api_key, get_api_key, get_settings, store_api_key
from rea.logging_config import configure_logging
from rea.models import Project, ProjectConfig, ProjectStatus, ProviderConfig, ProviderType, ScanDepth
from rea.security.url_validation import URLValidationError, validate_url
from rea.storage.database import get_db
from rea.storage.projects import ProjectStore
from rea.storage.usage import UsageStore

app = typer.Typer(
    name="rew",
    help="REW — Reverse Engineer Website",
    add_completion=False,
    no_args_is_help=False,
)
console = Console()
err = Console(stderr=True)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_stores() -> tuple[ProjectStore, UsageStore]:
    db = get_db()
    return ProjectStore(db), UsageStore(db)


def _require_provider(settings: any) -> tuple[str, ProviderConfig]:
    default = settings.default_provider()
    if not default:
        err.print("[red]No provider configured. Run:[/red] python3 ccopecode.py config")
        raise typer.Exit(1)
    cfg = settings.get_provider(default)
    if cfg is None:
        err.print(f"[red]Provider '{default}' not found in config.[/red]")
        raise typer.Exit(1)
    if not cfg.model_id:
        err.print("[red]No model selected. Run:[/red] python3 ccopecode.py models")
        raise typer.Exit(1)
    return default, cfg


def _build_provider(name: str, cfg: ProviderConfig):
    from rea.providers.factory import build_provider
    return build_provider(name, cfg)


# ---------------------------------------------------------------------------
# Main interactive menu (no args)
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version."),
    verbose: int = typer.Option(0, "--verbose", "-V", count=True, help="Increase verbosity."),
) -> None:
    configure_logging(verbose)

    if version:
        console.print(f"REW {__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is not None:
        return

    # Interactive menu.
    _interactive_menu()


def _interactive_menu() -> None:
    settings = get_settings()
    project_store, _ = _get_stores()

    while True:
        console.clear()
        provider_name = settings.default_provider() or "[dim]none[/dim]"
        provider_cfg = settings.get_provider(settings.default_provider() or "") if settings.default_provider() else None
        model = (provider_cfg.model_id or "[dim]none[/dim]") if provider_cfg else "[dim]none[/dim]"
        projects = project_store.list_all()

        console.print(Panel(
            f"[bold white]REW — Reverse Engineer Website[/bold white]  [dim]v{__version__}[/dim]\n\n"
            f"  Provider : [cyan]{provider_name}[/cyan]\n"
            f"  Model    : [cyan]{model}[/cyan]\n"
            f"  Projects : [cyan]{len(projects)}[/cyan]",
            border_style="dim",
            width=50,
        ))

        console.print()
        console.print("  [bold]1.[/bold] Configure provider")
        console.print("  [bold]2.[/bold] Select model")
        console.print("  [bold]3.[/bold] Start website analysis")
        console.print("  [bold]4.[/bold] Resume project")
        console.print("  [bold]5.[/bold] Browse projects")
        console.print("  [bold]6.[/bold] Token usage & costs")
        console.print("  [bold]7.[/bold] Settings")
        console.print("  [bold]8.[/bold] Help")
        console.print("  [bold]9.[/bold] Exit")
        console.print()

        choice = Prompt.ask("Choose", choices=["1","2","3","4","5","6","7","8","9"], default="9")

        if choice == "1":
            _menu_configure_provider(settings)
        elif choice == "2":
            _menu_select_model(settings)
        elif choice == "3":
            _menu_start_analysis(settings, project_store)
        elif choice == "4":
            _menu_resume(project_store)
        elif choice == "5":
            _menu_browse_projects(project_store)
        elif choice == "6":
            _menu_usage()
        elif choice == "7":
            _menu_settings(settings)
        elif choice == "8":
            _show_help()
        elif choice == "9":
            raise typer.Exit()


def _menu_configure_provider(settings: any) -> None:
    console.print("\n[bold]Configure Provider[/bold]\n")
    console.print("  1. OpenRouter")
    console.print("  2. OpenAI")
    console.print("  3. Anthropic")
    console.print("  4. OpenAI-compatible endpoint")
    console.print("  5. Back")
    choice = Prompt.ask("Provider type", choices=["1","2","3","4","5"], default="5")
    if choice == "5":
        return

    type_map = {"1": ProviderType.OPENROUTER, "2": ProviderType.OPENAI,
                "3": ProviderType.ANTHROPIC, "4": ProviderType.COMPATIBLE}
    ptype = type_map[choice]

    name = Prompt.ask("Provider label", default=ptype.value)
    api_key = getpass.getpass(f"API key for {name} (hidden): ")
    if not api_key:
        console.print("[yellow]No key entered — cancelled.[/yellow]")
        return

    base_url = None
    if ptype == ProviderType.COMPATIBLE:
        base_url = Prompt.ask("Base URL (e.g. http://localhost:11434/v1)")

    cfg = ProviderConfig(type=ptype, name=name, base_url=base_url)
    store_api_key(name, api_key)
    settings.save_provider(name, cfg)
    settings.set_default_provider(name)

    console.print(f"[green]Provider '{name}' saved and set as default.[/green]")

    if Confirm.ask("Test connection now?", default=True):
        asyncio.run(_test_provider_async(name, cfg))

    Prompt.ask("\nPress Enter to continue")


def _menu_select_model(settings: any) -> None:
    provider_name = settings.default_provider()
    if not provider_name:
        console.print("[red]No provider configured.[/red]")
        Prompt.ask("Press Enter")
        return

    cfg = settings.get_provider(provider_name)
    if cfg is None:
        return

    console.print(f"\n[bold]Select Model — {provider_name}[/bold]\n")
    console.print("[dim]Fetching model list...[/dim]")

    models = asyncio.run(_fetch_models_async(provider_name, cfg))
    if not models:
        console.print("[yellow]No models returned. Enter model ID manually.[/yellow]")
        model_id = Prompt.ask("Model ID")
        if model_id:
            cfg.model_id = model_id
            settings.save_provider(provider_name, cfg)
        return

    # Display searchable list.
    search = Prompt.ask("Search models (leave blank to list all)", default="")
    filtered = [m for m in models if not search or search.lower() in m.id.lower()][:25]

    if not filtered:
        console.print("[yellow]No models matched.[/yellow]")
        filtered = models[:25]

    table = Table(title="Available Models", box=box.SIMPLE)
    table.add_column("#", style="dim", width=4)
    table.add_column("ID")
    table.add_column("Context")
    table.add_column("Input $/Mtok")
    table.add_column("Output $/Mtok")
    table.add_column("Tools")

    for i, m in enumerate(filtered, 1):
        ctx = str(m.context_window) if m.context_window else "?"
        inp = f"${m.input_price_per_mtok:.2f}" if m.input_price_per_mtok is not None else "?"
        out = f"${m.output_price_per_mtok:.2f}" if m.output_price_per_mtok is not None else "?"
        tools = "✓" if m.supports_tools else ("?" if m.supports_tools is None else "✗")
        table.add_row(str(i), m.id, ctx, inp, out, tools)

    console.print(table)

    idx = Prompt.ask(f"Select (1-{len(filtered)}) or enter model ID manually", default="")
    try:
        n = int(idx)
        if 1 <= n <= len(filtered):
            cfg.model_id = filtered[n - 1].id
            settings.save_provider(provider_name, cfg)
            console.print(f"[green]Model set: {cfg.model_id}[/green]")
        else:
            console.print("[red]Invalid selection.[/red]")
    except ValueError:
        if idx:
            cfg.model_id = idx
            settings.save_provider(provider_name, cfg)
            console.print(f"[green]Model set: {cfg.model_id}[/green]")

    Prompt.ask("\nPress Enter to continue")


def _menu_start_analysis(settings: any, project_store: ProjectStore) -> None:
    console.print("\n[bold]Start Website Analysis[/bold]\n")
    url = Prompt.ask("Target URL")
    try:
        url = validate_url(url)
    except URLValidationError as exc:
        console.print(f"[red]Invalid URL: {exc}[/red]")
        Prompt.ask("Press Enter")
        return

    name = Prompt.ask("Project name", default=_url_to_name(url))

    depth_choice = Prompt.ask(
        "Scan depth",
        choices=["shallow", "standard", "deep"],
        default="standard",
    )
    depth = ScanDepth(depth_choice)
    max_pages = {"shallow": 1, "standard": 10, "deep": 40}[depth_choice]

    provider_name, cfg = _require_provider(settings)

    project = Project(
        name=name,
        target_url=url,
        provider_name=provider_name,
        model_id=cfg.model_id,
        config=ProjectConfig(scan_depth=depth, max_pages=max_pages),
        workspace_path=str(Path(settings.get("output_workspace", "projects")) / ""),
    )
    project_store.create(project)
    console.print(f"\n[green]Project created:[/green] {project.id}")

    if Confirm.ask("Start scanning now?", default=True):
        asyncio.run(_run_scan(project, cfg, provider_name, settings))

    Prompt.ask("\nPress Enter to continue")


def _menu_resume(project_store: ProjectStore) -> None:
    projects = project_store.list_all()
    if not projects:
        console.print("[dim]No projects found.[/dim]")
        Prompt.ask("Press Enter")
        return

    _print_projects_table(projects)
    project_id = Prompt.ask("Project ID to resume (or blank to cancel)", default="")
    if not project_id:
        return

    project = project_store.get(project_id)
    if not project:
        console.print("[red]Project not found.[/red]")
        Prompt.ask("Press Enter")
        return

    settings = get_settings()
    pname = project.provider_name or settings.default_provider()
    if not pname:
        console.print("[red]No provider associated with this project.[/red]")
        Prompt.ask("Press Enter")
        return
    cfg = settings.get_provider(pname)
    if not cfg:
        console.print(f"[red]Provider '{pname}' not found.[/red]")
        Prompt.ask("Press Enter")
        return

    asyncio.run(_run_scan(project, cfg, pname, settings))
    Prompt.ask("\nPress Enter to continue")


def _menu_browse_projects(project_store: ProjectStore) -> None:
    projects = project_store.list_all()
    if not projects:
        console.print("[dim]No projects.[/dim]")
    else:
        _print_projects_table(projects)
    Prompt.ask("\nPress Enter to continue")


def _menu_usage() -> None:
    _, usage_store = _get_stores()
    summary = usage_store.summary()
    if not summary:
        console.print("[dim]No usage recorded yet.[/dim]")
    else:
        _print_usage_panel(summary)
    Prompt.ask("\nPress Enter to continue")


def _menu_settings(settings: any) -> None:
    console.print("\n[bold]Settings[/bold]\n")
    all_settings = settings.all()
    table = Table(box=box.SIMPLE)
    table.add_column("Key")
    table.add_column("Value")
    skip = {"providers"}
    for k, v in all_settings.items():
        if k not in skip:
            table.add_row(k, str(v))
    console.print(table)

    key = Prompt.ask("\nKey to change (or blank)", default="")
    if key and key in all_settings and key not in skip:
        current = all_settings[key]
        val = Prompt.ask(f"New value for {key}", default=str(current))
        # Basic type coercion.
        try:
            if isinstance(current, bool):
                settings.set(key, val.lower() in ("true", "1", "yes"))
            elif isinstance(current, int):
                settings.set(key, int(val))
            elif isinstance(current, float):
                settings.set(key, float(val))
            elif current is None:
                settings.set(key, val if val else None)
            else:
                settings.set(key, val)
            console.print(f"[green]{key} updated.[/green]")
        except ValueError:
            console.print("[red]Invalid value.[/red]")

    Prompt.ask("\nPress Enter to continue")


def _show_help() -> None:
    console.print(Panel("""
[bold]REW — Reverse Engineer Website[/bold]

REW inspects websites using a real browser, maps their structure and
client-side behavior, then uses AI to generate a working reconstruction.

[bold]Commands:[/bold]
  python3 ccopecode.py              Interactive menu
  python3 ccopecode.py config       Configure an AI provider
  python3 ccopecode.py models       List and select models
  python3 ccopecode.py scan URL     Scan a website
  python3 ccopecode.py projects     List all projects
  python3 ccopecode.py resume ID    Resume a project
  python3 ccopecode.py report ID    View a project report
  python3 ccopecode.py usage        Token & cost summary
  python3 ccopecode.py test-provider  Test current provider

[bold]What REW can recover:[/bold]
  ✓ Page structure and layout
  ✓ Observable client-side interactions
  ✓ Design tokens (colors, fonts, spacing)
  ✓ Responsive breakpoints
  ✓ Discovered routes and navigation
  ✓ Form structures
  ✓ Observable API endpoint patterns

[bold]What REW cannot recover:[/bold]
  ✗ Private backend source code
  ✗ Server-side business logic
  ✗ Database schemas or content
  ✗ Credentials or secrets
  ✗ Authenticated or paywalled content
    """, title="Help", border_style="dim"))
    Prompt.ask("\nPress Enter to continue")


# ---------------------------------------------------------------------------
# CLI subcommands
# ---------------------------------------------------------------------------

@app.command("config")
def cmd_config() -> None:
    """Configure an AI provider interactively."""
    settings = get_settings()
    _menu_configure_provider(settings)


@app.command("models")
def cmd_models() -> None:
    """List and select a model for the current provider."""
    settings = get_settings()
    project_store, _ = _get_stores()
    _menu_select_model(settings)


@app.command("scan")
def cmd_scan(
    url: str = typer.Argument(..., help="Target website URL."),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Project name."),
    depth: str = typer.Option("standard", "--depth", "-d", help="shallow|standard|deep"),
    no_reconstruct: bool = typer.Option(False, "--no-reconstruct", help="Skip reconstruction."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True),
) -> None:
    """Scan a website and optionally reconstruct it."""
    configure_logging(verbose)
    settings = get_settings()
    project_store, _ = _get_stores()

    try:
        url = validate_url(url)
    except URLValidationError as exc:
        err.print(f"[red]Invalid URL: {exc}[/red]")
        raise typer.Exit(1)

    provider_name, cfg = _require_provider(settings)

    scan_depth = ScanDepth(depth) if depth in ("shallow", "standard", "deep") else ScanDepth.STANDARD
    max_pages = {"shallow": 1, "standard": 10, "deep": 40}.get(depth, 10)

    project = Project(
        name=name or _url_to_name(url),
        target_url=url,
        provider_name=provider_name,
        model_id=cfg.model_id,
        config=ProjectConfig(scan_depth=scan_depth, max_pages=max_pages),
        workspace_path=str(Path(settings.get("output_workspace", "projects"))),
    )
    project_store.create(project)
    console.print(f"[green]Project:[/green] {project.id}  [dim]{project.name}[/dim]")

    asyncio.run(_run_scan(project, cfg, provider_name, settings, reconstruct=not no_reconstruct))


@app.command("projects")
def cmd_projects() -> None:
    """List all projects."""
    project_store, _ = _get_stores()
    projects = project_store.list_all()
    if not projects:
        console.print("[dim]No projects yet.[/dim]")
        return
    _print_projects_table(projects)


@app.command("resume")
def cmd_resume(
    project_id: str = typer.Argument(..., help="Project ID."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True),
) -> None:
    """Resume a paused or interrupted project."""
    configure_logging(verbose)
    settings = get_settings()
    project_store, _ = _get_stores()

    project = project_store.get(project_id)
    if not project:
        err.print(f"[red]Project '{project_id}' not found.[/red]")
        raise typer.Exit(1)

    pname = project.provider_name or settings.default_provider()
    cfg = settings.get_provider(pname or "")
    if not cfg:
        err.print("[red]Provider not found.[/red]")
        raise typer.Exit(1)

    asyncio.run(_run_scan(project, cfg, pname, settings))


@app.command("report")
def cmd_report(
    project_id: str = typer.Argument(..., help="Project ID."),
) -> None:
    """Display a project scan report."""
    project_store, usage_store = _get_stores()
    project = project_store.get(project_id)
    if not project:
        err.print(f"[red]Project '{project_id}' not found.[/red]")
        raise typer.Exit(1)

    db = get_db()
    from rea.storage.projects import ProjectStore as PS
    ps = PS(db)
    pages = ps.get_pages(project_id)
    actions = ps.get_actions(project_id)
    events = ps.get_network_events(project_id)
    assets = ps.get_assets(project_id)

    console.print(Panel(
        f"[bold]{project.name}[/bold]\n"
        f"URL: {project.target_url}\n"
        f"Status: [cyan]{project.status.value}[/cyan]\n"
        f"Model: {project.model_id or '?'}\n"
        f"Created: {project.created_at.strftime('%Y-%m-%d %H:%M')}",
        title=f"Project {project.id}",
    ))
    console.print(f"\n  Pages scanned : {len(pages)}")
    console.print(f"  Actions found : {len(actions)}")
    console.print(f"  Network events: {len(events)}")
    console.print(f"  Assets indexed: {len(assets)}")

    if pages:
        table = Table(title="Pages", box=box.SIMPLE)
        table.add_column("URL")
        table.add_column("Title")
        table.add_column("Status")
        for p in pages[:20]:
            table.add_row(p.url, p.title or "?", str(p.status_code or "?"))
        console.print(table)

    usage = usage_store.summary(project_id)
    if usage:
        _print_usage_panel(usage, title=f"Usage — {project.id}")


@app.command("usage")
def cmd_usage() -> None:
    """Show global token usage and cost summary."""
    _, usage_store = _get_stores()
    summary = usage_store.summary()
    if not summary:
        console.print("[dim]No usage recorded yet.[/dim]")
        return
    _print_usage_panel(summary)


@app.command("test-provider")
def cmd_test_provider(
    provider: Optional[str] = typer.Option(None, "--provider", "-p"),
) -> None:
    """Test provider connectivity and authentication."""
    settings = get_settings()
    name = provider or settings.default_provider()
    if not name:
        err.print("[red]No provider configured.[/red]")
        raise typer.Exit(1)
    cfg = settings.get_provider(name)
    if not cfg:
        err.print(f"[red]Provider '{name}' not found.[/red]")
        raise typer.Exit(1)
    asyncio.run(_test_provider_async(name, cfg))


# ---------------------------------------------------------------------------
# Async scan pipeline
# ---------------------------------------------------------------------------

async def _run_scan(
    project: Project,
    provider_cfg: ProviderConfig,
    provider_name: str,
    settings: any,
    reconstruct: bool = True,
) -> None:
    """Full scan pipeline: browse → analyze → reconstruct → test."""
    from rea.agent.orchestrator import AgentOrchestrator
    from rea.agent.task_state import TaskState
    from rea.agent.token_budget import TokenBudget
    from rea.analysis.asset_index import index_assets, merge_design_tokens
    from rea.analysis.evidence import build_project_summary
    from rea.agent.context import ContextManager
    from rea.agent.tool_registry import ToolRegistry
    from rea.browser.crawler import crawl
    from rea.browser.manager import BrowserManager
    from rea.reconstruction.generator import ReconstructionGenerator
    from rea.reconstruction.tester import test_reconstruction
    from rea.reconstruction.workspace import Workspace
    from rea.storage.database import get_db

    db = get_db()
    project_store = ProjectStore(db)
    usage_store = UsageStore(db)
    task_state = TaskState(db, project.id)

    project_store.update_status(project.id, ProjectStatus.SCANNING)

    budget = TokenBudget(
        max_input_tokens=project.config.input_token_budget,
        max_output_tokens=project.config.output_token_budget,
        max_cost_usd=project.config.cost_budget_usd,
        max_iterations=project.config.max_iterations,
        max_tool_calls=project.config.max_tool_calls,
    )

    # Determine workspace.
    ws_root = Path(settings.get("output_workspace", "projects"))
    workspace = Workspace(ws_root, project.id)
    project.workspace_path = str(workspace.root)
    project_store.update(project)

    screenshot_dir = workspace.path("screenshots")
    asset_cache_dir = workspace.path("assets")

    # ----------------------------------------------------------------
    # Phase 1: Browser crawl
    # ----------------------------------------------------------------
    console.print(f"\n[bold]Phase 1:[/bold] Crawling {project.target_url} ...")

    already_visited = project_store.page_urls(project.id)
    max_new = project.config.max_pages - len(already_visited)

    pages_collected = project_store.get_pages(project.id)
    actions_collected = project_store.get_actions(project.id)
    events_collected = project_store.get_network_events(project.id)

    if max_new > 0:
        async with BrowserManager(
            headless=project.config.headless,
            viewport_width=project.config.viewport_width,
            viewport_height=project.config.viewport_height,
            request_timeout_ms=project.config.request_timeout_seconds * 1000,
            allowed_domains=project.config.allowed_domains or [],
        ) as bm:
            ctx = await bm.new_context()
            crawl_result = await crawl(
                context=ctx,
                start_url=project.target_url,
                max_pages=max_new,
                max_depth=3,
                screenshot_dir=screenshot_dir,
                full_page_screenshots=project.config.full_page_screenshots,
                already_visited=already_visited,
                discover_page_actions=True,
            )

        # Persist.
        for page in crawl_result.pages:
            project_store.upsert_page(project.id, page)
        for action in crawl_result.actions:
            project_store.save_action(project.id, action)
        project_store.save_network_events(project.id, crawl_result.network_events)

        pages_collected.extend(crawl_result.pages)
        actions_collected.extend(crawl_result.actions)
        events_collected.extend(crawl_result.network_events)

        console.print(f"  Crawled {len(crawl_result.pages)} pages, "
                      f"{len(crawl_result.actions)} actions, "
                      f"{len(crawl_result.network_events)} network events.")
        if crawl_result.errors:
            for e in crawl_result.errors[:3]:
                console.print(f"  [yellow]⚠[/yellow] {e}")
    else:
        console.print(f"  [dim]Using {len(pages_collected)} previously scanned pages.[/dim]")

    # ----------------------------------------------------------------
    # Phase 2: Asset analysis
    # ----------------------------------------------------------------
    console.print("\n[bold]Phase 2:[/bold] Indexing assets ...")
    project_store.update_status(project.id, ProjectStatus.ANALYZING)

    existing_assets = project_store.get_assets(project.id)
    existing_hashes = {a.url: a.content_hash for a in existing_assets if a.content_hash}

    # Collect CSS and JS URLs from pages.
    from rea.analysis.html_parser import parse_html
    css_urls: list[str] = []
    js_urls: list[str] = []
    for page in pages_collected:
        # We don't have raw HTML here, but links are already extracted.
        pass

    # Collect from network events.
    for ev in events_collected:
        url_lower = ev.url.lower()
        if ".css" in url_lower:
            css_urls.append(ev.url)
        elif ".js" in url_lower and "chunk" not in url_lower:
            js_urls.append(ev.url)

    all_asset_urls = list(set(css_urls + js_urls))[:30]

    assets_collected, analysis_map = await index_assets(
        all_asset_urls, asset_cache_dir, existing_hashes
    )
    for asset in assets_collected:
        project_store.upsert_asset(project.id, asset)

    css_analyses = [v for k, v in analysis_map.items() if k in css_urls and v]
    js_analyses = [v for k, v in analysis_map.items() if k in js_urls and v]
    design_tokens = merge_design_tokens(css_analyses)

    console.print(f"  {len(assets_collected)} assets indexed, {len(css_analyses)} CSS, {len(js_analyses)} JS analyzed.")

    # ----------------------------------------------------------------
    # Phase 3: AI analysis
    # ----------------------------------------------------------------
    console.print("\n[bold]Phase 3:[/bold] AI analysis ...")
    project_store.update_status(project.id, ProjectStatus.ANALYZING)

    provider = _build_provider(provider_name, provider_cfg)
    all_assets_db = project_store.get_assets(project.id)

    async with provider:
        ctx_mgr = ContextManager(
            project, pages_collected, actions_collected, events_collected,
            all_assets_db, design_tokens, js_analyses,
        )

        # Build tool registry.
        registry = _build_tool_registry(
            project, project_store, pages_collected, actions_collected, events_collected
        )

        orchestrator = AgentOrchestrator(
            provider=provider,
            registry=registry,
            context=ctx_mgr,
            budget=budget,
            task_state=task_state,
            usage_store=usage_store,
            project_id=project.id,
        )

        analysis_result = await orchestrator.run_task(
            task_type="site_analysis",
            task_description=(
                "Analyze the scanned website. Summarize: "
                "1) overall structure and purpose, "
                "2) key interactive features, "
                "3) technical stack indicators, "
                "4) any gaps in the scan data that should be investigated. "
                "Be concise and factual."
            ),
            max_tokens=1024,
            use_tools=False,
        )

        if analysis_result.get("text"):
            console.print(Panel(analysis_result["text"], title="AI Analysis", border_style="dim"))

        # ----------------------------------------------------------------
        # Phase 4: Reconstruction
        # ----------------------------------------------------------------
        if reconstruct:
            console.print("\n[bold]Phase 4:[/bold] Generating reconstruction ...")
            project_store.update_status(project.id, ProjectStatus.GENERATING)

            generator = ReconstructionGenerator(
                provider=provider,
                project=project,
                workspace=workspace,
                pages=pages_collected,
                actions=actions_collected,
                network_events=events_collected,
                assets=all_assets_db,
                design_tokens=design_tokens,
                js_analysis=js_analyses,
                budget=budget,
                project_store=project_store,
                usage_store=usage_store,
            )

            try:
                written = await generator.generate()
                console.print(f"  [green]Generated {len(written)} file(s):[/green]")
                for f in written:
                    console.print(f"    {workspace.root / f}")
            except Exception as exc:
                console.print(f"  [red]Generation error:[/red] {exc}")

            # ----------------------------------------------------------------
            # Phase 5: Testing
            # ----------------------------------------------------------------
            console.print("\n[bold]Phase 5:[/bold] Testing reconstruction ...")
            project_store.update_status(project.id, ProjectStatus.TESTING)

            try:
                report = await test_reconstruction(
                    workspace_path=workspace.root,
                    original_pages=pages_collected,
                    project_id=project.id,
                    viewport_width=project.config.viewport_width,
                    viewport_height=project.config.viewport_height,
                )
                console.print(report.summary())
            except Exception as exc:
                console.print(f"  [yellow]Testing skipped:[/yellow] {exc}")

    project_store.update_status(project.id, ProjectStatus.COMPLETE)
    console.print(f"\n[green]✓ Project {project.id} complete.[/green]")
    console.print(f"  Output: {workspace.root}")

    # Usage summary.
    usage = usage_store.summary(project.id)
    if usage:
        _print_usage_panel(usage, title="Usage this run")


def _build_tool_registry(
    project: "Project",
    project_store: "ProjectStore",
    pages: list,
    actions: list,
    events: list,
) -> "ToolRegistry":
    """Register the approved tools the agent can invoke."""
    from rea.agent.tool_registry import ToolRegistry

    registry = ToolRegistry()

    # Tool: get_page_summary
    async def _get_page_summary(args: dict) -> dict:
        url = args["url"]
        page = next((p for p in pages if p.url == url), None)
        if not page:
            return {"error": f"Page not found: {url}"}
        page_actions = [a for a in actions if a.page_url == url]
        return {
            "url": page.url,
            "title": page.title,
            "headings": page.headings[:10],
            "form_count": len(page.forms),
            "link_count": len(page.links),
            "action_count": len(page_actions),
            "console_errors": page.console_errors[:5],
        }

    registry.register(
        name="get_page_summary",
        description="Get a structured summary of a specific scanned page.",
        parameters_schema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The page URL."}},
            "required": ["url"],
        },
        handler=_get_page_summary,
    )

    # Tool: list_pages
    async def _list_pages(args: dict) -> dict:
        return {"pages": [{"url": p.url, "title": p.title, "status": p.status_code} for p in pages]}

    registry.register(
        name="list_pages",
        description="List all pages discovered during the crawl.",
        parameters_schema={"type": "object", "properties": {}},
        handler=_list_pages,
    )

    # Tool: list_actions
    async def _list_actions(args: dict) -> dict:
        page_url = args.get("page_url")
        filtered = [a for a in actions if not page_url or a.page_url == page_url]
        return {
            "actions": [
                {
                    "id": a.id, "page": a.page_url, "type": a.element_type,
                    "label": a.label, "confidence": a.confidence.value,
                    "result": a.observed_result,
                }
                for a in filtered[:50]
            ]
        }

    registry.register(
        name="list_actions",
        description="List discovered interactive actions, optionally filtered by page URL.",
        parameters_schema={
            "type": "object",
            "properties": {
                "page_url": {"type": "string", "description": "Optional page URL filter."}
            },
        },
        handler=_list_actions,
    )

    # Tool: list_api_calls
    async def _list_api_calls(args: dict) -> dict:
        api = [e for e in events if e.is_xhr]
        return {
            "api_calls": [
                {"method": e.method, "url": e.url, "status": e.status, "type": e.content_type}
                for e in api[:40]
            ]
        }

    registry.register(
        name="list_api_calls",
        description="List observed XHR/fetch API calls from the crawl.",
        parameters_schema={"type": "object", "properties": {}},
        handler=_list_api_calls,
    )

    return registry


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------

async def _test_provider_async(name: str, cfg: ProviderConfig) -> None:
    provider = _build_provider(name, cfg)
    console.print(f"[dim]Testing {name} ({cfg.type.value}) ...[/dim]")
    try:
        async with provider:
            ok = await provider.validate_connection()
            if ok:
                console.print(f"[green]✓ {name}: connection successful.[/green]")
            else:
                console.print(f"[red]✗ {name}: connection failed.[/red]")
    except Exception as exc:
        console.print(f"[red]✗ {name}: {exc}[/red]")


async def _fetch_models_async(name: str, cfg: ProviderConfig) -> list:
    provider = _build_provider(name, cfg)
    try:
        async with provider:
            return await provider.list_models()
    except Exception as exc:
        err.print(f"[red]Model fetch failed: {exc}[/red]")
        return []


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _print_projects_table(projects: list[Project]) -> None:
    table = Table(title="Projects", box=box.SIMPLE)
    table.add_column("ID", style="dim")
    table.add_column("Name")
    table.add_column("URL")
    table.add_column("Status")
    table.add_column("Updated")
    for p in projects:
        status_color = {
            "complete": "green", "scanning": "cyan",
            "error": "red", "paused": "yellow",
        }.get(p.status.value, "white")
        table.add_row(
            p.id, p.name, p.target_url[:50],
            f"[{status_color}]{p.status.value}[/{status_color}]",
            p.updated_at.strftime("%m-%d %H:%M"),
        )
    console.print(table)


def _print_usage_panel(summary: dict, title: str = "Token Usage") -> None:
    inp = summary.get("total_input_tokens", 0)
    out = summary.get("total_output_tokens", 0)
    cost = summary.get("estimated_cost_usd", 0.0)
    reqs = summary.get("total_api_requests", 0)
    retries = summary.get("total_retried_requests", 0)

    console.print(Panel(
        f"  Provider : {summary.get('provider', '?')}\n"
        f"  Model    : {summary.get('model_id', '?')}\n\n"
        f"  Input tokens  : {inp:,}\n"
        f"  Output tokens : {out:,}\n"
        f"  API requests  : {reqs}  (retried: {retries})\n\n"
        f"  Est. cost     : ${cost:.6f}",
        title=title,
        border_style="dim",
    ))


def _url_to_name(url: str) -> str:
    from urllib.parse import urlparse
    host = urlparse(url).hostname or url
    return host.replace("www.", "")[:40]
