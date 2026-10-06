"""Inspection mode: capture Zoho pages so the selectors can be verified.

Nothing is clicked or changed. You navigate in the browser yourself; for each
page you name, this saves a screenshot, sanitised HTML, the accessibility tree
and a summary of headings/buttons/inputs/table columns (input VALUES are not
recorded). Sign-in pages are never captured.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from playwright.sync_api import Page

from .approval import Console
from .browser import sanitize_html
from .config import Settings
from .logger import get_logger
from .utils import safe_filename, timestamp
from .zoho_selectors import LoginSelectors

_SUMMARY_JS = """
() => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 120);
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const attrs = el => {
    const out = {};
    for (const a of el.attributes) {
      if (a.name === 'value' || /token|csrf|password|session/i.test(a.name)) continue;
      const useful = /^(id|name|type|role|placeholder|aria-label|title|href|for|data-[\\w-]+)$/;
      if (useful.test(a.name)) out[a.name] = a.value.slice(0, 120);
    }
    return out;
  };
  const pick = (sel, limit) => Array.from(document.querySelectorAll(sel)).filter(visible).slice(0, limit)
    .map(el => ({tag: el.tagName.toLowerCase(), text: clean(el.innerText), ...attrs(el)}));
  return {
    title: document.title,
    headings: pick('h1, h2, h3, h4, [role="heading"]', 50),
    buttons: pick('button, [role="button"], input[type="submit"]', 150),
    links: pick('a, [role="link"]', 200),
    inputs: pick('input:not([type="hidden"]), select, textarea, [role="combobox"], [role="textbox"]', 150),
    labels: pick('label', 150),
    tables: Array.from(document.querySelectorAll('table, [role="grid"], [role="table"]')).filter(visible).slice(0, 20)
      .map(t => ({...attrs(t), headers: Array.from(t.querySelectorAll('th, [role="columnheader"]')).map(h => clean(h.innerText)),
                 rows: t.querySelectorAll('tr, [role="row"]').length})),
  };
}
"""


def capture_page(page: Page, settings: Settings, label: str) -> str:
    if LoginSelectors.ACCOUNTS_HOST_FRAGMENT in (urlparse(page.url).hostname or ""):
        return "Not captured: this is a Zoho sign-in page."
    folder = settings.inspection_dir / f"{timestamp()}_{safe_filename(label)}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "url.txt").write_text(page.url, encoding="utf-8")
    page.screenshot(path=str(folder / "screenshot.png"), full_page=True)
    (folder / "page.html").write_text(sanitize_html(page.content()), encoding="utf-8")
    (folder / "aria.txt").write_text(page.locator("body").aria_snapshot(), encoding="utf-8")
    summary = page.evaluate(_SUMMARY_JS)
    (folder / "elements.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"Saved to {folder}"


def run_inspection(page: Page, settings: Settings, console: Console) -> None:
    log = get_logger()
    console.banner("INSPECTION MODE (read only)")
    console.say("In the browser, open each of these Zoho pages one at a time:")
    console.say("  1. Sales > Payments Received (the list)")
    console.say("  2. One payment created by Harish that has an unused amount (detail view)")
    console.say("  3. That payment's Edit screen showing the open invoices - DO NOT SAVE")
    console.say("For each page, type a short name here (e.g. payments_list) and press ENTER.")
    console.say("Press ENTER on an empty line when finished.")
    while True:
        label = console.ask("\nPage name (blank to finish): ").strip()
        if not label:
            break
        try:
            message = capture_page(page, settings, label)
        except Exception as exc:  # noqa: BLE001 - keep inspecting other pages
            message = f"Capture failed: {exc}"
        console.say(message)
        log.info("Inspection capture '%s': %s", label, message)
    console.say(f"\nInspection files are in: {settings.inspection_dir}")
    console.say("They contain your financial data - share them only with the person maintaining this tool.")
