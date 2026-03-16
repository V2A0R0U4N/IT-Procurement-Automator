"""
Scraper package — Playwright-based product scrapers for Amazon.in and Flipkart.
"""

from .browser import ScrapedProduct, create_stealth_context, run_scraper
from .amazon import AmazonScraper
from .flipkart import FlipkartScraper

__all__ = [
    "ScrapedProduct",
    "create_stealth_context",
    "run_scraper",
    "AmazonScraper",
    "FlipkartScraper",
]
