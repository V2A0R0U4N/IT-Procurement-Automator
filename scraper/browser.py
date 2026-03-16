"""
scraper/browser.py — Playwright browser factory + shared utilities
===================================================================
Contains the ScrapedProduct data model, stealth browser context factory,
and the concurrent run_scraper entry point.
"""

import re
import asyncio
import random
import logging
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import async_playwright, Page, BrowserContext

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

HEADLESS       = True
MAX_PRODUCTS   = 3        # max product pages to visit per platform
DELAY_MIN      = 2.0      # seconds between actions (polite + anti-bot)
DELAY_MAX      = 4.0
PAGE_TIMEOUT   = 20000    # ms

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
]

# Block these resource types — speeds up loading significantly
# Images, fonts, media are not needed for spec extraction
BLOCK_TYPES = {"image", "media", "font", "other"}


# ─────────────────────────────────────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScrapedProduct:
    title:        str   = ""
    price_raw:    str   = ""
    price_num:    float = 0.0
    specs:        dict  = field(default_factory=dict)
    reviews:      list  = field(default_factory=list)
    url:          str   = ""
    platform:     str   = ""
    product_id:   str   = ""    # ASIN for Amazon, PID for Flipkart
    image_url:    str   = ""
    rating:       str   = ""
    review_count: str   = ""
    description:  str   = ""    # raw bullet text — used as fallback for LLM


# ─────────────────────────────────────────────────────────────────────────────
# SHARED BROWSER FACTORY
# ─────────────────────────────────────────────────────────────────────────────

async def create_stealth_context(pw, headless: bool = HEADLESS) -> tuple:
    """
    Launch browser + create a context with stealth settings.
    Returns (browser, context).

    Stealth settings applied:
    - Realistic user agent
    - Real viewport dimensions
    - Indian locale + timezone (matching the e-commerce sites)
    - navigator.webdriver = false (via init script)
    - Resource blocking for speed
    """
    browser = await pw.chromium.launch(
        headless=headless,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--window-size=1366,768",
        ]
    )

    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1366, "height": 768},
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        ignore_https_errors=True,
        java_script_enabled=True,
        # Pretend to have a real screen
        color_scheme="light",
    )

    # ── Key stealth script ───────────────────────────────────────────────────
    # Removes the navigator.webdriver flag that sites use to detect Playwright
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-IN', 'en-US', 'en'],
        });
        window.chrome = { runtime: {} };
    """)

    # ── Block unnecessary resources ──────────────────────────────────────────
    async def block_resources(route):
        if route.request.resource_type in BLOCK_TYPES:
            await route.abort()
        else:
            await route.continue_()

    await context.route("**/*", block_resources)

    return browser, context


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER — run both scrapers concurrently
# ─────────────────────────────────────────────────────────────────────────────

async def run_scraper(query: str) -> dict:
    """
    Run Amazon + Flipkart scrapers concurrently on the same query.
    Returns combined results from both platforms.
    """
    from .amazon import AmazonScraper
    from .flipkart import FlipkartScraper

    log.info(f"Starting concurrent scrape for: '{query}'")

    async with async_playwright() as pw:

        # Create two separate browser contexts
        # Separate contexts = separate cookies, separate sessions
        # More realistic and less likely to trigger bot detection
        browser, amazon_ctx = await create_stealth_context(pw, headless=HEADLESS)
        _, flipkart_ctx     = await create_stealth_context(pw, headless=HEADLESS)

        amazon_page   = await amazon_ctx.new_page()
        flipkart_page = await flipkart_ctx.new_page()

        amazon_scraper   = AmazonScraper(amazon_page)
        flipkart_scraper = FlipkartScraper(flipkart_page)

        # Run both concurrently
        amazon_task   = asyncio.create_task(amazon_scraper.search_and_extract(query))
        flipkart_task = asyncio.create_task(flipkart_scraper.search_and_extract(query))

        amazon_results, flipkart_results = await asyncio.gather(
            amazon_task, flipkart_task, return_exceptions=True
        )

        if isinstance(amazon_results, Exception):
            log.error(f"Amazon failed: {amazon_results}")
            amazon_results = []
        if isinstance(flipkart_results, Exception):
            log.error(f"Flipkart failed: {flipkart_results}")
            flipkart_results = []

        await browser.close()

    all_products = list(amazon_results) + list(flipkart_results)

    log.info(
        f"Scrape complete: {len(amazon_results)} Amazon + "
        f"{len(flipkart_results)} Flipkart = {len(all_products)} total"
    )

    return {
        "amazon":   [vars(p) for p in amazon_results],
        "flipkart": [vars(p) for p in flipkart_results],
        "total":    len(all_products),
        "products": [vars(p) for p in all_products],
    }


# ─────────────────────────────────────────────────────────────────────────────
# DEMO
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    async def demo():
        # Test with laptop query
        query = "Asus laptop Intel i5 16GB RAM 512GB SSD"
        results = await run_scraper(query)

        print(f"\n{'='*70}")
        print(f"Query: {query}")
        print(f"Total products: {results['total']} "
              f"({len(results['amazon'])} Amazon, "
              f"{len(results['flipkart'])} Flipkart)")

        for i, p in enumerate(results["products"], 1):
            print(f"\n--- Product {i} ({p['platform']}) ---")
            print(f"Title : {p['title'][:70]}")
            print(f"Price : {p['price_raw']}")
            print(f"URL   : {p['url']}")
            print(f"Specs ({len(p['specs'])} keys):")
            for k, v in list(p["specs"].items())[:8]:
                print(f"  {k}: {v}")
            if p["description"]:
                print(f"Desc  : {p['description'][:150]}")

    asyncio.run(demo())
