"""
scraper/flipkart.py — Flipkart product scraper
=================================================
Handles Flipkart search results + individual product page spec extraction.
Uses Playwright only — no requests, no BeautifulSoup.

Key Flipkart-specific challenges:
- Login popup appears on almost every page → dismissed via Escape key
- Specs are in a collapsible accordion → must click to expand
- Ad listings have an "Ad" badge → detected before visiting
- Multiple page layout versions → multiple selector fallbacks
"""

import re
import asyncio
import random
import logging
from urllib.parse import quote_plus
from typing import Optional

from playwright.async_api import Page

from .browser import (
    ScrapedProduct, MAX_PRODUCTS, DELAY_MIN, DELAY_MAX, PAGE_TIMEOUT
)

log = logging.getLogger(__name__)


class FlipkartScraper:
    """
    Scrapes Flipkart using Playwright only.
    """

    BASE = "https://www.flipkart.com"

    def __init__(self, page: Page):
        self.page = page

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC
    # ─────────────────────────────────────────────────────────────────────────

    async def search_and_extract(
        self, query: str, max_products: int = MAX_PRODUCTS
    ) -> list[ScrapedProduct]:

        products = []
        url = f"{self.BASE}/search?q={quote_plus(query)}&otracker=search"
        log.info(f"[Flipkart] Searching: {url}")

        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await self._dismiss_popup()
            await self._delay()
        except Exception as e:
            log.error(f"[Flipkart] Failed to load search page: {e}")
            return products

        product_urls = await self._collect_product_urls()
        log.info(f"[Flipkart] Found {len(product_urls)} non-sponsored URLs")

        for i, product_url in enumerate(product_urls[:max_products]):
            log.info(f"[Flipkart] Visiting product {i+1}/{min(len(product_urls), max_products)}")
            await self._delay()

            product = await self._extract_product(product_url)
            if product and product.title:
                products.append(product)
                log.info(f"[Flipkart] Extracted: {product.title[:65]}")
            else:
                log.warning(f"[Flipkart] Could not extract: {product_url}")

        log.info(f"[Flipkart] Done — {len(products)} products extracted")
        return products

    # ─────────────────────────────────────────────────────────────────────────
    # COLLECT URLs
    # ─────────────────────────────────────────────────────────────────────────

    async def _collect_product_urls(self) -> list[str]:
        urls = []

        # Wait for the product grid to load
        try:
            await self.page.wait_for_selector(
                "div[data-id], div._1AtVbE, div._2kHMtA, div._4ddWXP",
                timeout=8000
            )
        except Exception:
            log.warning("[Flipkart] Product grid did not load in time")
            return urls

        # Get all product card containers
        cards = await self.page.query_selector_all(
            "div[data-id], div._1AtVbE, div._2kHMtA, div._4ddWXP"
        )

        for card in cards:
            # ── Ad detection — 3 methods ──────────────────────────────────

            # Method 1: explicit ad badge element
            ad_el = await card.query_selector(
                "._3MbgHc, [class*='sponsored'], [class*='promoted'], "
                "[class*='_3Lysjg']"
            )
            if ad_el:
                continue

            # Method 2: "Ad" text as a standalone word in the card
            card_text = await card.inner_text()
            lines = [l.strip() for l in card_text.split("\n") if l.strip()]
            if any(l in ("Ad", "Sponsored", "Featured") for l in lines):
                continue

            # Method 3: card has data-tracking with "ad" or "sponsored"
            tkd = await card.get_attribute("data-tkid") or ""
            if tkd and "ADVIEW" in tkd:
                continue
                
            tracking = await card.get_attribute("data-tkd") or ""
            if "sponsored" in tracking.lower():
                continue

            # ── Extract link ───────────────────────────────────────────────
            link = await card.query_selector("a[href*='/p/']")
            if not link:
                link = await card.query_selector("a[href*='pid=']")
            if not link:
                # Try relative link in randomized classes
                link = await card.query_selector("a.k7wcnx, a._1fQZEK")

            if link:
                href = await link.get_attribute("href") or ""
                if href:
                    full = href if href.startswith("http") else f"{self.BASE}{href}"
                    # Base URL for de-duplication
                    base_url = full.split("?")[0]
                    if ("/p/" in base_url or "pid=" in full) and base_url not in urls:
                        urls.append(base_url)

        return urls

    # ─────────────────────────────────────────────────────────────────────────
    # EXTRACT one product page
    # ─────────────────────────────────────────────────────────────────────────

    async def _extract_product(self, url: str) -> Optional[ScrapedProduct]:
        product = ScrapedProduct(url=url, platform="Flipkart")

        try:
            await self.page.goto(
                url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT
            )
            await self._dismiss_popup()
            await self._delay(0.5, 1.5)

            # ── Title ────────────────────────────────────────────────────────
            title = await self.page.evaluate('''() => {
                const sel = [".VU-ZEz", "span.B_NuCI", "h1._9E25nV", "h1.yhB1nd", ".G6XhRU h1", "h1"];
                for (let s of sel) {
                    let el = document.querySelector(s);
                    if (el && el.innerText.trim()) return el.innerText.trim();
                }
                // Fallback: meta tags
                let og = document.querySelector('meta[property="og:title"]');
                if (og) return og.content.split(' - ')[0];
                return document.title.split(' | ')[0];
            }''')
            product.title = title

            # ── Price ────────────────────────────────────────────────────────
            price_data = await self.page.evaluate('''() => {
                const sel = [".Nx9bqj.CxhGGd", "._30jeq3._16Jk6d", "._30jeq3", ".CEmiEU ._1vC4OE"];
                for (let s of sel) {
                    let el = document.querySelector(s);
                    if (el && el.innerText.includes('₹')) return el.innerText.trim();
                }
                // Search all divs for currency symbol
                let divs = Array.from(document.querySelectorAll('div,span')).filter(d => d.innerText.startsWith('₹') && d.innerText.length < 15);
                return divs.length ? divs[0].innerText.trim() : "";
            }''')
            if price_data:
                product.price_raw = price_data
                product.price_num = self._parse_price(price_data)

            # ── Product ID from URL ──────────────────────────────────────────
            m = re.search(r"/p/([A-Z0-9]+)", url, re.IGNORECASE)
            if not m:
                m = re.search(r"pid=([A-Z0-9]+)", url, re.IGNORECASE)
            if m:
                product.product_id = m.group(1)

            # ── SPECS — 3 strategies ─────────────────────────────────────────

            # Strategy 1: Expand accordion and extract spec table
            specs = await self._extract_accordion_specs()

            # Strategy 2: Raw spec table without accordion
            if len(specs) < 3:
                specs.update(await self._extract_raw_spec_table())

            # Strategy 3: Key highlights section
            if len(specs) < 2:
                highlights = await self._extract_highlights()
                product.description = "\n".join(highlights)
                specs.update(self._parse_text_for_specs(product.description))

            product.specs = specs

            # ── Rating ───────────────────────────────────────────────────────
            for sel in ["._3LWZlK", "._1lRcqv ._3LWZlK", ".gUuXy-"]:
                el = await self.page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if text and re.match(r"\d", text):
                        product.rating = text
                        break

            # ── Reviews ──────────────────────────────────────────────────────
            product.reviews = await self._extract_reviews()

        except Exception as e:
            log.error(f"[Flipkart] Product extraction error ({url}): {e}")
            return None

        return product if product.title else None

    # ── Spec extraction strategies ────────────────────────────────────────────

    async def _extract_accordion_specs(self) -> dict:
        """
        Flipkart shows specs in collapsible sections.
        We must click each 'View more details' / '+' to expand them.
        """
        specs = {}

        expand_selectors = [
            "._1h_eID",
            "._3ULzGw",
            "._3hwKSh",
            "[class*='_2YjTDi']",
            "._3ZnBKC",
        ]

        for sel in expand_selectors:
            btns = await self.page.query_selector_all(sel)
            for btn in btns[:15]:
                try:
                    await btn.click()
                    await asyncio.sleep(0.3)
                except Exception:
                    pass

        # Extract from expanded table rows
        row_selectors = [
            "tr._1s_Smc", "._14cfVK", "._2-riNZ", "table._2dETP tr", "._3_6Uyw ._14cfVK",
            "div._1psv1zeb9 div._1psv1ze0", # New dynamic layout
        ]

        for sel in row_selectors:
            rows = await self.page.query_selector_all(sel)
            for row in rows:
                # Try many common cell selectors
                cells = await row.query_selector_all(
                    "td, ._1hKmbr, ._2RngIm, ._21lJbe td, div[font], span[font]"
                )
                if len(cells) >= 2:
                    key = (await cells[0].inner_text()).strip()
                    val = (await cells[1].inner_text()).strip()
                    if key and val and len(key) < 60 and len(val) < 200:
                        specs[key] = val

            if len(specs) >= 5:
                break

        return specs

    async def _extract_raw_spec_table(self) -> dict:
        """
        Some Flipkart pages show specs in a plain table without accordion.
        """
        specs = {}
        rows = await self.page.query_selector_all(
            "._14cfVK, .rzSsCi tr, ._3oDz0I tr"
        )
        for row in rows:
            cells = await row.query_selector_all("td")
            if len(cells) >= 2:
                key = (await cells[0].inner_text()).strip()
                val = (await cells[1].inner_text()).strip()
                if key and val and len(key) < 60:
                    specs[key] = val
        return specs

    async def _extract_highlights(self) -> list[str]:
        """
        Flipkart's 'Highlights' section at the top of the listing.
        """
        highlights = []
        selectors = [
            "._21lJbe li", ".X3BRps li", "li._21A6_g", "div._1AtVbE li",
            "ul li", # Generic fallback
        ]
        for sel in selectors:
            items = await self.page.query_selector_all(sel)
            for item in items:
                text = (await item.inner_text()).strip()
                if text and len(text) > 5 and text not in highlights:
                    highlights.append(text)
            if len(highlights) >= 3:
                break
        return highlights

    async def _extract_reviews(self) -> list[str]:
        """
        Extract top user reviews/comments from the product page.
        """
        reviews = []
        selectors = [".t-ZTKy", "div.Zmyq-m", "div._6K-7Co"]
        for sel in selectors:
            items = await self.page.query_selector_all(sel)
            for item in items:
                text = (await item.inner_text()).strip()
                if text and len(text) > 15:
                    text = re.sub(r"READ MORE", "", text, flags=re.IGNORECASE).strip()
                    if text not in reviews:
                        reviews.append(text)
            if len(reviews) >= 3:
                break
        return reviews[:5]

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    async def _dismiss_popup(self):
        """
        Flipkart shows a login popup on almost every page load.
        Dismiss via Escape key — the most reliable method.
        """
        try:
            await asyncio.sleep(1.0)
            await self.page.keyboard.press("Escape")

            for sel in [
                "button._2KpZ6l._2doB4z",
                "button.close-button",
                "._2AkmmA button",
                "[class*='close']",
            ]:
                el = await self.page.query_selector(sel)
                if el:
                    try:
                        await el.click()
                        break
                    except Exception:
                        pass
        except Exception:
            pass

    def _parse_text_for_specs(self, text: str) -> dict:
        """
        Parse unstructured highlight text into key-value specs.
        Flipkart highlights often use pipe separators.
        """
        specs = {}
        parts = re.split(r"[|\n]", text)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            p = part.lower()

            if re.search(r"i[3579][-\s]\d+", p):
                specs.setdefault("Processor", part)
            elif re.search(r"\d+\s*gb\s+(?:ram|memory)", p):
                specs.setdefault("RAM", part)
            elif re.search(r"\d+\s*(?:gb|tb)\s+ssd", p):
                specs.setdefault("Storage", part)
            elif re.search(r"\d+\.?\d*\s*inch", p):
                specs.setdefault("Display", part)
            elif re.search(r"\d+(?:st|nd|rd|th)\s+gen", p):
                specs.setdefault("Generation", part)
            elif re.search(r"(ips|va|tn|oled)\s+display", p):
                specs.setdefault("Panel Type", part)
            elif re.search(r"(4k|uhd|qhd|fhd|1080|2160)", p):
                specs.setdefault("Resolution", part)

        return specs

    def _parse_price(self, raw: str) -> float:
        cleaned = re.sub(r"[₹$,\s]", "", raw)
        m = re.search(r"[\d.]+", cleaned)
        try:
            return float(m.group(0)) if m else 0.0
        except ValueError:
            return 0.0

    async def _delay(self, mn: float = DELAY_MIN, mx: float = DELAY_MAX):
        await asyncio.sleep(random.uniform(mn, mx))
