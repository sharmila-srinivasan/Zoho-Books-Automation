"""Generic, selector-free page reading helpers used by the page objects.

* ``read_table`` finds a table by its column headings and returns cell text
  mapped by heading - columns are never located by position guesses.
* ``read_labeled_text`` reads the value shown next to a visible label.
"""

from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Locator, Page

from .exceptions import ZohoUIChangedError
from .utils import normalize_header

ROW_ATTRIBUTE = "data-zpr-row"
_CELL_SELECTOR = ":scope > td, :scope > [role='gridcell'], :scope > [role='cell']"

# Must normalise exactly like utils.normalize_header.
_READ_TABLE_JS = """
(args) => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toUpperCase().replace(/[^A-Z0-9# ]/g, '').trim();
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  document.querySelectorAll('[' + args.rowAttr + ']').forEach(el => el.removeAttribute(args.rowAttr));
  const rows = Array.from(document.querySelectorAll('tr, [role="row"]')).filter(visible);
  const headerCells = r => Array.from(r.querySelectorAll(':scope > th, :scope > [role="columnheader"]'));
  const dataCells = r => Array.from(r.querySelectorAll(":scope > td, :scope > [role='gridcell'], :scope > [role='cell']"));
  const seen = [];
  const matches = [];
  rows.forEach((row, index) => {
    const cells = headerCells(row);
    if (!cells.length) return;
    const texts = cells.map(c => norm(c.innerText));
    seen.push(texts);
    const map = {};
    for (const [key, aliases] of Object.entries(args.columns)) {
      const i = texts.findIndex(t => aliases.includes(t));
      if (i >= 0) map[key] = i;
    }
    if (args.required.every(k => k in map)) matches.push({index, texts, map, count: cells.length});
  });
  if (matches.length !== 1) return {error: matches.length ? 'multiple' : 'not_found', seen};
  const m = matches[0];
  const out = [];
  let skipped = 0;
  for (let j = m.index + 1; j < rows.length; j++) {
    if (headerCells(rows[j]).length) break;
    const cells = dataCells(rows[j]);
    if (!cells.length) continue;
    if (cells.length !== m.count) { skipped++; continue; }
    rows[j].setAttribute(args.rowAttr, String(out.length));
    out.push(cells.map(c => {
      const inputs = c.querySelectorAll('input:not([type="checkbox"]):not([type="hidden"])');
      return {text: clean(c.innerText), inputs: inputs.length, value: inputs.length === 1 ? inputs[0].value : null};
    }));
  }
  return {headers: m.texts, map: m.map, rows: out, skipped};
}
"""

_READ_LABELED_JS = """
(aliases) => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim().replace(/[:*]\\s*$/, '').trim().toUpperCase();
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const valueOf = el => {
    if (!el) return '';
    const input = el.matches('input, textarea, select') ? el : el.querySelector('input:not([type="hidden"]), textarea, select');
    if (input && input.value) return input.tagName === 'SELECT' ? input.options[input.selectedIndex]?.text || '' : input.value;
    return el.innerText;
  };
  const found = [];
  for (const el of document.querySelectorAll('label, span, div, dt, th, td, p, strong, b, h4, h5, h6')) {
    // A label never contains the field itself; wrappers around label+field are skipped.
    if (el.children.length > 2 || !visible(el) || el.querySelector('input, select, textarea')) continue;
    if (!aliases.includes(norm(el.innerText))) continue;
    let value = clean(valueOf(el.nextElementSibling));
    if (!value && el.parentElement) value = clean(valueOf(el.parentElement.nextElementSibling));
    if (value) found.push(value);
  }
  return Array.from(new Set(found));
}
"""


@dataclass(frozen=True)
class Cell:
    text: str
    input_count: int
    input_value: str | None


@dataclass(frozen=True)
class TableData:
    headers: list[str]
    column_index: dict[str, int]
    rows: list[list[Cell]]
    skipped_rows: int

    def cell(self, row: list[Cell], key: str) -> Cell | None:
        index = self.column_index.get(key)
        return row[index] if index is not None else None

    def text(self, row: list[Cell], key: str) -> str:
        cell = self.cell(row, key)
        return cell.text if cell else ""


def read_table(page: Page, columns: dict[str, tuple[str, ...]], required: tuple[str, ...], what: str) -> TableData:
    """Find exactly one table whose headings include every required column."""
    args = {
        "columns": {k: [normalize_header(a) for a in v] for k, v in columns.items()},
        "required": list(required),
        "rowAttr": ROW_ATTRIBUTE,
    }
    data = page.evaluate(_READ_TABLE_JS, args)
    if "error" in data:
        problem = "was not found" if data["error"] == "not_found" else "matched more than one table"
        raise ZohoUIChangedError(
            f"The {what} table {problem}. Required columns: {list(required)}. "
            f"Column headings seen on the page: {data.get('seen')}"
        )
    rows = [[Cell(c["text"], c["inputs"], c["value"]) for c in row] for row in data["rows"]]
    return TableData(data["headers"], data["map"], rows, data["skipped"])


def row_locator(page: Page, row_index: int) -> Locator:
    """The row tagged by the most recent read_table call."""
    return page.locator(f"[{ROW_ATTRIBUTE}='{row_index}']")


def cell_locator(page: Page, row_index: int, column_index: int) -> Locator:
    # Column index comes from matching the heading text, not from a guess.
    return row_locator(page, row_index).locator(_CELL_SELECTOR).nth(column_index)


def read_labeled_values(page: Page, labels: tuple[str, ...]) -> list[str]:
    """Every distinct value shown next to a visible label (a label may appear more than once)."""
    aliases = [" ".join(label.split()).rstrip(":*").strip().upper() for label in labels]
    return page.evaluate(_READ_LABELED_JS, aliases)


def read_labeled_text(page: Page, labels: tuple[str, ...], what: str) -> str | None:
    """Value shown next to a visible label. None if absent; error if contradictory."""
    values = read_labeled_values(page, labels)
    if not values:
        return None
    if len(values) > 1:
        raise ZohoUIChangedError(f"{what}: several different values found next to {labels}: {values}")
    return values[0]


def first_visible(candidates: list[Locator], timeout_ms: int) -> Locator | None:
    """First candidate that becomes visible within the timeout (checked in order)."""
    per_candidate = max(timeout_ms // max(len(candidates), 1), 500)
    for candidate in candidates:
        try:
            candidate.first.wait_for(state="visible", timeout=per_candidate)
            return candidate.first
        except Exception:  # noqa: BLE001 - not visible: try the next candidate
            continue
    return None
