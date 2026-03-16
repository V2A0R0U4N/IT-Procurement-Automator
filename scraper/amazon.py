"""
scraper/amazon.py — Amazon.in product scraper
===============================================
Handles Amazon search results + individual product page spec extraction.
Uses Playwright only — no requests, no BeautifulSoup.
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


class AmazonScraper:
    """
    Scrapes Amazon.in using Playwright only.

    Flow:
      1. Open search URL
      2. Detect + skip sponsored listings
      3. Collect product URLs from result cards
      4. Visit each product page
      5. Extract specs using multiple Playwright-based strategies
    """

    BASE = "https://www.amazon.in"

    def __init__(self, page: Page):
        self.page = page

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC: search + extract all products
    # ─────────────────────────────────────────────────────────────────────────

    async def search_and_extract(
        self, query: str, max_products: int = MAX_PRODUCTS
    ) -> list[ScrapedProduct]:

        products = []
        url = f"{self.BASE}/s?k={quote_plus(query)}&i=electronics"
        log.info(f"[Amazon] Searching: {url}")

        # ── Load search results ──────────────────────────────────────────────
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await self._delay()
        except Exception as e:
            log.error(f"[Amazon] Failed to load search page: {e}")
            return products

        # ── Check for CAPTCHA ────────────────────────────────────────────────
        if await self._is_captcha():
            log.warning("[Amazon] CAPTCHA detected on search page — aborting")
            return products

        # ── Collect product URLs ─────────────────────────────────────────────
        product_urls = await self._collect_product_urls()
        log.info(f"[Amazon] Found {len(product_urls)} non-sponsored product URLs")

        # If fewer than 3 results, try page 2
        if len(product_urls) < 3:
            log.info("[Amazon] Few results on page 1, trying page 2")
            await self.page.goto(
                f"{url}&page=2",
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )
            await self._delay()
            more = await self._collect_product_urls()
            product_urls.extend(
                u for u in more if u not in product_urls
            )

        # ── Visit each product page ──────────────────────────────────────────
        for i, product_url in enumerate(product_urls[:max_products]):
            log.info(f"[Amazon] Visiting product {i+1}/{min(len(product_urls), max_products)}")
            await self._delay()

            product = await self._extract_product(product_url)
            if product and product.title:
                products.append(product)
                log.info(f"[Amazon] Extracted: {product.title[:65]}")
            else:
                log.warning(f"[Amazon] Could not extract product from: {product_url}")

        log.info(f"[Amazon] Done — {len(products)} products extracted")
        return products

    # ─────────────────────────────────────────────────────────────────────────
    # COLLECT PRODUCT URLs from search results
    # ─────────────────────────────────────────────────────────────────────────

    async def _collect_product_urls(self) -> list[str]:
        urls = []

        # Wait for result cards to appear
        try:
            await self.page.wait_for_selector(
                "[data-component-type='s-search-result']",
                timeout=8000
            )
        except Exception:
            log.warning("[Amazon] Result cards did not load in time")
            return urls

        # Get all result card elements
        cards = await self.page.query_selector_all(
            "[data-component-type='s-search-result']"
        )

        for card in cards:

            # ── Sponsored detection — 3 methods ─────────────────────────────

            # Method 1: dedicated sponsored element inside the card
            sponsored_el = await card.query_selector(
                "[data-component-type='sp-sponsored-result'], "
                ".puis-sponsored-label-text, "
                ".s-label-popover-default"
            )
            if sponsored_el:
                continue

            # Method 2: aria-label containing "Sponsored"
            aria = await card.get_attribute("aria-label") or ""
            if "sponsored" in aria.lower():
                continue

            # Method 3: text scan — "Sponsored" appears as standalone line
            # in the card's inner text
            card_text = await card.inner_text()
            # Split into lines and check each — avoids false positives
            # where "sponsored" appears inside a product name
            lines = [l.strip() for l in card_text.split("\n") if l.strip()]
            if any(l.lower() == "sponsored" for l in lines):
                continue

            # ── Extract the product link ─────────────────────────────────────
            # Amazon product links always contain /dp/ followed by the ASIN
            link = await card.query_selector("a[href*='/dp/']")
            if not link:
                link = await card.query_selector("h2 a[href]")

            if link:
                href = await link.get_attribute("href") or ""
                # Clean URL — extract just /dp/ASIN to remove all tracking params
                asin_match = re.search(r"/dp/([A-Z0-9]{10})", href)
                if asin_match:
                    clean = f"{self.BASE}/dp/{asin_match.group(1)}"
                    if clean not in urls:
                        urls.append(clean)

        return urls

    # ─────────────────────────────────────────────────────────────────────────
    # EXTRACT one product page — all data via Playwright
    # ─────────────────────────────────────────────────────────────────────────

    async def _extract_product(self, url: str) -> Optional[ScrapedProduct]:
        product = ScrapedProduct(url=url, platform="Amazon")

        try:
            await self.page.goto(
                url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT
            )
            await self._delay(0.5, 1.5)

            if await self._is_captcha():
                log.warning(f"[Amazon] CAPTCHA on product page: {url}")
                return None

            # ── Title ────────────────────────────────────────────────────────
            for sel in [
                "#productTitle",
                "h1.a-size-large span",
                "span.product-title-word-break",
                "h1",
            ]:
                el = await self.page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if text:
                        product.title = text
                        break

            # ── Price ────────────────────────────────────────────────────────
            for sel in [
                ".a-price .a-offscreen",
                "#priceblock_ourprice",
                "#priceblock_dealprice",
                "#corePriceDisplay_desktop_feature_div .a-offscreen",
                ".a-price-whole",
                "span.a-color-price",
            ]:
                el = await self.page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if text and any(c.isdigit() for c in text):
                        product.price_raw = text
                        product.price_num = self._parse_price(text)
                        break

            # ── ASIN from URL ────────────────────────────────────────────────
            m = re.search(r"/dp/([A-Z0-9]{10})", url)
            if m:
                product.product_id = m.group(1)

            # ── SPECS — 3 strategies ─────────────────────────────────────────

            # Strategy 1: Technical Details table
            specs = await self._extract_tech_details()

            # Strategy 2: Product Information / Detail Bullets
            if len(specs) < 3:
                specs.update(await self._extract_detail_bullets())

            # Strategy 3: Feature bullets
            if len(specs) < 2:
                bullets = await self._extract_feature_bullets()
                product.description = "\n".join(bullets)
                specs.update(self._parse_text_for_specs(product.description))

            product.specs = specs

            # ── Rating ───────────────────────────────────────────────────────
            for sel in ["#acrPopover .a-icon-alt", "#averageCustomerReviews .a-icon-alt"]:
                el = await self.page.query_selector(sel)
                if el:
                    product.rating = (await el.inner_text()).strip()
                    break

            # ── Review count ─────────────────────────────────────────────────
            el = await self.page.query_selector("#acrCustomerReviewText")
            if el:
                product.review_count = (await el.inner_text()).strip()

            # ── Reviews ──────────────────────────────────────────────────────
            product.reviews = await self._extract_reviews()

        except Exception as e:
            log.error(f"[Amazon] Product extraction error ({url}): {e}")
            return None

        return product if product.title else None

    # ── Spec extraction strategies ────────────────────────────────────────────

    async def _extract_tech_details(self) -> dict:
        """
        Amazon's 'Technical Details' table.
        Structured as <tr> rows with <th> key and <td> value.
        """
        specs = {}
        selectors = [
            "#productDetails_techSpec_section_1 tr",
            "#productDetails_techSpec_section_2 tr",
            "#technicalSpecifications_section_1 tr",
            ".prodDetTable tr",
        ]
        for sel in selectors:
            rows = await self.page.query_selector_all(sel)
            for row in rows:
                th = await row.query_selector("th")
                td = await row.query_selector("td")
                if th and td:
                    key = (await th.inner_text()).strip().rstrip(":")
                    val = (await td.inner_text()).strip()
                    # Clean control characters Amazon sometimes includes
                    key = re.sub(r"[\u200f\u200e\n\r]", " ", key).strip()
                    val = re.sub(r"[\u200f\u200e]", "", val).strip()
                    if key and val and len(key) < 60:
                        specs[key] = val
            if len(specs) >= 3:
                break
        return specs

    async def _extract_detail_bullets(self) -> dict:
        """
        Amazon's 'Product Information' bullet section.
        Format: "Key : Value" in <li> elements.
        """
        specs = {}
        selectors = [
            "#detailBullets_feature_div li",
            "#detail-bullets .content li",
            "#productDetails_detailBullets_sections1 tr",
        ]
        for sel in selectors:
            items = await self.page.query_selector_all(sel)
            for item in items:
                text = (await item.inner_text()).strip()
                text = re.sub(r"[\u200f\u200e\u2022]", "", text).strip()
                if ":" in text:
                    parts = text.split(":", 1)
                    key = parts[0].strip()
                    val = parts[1].strip() if len(parts) > 1 else ""
                    if key and val and len(key) < 60:
                        specs[key] = val
            if len(specs) >= 3:
                break
        return specs

    async def _extract_feature_bullets(self) -> list[str]:
        """
        Amazon's feature bullet points at the top of the listing.
        """
        bullets = []
        items = await self.page.query_selector_all(
            "#feature-bullets li span.a-list-item, "
            "#featurebullets_feature_div li span"
        )
        for item in items:
            text = (await item.inner_text()).strip()
            if text and len(text) > 8:
                bullets.append(text)
        return bullets

    async def _extract_reviews(self) -> list[str]:
        """
        Extract top user reviews/comments from the product page.
        """
        reviews = []
        selectors = [
            ".review-text-content span",
            "div[data-hook='review-collapsed'] span",
            ".a-expander-partial-collapse-content"
        ]
        for sel in selectors:
            items = await self.page.query_selector_all(sel)
            for item in items:
                text = (await item.inner_text()).strip()
                if text and len(text) > 15:
                    if text not in reviews:
                        reviews.append(text)
            if len(reviews) >= 3:
                break
        return reviews[:5]

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_text_for_specs(self, text: str) -> dict:
        """Parse unstructured text into spec key-value pairs using regex."""
        specs = {}
        t = text.lower()

        patterns = [
            (
                r"(intel\s+core\s+i[3579][-\s]\d+\w*"
                r"(?:\s*\(?\d+th\s+gen\)?)?)",
                "Processor"
            ),
            (r"(\d+\s*gb\s+(?:ddr\d\s*)?(?:ram|memory))", "RAM"),
            (r"(\d+\s*(?:gb|tb)\s+(?:nvme\s+)?ssd)", "Storage"),
            (r"(\d+\.?\d*[\"']\s*(?:fhd|uhd|ips|tn|va)?)", "Display"),
            (r"(\d+)(?:st|nd|rd|th)\s+gen", "Processor Gen"),
        ]

        for pattern, key in patterns:
            m = re.search(pattern, t, re.IGNORECASE)
            if m and key not in specs:
                specs[key] = m.group(0).strip()

        return specs

    def _parse_price(self, raw: str) -> float:
        cleaned = re.sub(r"[₹$,\s]", "", raw)
        m = re.search(r"[\d.]+", cleaned)
        try:
            return float(m.group(0)) if m else 0.0
        except ValueError:
            return 0.0

    async def _is_captcha(self) -> bool:
        try:
            content = await self.page.content()
            markers = [
                "robot check", "type the characters",
                "enter the characters", "captcha",
                "api-services-support@amazon",
            ]
            return any(m in content.lower() for m in markers)
        except Exception:
            return False

    async def _delay(self, mn: float = DELAY_MIN, mx: float = DELAY_MAX):
        await asyncio.sleep(random.uniform(mn, mx))
