"""Client-side action discovery and safe interaction testing."""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Page

from rea.models import ActionConfidence, DiscoveredAction

log = logging.getLogger(__name__)

# Actions that can mutate external state — require explicit user confirmation.
_DESTRUCTIVE_TYPES = {"submit", "delete", "pay", "order", "publish", "send", "confirm"}


async def discover_actions(page: Page, page_url: str) -> list[DiscoveredAction]:
    """Enumerate all interactive elements on the current page."""
    actions: list[DiscoveredAction] = []

    # Buttons.
    actions.extend(await _discover_buttons(page, page_url))

    # Links with meaningful hrefs.
    actions.extend(await _discover_links(page, page_url))

    # Inputs and text fields.
    actions.extend(await _discover_inputs(page, page_url))

    # Select dropdowns.
    actions.extend(await _discover_selects(page, page_url))

    # Checkboxes and radio buttons.
    actions.extend(await _discover_toggles(page, page_url))

    log.debug("Discovered %d actions on %s", len(actions), page_url)
    return actions


async def test_action_safe(
    page: Page,
    action: DiscoveredAction,
) -> DiscoveredAction:
    """Perform a safe, reversible test of *action* and record the result.

    Only reversible interactions are attempted automatically.
    Destructive actions are recorded but not executed.
    """
    label_lower = (action.label or "").lower()
    if any(d in label_lower for d in _DESTRUCTIVE_TYPES):
        action.notes = "Skipped: potentially destructive action."
        action.confidence = ActionConfidence.LOW
        return action

    el_type = action.element_type
    selector = action.selector

    if not selector:
        return action

    try:
        initial_url = page.url

        if el_type == "button" and selector:
            await _test_button(page, action, initial_url)
        elif el_type == "input_text" and selector:
            await _test_text_input(page, action)
        elif el_type == "select" and selector:
            await _test_select(page, action)
        elif el_type == "link" and selector:
            # Don't navigate; just record.
            action.confidence = ActionConfidence.MEDIUM
            action.observed_result = "Navigation link (not followed during action test)"

    except Exception as exc:
        log.debug("Action test failed for %s: %s", action.id, exc)
        action.confidence = ActionConfidence.LOW
        action.notes = f"Test error: {str(exc)[:100]}"

    return action


async def _test_button(page: Page, action: DiscoveredAction, initial_url: str) -> None:
    el = await page.query_selector(action.selector or "")
    if not el:
        return

    action.initial_state = "button visible"
    await el.click(timeout=3000)
    await page.wait_for_timeout(600)

    new_url = page.url
    action.url_changed = new_url != initial_url

    if action.url_changed:
        action.observed_result = f"Navigated to {new_url}"
        # Go back.
        await page.go_back()
        await page.wait_for_timeout(500)
    else:
        # Check for visible DOM changes — new elements or dialogs.
        dialog_visible = await page.evaluate("""
            () => {
                const dialog = document.querySelector('[role="dialog"],[aria-modal="true"],.modal,.dialog');
                return dialog ? dialog.textContent.trim().slice(0, 80) : null;
            }
        """)
        if dialog_visible:
            action.observed_result = f"Dialog appeared: {dialog_visible}"
            action.confidence = ActionConfidence.HIGH
            # Try to close.
            try:
                close = await page.query_selector('[aria-label="Close"],[data-dismiss],[class*="close"]')
                if close:
                    await close.click()
                    await page.wait_for_timeout(300)
            except Exception:
                pass
        else:
            action.observed_result = "Clicked — no navigation or dialog detected"
            action.confidence = ActionConfidence.MEDIUM

    action.confidence = ActionConfidence.HIGH if action.url_changed or "Dialog" in (action.observed_result or "") else ActionConfidence.MEDIUM


async def _test_text_input(page: Page, action: DiscoveredAction) -> None:
    el = await page.query_selector(action.selector or "")
    if not el:
        return

    action.initial_state = "empty"
    await el.fill("test")
    await page.wait_for_timeout(400)

    suggestions = await page.evaluate("""
        () => {
            const list = document.querySelector('[role="listbox"],[role="combobox"] ul,.autocomplete,.suggestions');
            return list ? list.textContent.trim().slice(0, 100) : null;
        }
    """)
    if suggestions:
        action.observed_result = f"Autocomplete appeared: {suggestions[:80]}"
        action.confidence = ActionConfidence.HIGH
    else:
        action.observed_result = "Text entered — no autocomplete observed"
        action.confidence = ActionConfidence.MEDIUM

    # Clear the test input.
    await el.fill("")


async def _test_select(page: Page, action: DiscoveredAction) -> None:
    el = await page.query_selector(action.selector or "")
    if not el:
        return

    options = await page.evaluate(
        "el => Array.from(el.options).map(o => o.text).slice(0, 5)",
        el,
    )
    action.initial_state = f"options: {options}"
    action.observed_result = "Select element — options enumerated"
    action.confidence = ActionConfidence.HIGH


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

async def _discover_buttons(page: Page, page_url: str) -> list[DiscoveredAction]:
    try:
        items = await page.evaluate("""
            () => Array.from(document.querySelectorAll(
                'button, [role="button"], input[type="button"], input[type="submit"], input[type="reset"]'
            )).map((el, i) => ({
                label: el.textContent?.trim() || el.value || el.getAttribute('aria-label') || '',
                selector: `button:nth-of-type(${i+1})`,
                aria_label: el.getAttribute('aria-label'),
                id: el.id || null,
                type: el.type || null,
            })).filter(e => e.label).slice(0, 50)
        """)
        results = []
        for i, item in enumerate(items):
            sel = f'button:nth-of-type({i+1})' if not item.get("id") else f'#{item["id"]}'
            results.append(DiscoveredAction(
                page_url=page_url,
                element_type="button",
                label=item["label"][:100],
                selector=sel,
                aria_label=item.get("aria_label"),
            ))
        return results
    except Exception as exc:
        log.debug("Button discovery failed: %s", exc)
        return []


async def _discover_links(page: Page, page_url: str) -> list[DiscoveredAction]:
    try:
        items = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                label: a.textContent.trim() || a.getAttribute('aria-label') || '',
                href: a.href,
                id: a.id || null,
            })).filter(a => a.label && !a.href.startsWith('javascript:')).slice(0, 80)
        """)
        results = []
        for item in items:
            sel = f'#{item["id"]}' if item.get("id") else f'a[href="{item["href"]}"]'
            results.append(DiscoveredAction(
                page_url=page_url,
                element_type="link",
                label=item["label"][:100],
                selector=sel[:200],
            ))
        return results
    except Exception as exc:
        log.debug("Link discovery failed: %s", exc)
        return []


async def _discover_inputs(page: Page, page_url: str) -> list[DiscoveredAction]:
    try:
        items = await page.evaluate("""
            () => Array.from(document.querySelectorAll(
                'input[type="text"], input[type="search"], input[type="email"], input[type="number"], textarea'
            )).map(el => {
                const label = (() => {
                    if (el.id) {
                        const lbl = document.querySelector(`label[for="${el.id}"]`);
                        if (lbl) return lbl.textContent.trim();
                    }
                    return el.placeholder || el.getAttribute('aria-label') || el.name || '';
                })();
                return {label, id: el.id || null, name: el.name || null, type: el.type || 'text'};
            }).filter(e => e.label).slice(0, 30)
        """)
        results = []
        for item in items:
            sel = f'#{item["id"]}' if item.get("id") else (
                f'input[name="{item["name"]}"]' if item.get("name") else f'input[type="{item["type"]}"]'
            )
            results.append(DiscoveredAction(
                page_url=page_url,
                element_type="input_text",
                label=item["label"][:100],
                selector=sel[:200],
            ))
        return results
    except Exception as exc:
        log.debug("Input discovery failed: %s", exc)
        return []


async def _discover_selects(page: Page, page_url: str) -> list[DiscoveredAction]:
    try:
        items = await page.evaluate("""
            () => Array.from(document.querySelectorAll('select')).map(el => {
                const label = (() => {
                    if (el.id) {
                        const lbl = document.querySelector(`label[for="${el.id}"]`);
                        if (lbl) return lbl.textContent.trim();
                    }
                    return el.getAttribute('aria-label') || el.name || 'select';
                })();
                return {label, id: el.id || null, name: el.name || null};
            }).slice(0, 20)
        """)
        results = []
        for item in items:
            sel = f'#{item["id"]}' if item.get("id") else (
                f'select[name="{item["name"]}"]' if item.get("name") else "select"
            )
            results.append(DiscoveredAction(
                page_url=page_url,
                element_type="select",
                label=item["label"][:100],
                selector=sel[:200],
            ))
        return results
    except Exception as exc:
        log.debug("Select discovery failed: %s", exc)
        return []


async def _discover_toggles(page: Page, page_url: str) -> list[DiscoveredAction]:
    try:
        items = await page.evaluate("""
            () => Array.from(document.querySelectorAll('input[type="checkbox"],input[type="radio"]')).map(el => {
                const label = (() => {
                    if (el.id) {
                        const lbl = document.querySelector(`label[for="${el.id}"]`);
                        if (lbl) return lbl.textContent.trim();
                    }
                    return el.name || el.type;
                })();
                return {label, id: el.id || null, type: el.type};
            }).filter(e => e.label).slice(0, 30)
        """)
        results = []
        for item in items:
            sel = f'#{item["id"]}' if item.get("id") else f'input[type="{item["type"]}"]'
            results.append(DiscoveredAction(
                page_url=page_url,
                element_type=item["type"],
                label=item["label"][:100],
                selector=sel[:200],
            ))
        return results
    except Exception as exc:
        log.debug("Toggle discovery failed: %s", exc)
        return []
