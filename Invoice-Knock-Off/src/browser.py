"""Browser start/stop, debug screenshots and error snapshots."""

from __future__ import annotations

import re
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from .config import Settings
from .logger import get_logger
from .models import RunContext
from .utils import safe_filename, timestamp
from .zoho_selectors import LoginSelectors

# Removed from saved HTML so captures never contain session secrets.
_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_SENSITIVE_ATTR = re.compile(
    r"""(\b(?:value|content|data-[\w-]*token[\w-]*)\s*=\s*)(["'])(.*?)\2""",
    re.IGNORECASE | re.DOTALL,
)
_SENSITIVE_HINT = re.compile(r"password|passwd|token|csrf|secret|session|otp|auth", re.IGNORECASE)
_TAG = re.compile(r"<(input|meta)\b[^>]*>", re.IGNORECASE)


def is_sign_in_url(url: str) -> bool:
    return LoginSelectors.ACCOUNTS_HOST_FRAGMENT in (urlparse(url).hostname or "")


def sanitize_html(html: str) -> str:
    """Strip scripts and blank out values of password/token-like inputs and meta tags."""
    html = _SCRIPT_BLOCK.sub("<script>/* removed */</script>", html)

    def scrub(tag_match: re.Match[str]) -> str:
        tag = tag_match.group(0)
        if not _SENSITIVE_HINT.search(tag):
            return tag
        return _SENSITIVE_ATTR.sub(lambda m: f"{m.group(1)}{m.group(2)}***{m.group(2)}", tag)

    return _TAG.sub(scrub, html)


class BrowserSession:
    """Context manager that owns the Playwright browser and the single working tab."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.log = get_logger()
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._browser = None
        self.page: Page | None = None

    def __enter__(self) -> BrowserSession:
        s = self.settings
        self._playwright = sync_playwright().start()
        chromium = self._playwright.chromium
        channel = None if s.browser_channel == "chromium" else s.browser_channel
        options = {"headless": s.headless, "channel": channel, "no_viewport": True, "args": ["--start-maximized"]}
        try:
            self._launch(chromium, options)
        except Exception as exc:  # noqa: BLE001 - fall back to bundled Chromium
            if channel is None:
                raise
            self.log.warning("Could not start %s (%s). Falling back to Playwright Chromium.", channel, exc)
            self._launch(chromium, {**options, "channel": None})
        self._context.set_default_timeout(s.timeout_ms)
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self.log.info("Browser started (%s, headless=%s)", s.browser_channel, s.headless)
        return self

    def _launch(self, chromium, options: dict) -> None:
        if self.settings.persist_browser_session:
            self.settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
            self._context = chromium.launch_persistent_context(str(self.settings.browser_profile_dir), **options)
        else:
            self._browser = chromium.launch(**{k: v for k, v in options.items() if k != "no_viewport"})
            self._context = self._browser.new_context(no_viewport=True)

    def __exit__(self, exc_type, exc, tb) -> None:
        for closer in (
            lambda: self._context and self._context.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._playwright and self._playwright.stop(),
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001 - browser may already be gone
                pass
        self.log.info("Browser closed")

    # ------------------------------------------------------------------
    def on_sign_in_page(self) -> bool:
        """Sign-in pages may show a typed password (eye icon), so they are never captured."""
        try:
            return is_sign_in_url(self.page.url) if self.page is not None else False
        except Exception:  # noqa: BLE001 - browser gone: treat as unsafe
            return True

    def capture(self, step: str) -> None:
        """Screenshot of an important moment (when SAVE_DEBUG_SCREENSHOTS=true)."""
        if not self.settings.save_debug_screenshots or self.page is None or self.on_sign_in_page():
            return
        try:
            folder = self.settings.screenshots_dir
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{timestamp()}_{safe_filename(step)}.png"
            self.page.screenshot(path=str(path), full_page=True)
            self.log.debug("Screenshot saved: %s", path)
        except Exception as exc:  # noqa: BLE001 - a screenshot must never break the run
            self.log.warning("Screenshot '%s' failed: %s", step, exc)

    def save_error_report(self, error: BaseException, context: RunContext) -> Path:
        """Write screenshot.png, page.html, current_url.txt and error.log to logs/errors/<time>/."""
        folder = self.settings.errors_dir / f"{timestamp()}_{safe_filename(type(error).__name__)}"
        folder.mkdir(parents=True, exist_ok=True)
        details = [
            f"Timestamp: {datetime.now().isoformat(timespec='seconds')}",
            f"Error type: {type(error).__name__}",
            f"Error: {error}",
            "",
            context.as_text(),
            "",
            "Traceback:",
            "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        ]
        page = self.page
        if page is not None and self.on_sign_in_page():
            details.append("Screenshot and page HTML not saved: the browser was on a Zoho sign-in page.")
            (folder / "current_url.txt").write_text(page.url.split("?")[0], encoding="utf-8")
        elif page is not None:
            for name, action in (
                ("current_url.txt", lambda: (folder / "current_url.txt").write_text(page.url, encoding="utf-8")),
                ("screenshot.png", lambda: page.screenshot(path=str(folder / "screenshot.png"), full_page=True)),
                ("page.html", lambda: (folder / "page.html").write_text(sanitize_html(page.content()), encoding="utf-8")),
            ):
                try:
                    action()
                except Exception as exc:  # noqa: BLE001 - browser may have crashed
                    details.append(f"Could not save {name}: {exc}")
        (folder / "error.log").write_text("\n".join(details), encoding="utf-8")
        return folder
