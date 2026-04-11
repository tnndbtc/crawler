"""
Base Playwright helper for headless-browser surface collectors.

Provides a thin async context manager that launches a Chromium browser,
navigates to a URL, waits for content to render, and returns the page
handle for DOM extraction.

Usage:
    async with PlaywrightPage(url) as page:
        html = await page.content()
        links = await page.query_selector_all("a[href]")

Key choices (validated 2026-04-11):
  - wait_until="domcontentloaded"  — networkidle times out on heavy SPAs
  - Extra wait after navigation    — JS-rendered lists need a render tick
  - Headless Chromium              — no display required
  - Stealth UA                     — reduces bot-detection friction
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from playwright.async_api import async_playwright, Page, Browser, BrowserContext

logger = logging.getLogger(__name__)

# Default wait after domcontentloaded (ms) — lets JS-rendered lists appear
DEFAULT_POST_NAV_WAIT_MS = 4000

# Stealth User-Agent (desktop Chrome)
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@asynccontextmanager
async def PlaywrightPage(
    url: str,
    *,
    wait_ms: int = DEFAULT_POST_NAV_WAIT_MS,
    extra_headers: Optional[dict] = None,
    viewport: Optional[dict] = None,
) -> AsyncGenerator[Page, None]:
    """
    Async context manager: launch browser, open page, yield Page, then close.

    Args:
        url:           URL to navigate to.
        wait_ms:       Milliseconds to wait after domcontentloaded (default 4000).
        extra_headers: Additional HTTP headers to set on the page.
        viewport:      Browser viewport dict e.g. {"width": 1280, "height": 800}.
                       Defaults to 1280×800.

    Yields:
        playwright.async_api.Page — ready for DOM queries.

    Example:
        async with PlaywrightPage("https://www.hk01.com/zone/1") as page:
            anchors = await page.query_selector_all("a[href]")
    """
    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=True)
        context: BrowserContext = await browser.new_context(
            user_agent=_UA,
            viewport=viewport or {"width": 1280, "height": 800},
            locale="zh-TW",
        )
        if extra_headers:
            await context.set_extra_http_headers(extra_headers)

        page: Page = await context.new_page()
        try:
            logger.debug(f"Playwright: navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)
            logger.debug(f"Playwright: page ready ({wait_ms}ms post-nav wait)")
            yield page
        finally:
            await context.close()
            await browser.close()


async def extract_links(
    page: Page,
    href_filter,
    base_url: str = "",
    limit: int = 100,
) -> list[dict]:
    """
    Extract all <a href> elements from the page that pass href_filter.

    Args:
        page:        Playwright Page object.
        href_filter: Callable(href: str) -> bool — returns True to include the link.
        base_url:    Prepended to relative hrefs (e.g. "https://www.hk01.com").
        limit:       Maximum links to return.

    Returns:
        List of {"href": str, "title": str} dicts.
    """
    anchors = await page.query_selector_all("a[href]")
    results = []
    seen: set = set()

    for anchor in anchors:
        if len(results) >= limit:
            break
        try:
            href = (await anchor.get_attribute("href") or "").strip()
            if not href or not href_filter(href):
                continue
            # Make absolute
            if href.startswith("/") and base_url:
                href = base_url.rstrip("/") + href
            if href in seen:
                continue

            title = (await anchor.inner_text()).strip()
            title = " ".join(title.split())  # collapse whitespace
            if not title:
                continue

            # Only mark as seen once we have a valid title (avoids locking out
            # a URL when the first anchor with that href has an empty text node)
            seen.add(href)
            results.append({"href": href, "title": title})
        except Exception:
            continue

    return results
