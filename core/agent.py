"""
core/agent.py — Procurement Agent Orchestrator
===============================================
Ties everything together:
1. Parse requirement
2. Build queries
3. Scrape platforms concurrently
4. Normalise specs
5. Evaluate products with LLM
6. Return structured results
"""

import asyncio
import logging
import time
from typing import Callable, Optional, Dict, Any, List

from .parser import parse_requirement, build_search_queries, requirement_to_dict
from .normaliser import SpecNormaliser
from .evaluator import LLMEvaluator
from scraper.browser import run_scraper

log = logging.getLogger(__name__)

class ProcurementAgent:
    """
    Orchestrates the full procurement pipeline.
    """

    def __init__(self):
        self.normaliser = SpecNormaliser()
        self.evaluator = LLMEvaluator()

    async def run(
        self, 
        request_text: str, 
        on_progress: Optional[Callable[[str, int], None]] = None
    ) -> Dict[str, Any]:
        """
        Run the full procurement pipeline for a given request.
        """
        start_time = time.time()
        
        def update_progress(msg: str, pct: int):
            if on_progress:
                on_progress(msg, pct)
            log.info(f"[{pct}%] {msg}")

        # ── 1. Parse Requirements (5%) ───────────────────────────────────────
        update_progress("Parsing requirements...", 5)
        req = parse_requirement(request_text)
        req_dict = requirement_to_dict(req)

        # ── 2. Build Queries (10%) ───────────────────────────────────────────
        update_progress("Building search queries...", 10)
        queries = build_search_queries(req)
        if not queries:
            return self._empty_result("Could not understand requirements - please be more specific.", 0)

        # ── 3. Start Scraping (15%) ──────────────────────────────────────────
        update_progress(f"Launching browsers for queries: {', '.join(queries[:2])}", 15)
        
        # Search Amazon and Flipkart concurrently for the primary queries
        update_progress("Searching Amazon and Flipkart concurrently...", 20)
        
        # We run only the first query to save strict rate limits (max 5 per platform)
        scrape_tasks = [run_scraper(q) for q in queries[:1]]
        scrape_results = await asyncio.gather(*scrape_tasks)
        
        all_products = []
        existing_ids = set()
        
        for res in scrape_results:
            for p in res.get("products", []):
                pid = p.get("product_id")
                if pid and pid not in existing_ids:
                    all_products.append(p)
                    existing_ids.add(pid)
                elif not pid: # Fallback for items without ID
                    all_products.append(p)

        # ── 5. Normalise and Evaluate (50%+) ─────────────────────────────────
        update_progress(f"Scraping complete. Found {len(all_products)} products. Starting evaluation...", 50)
        
        approved = []
        rejected = []
        
        if not all_products:
            return self._empty_result("No products found for this query on Amazon or Flipkart.", time.time() - start_time)

        # Process each product
        num_products = len(all_products)
        for i, product in enumerate(all_products):
            # Update progress between 55% and 95%
            pct = 55 + int((i / num_products) * 40)
            update_progress(f"Evaluating product {i+1}/{num_products}: {product.get('title', '')[:40]}...", pct)

            # Normalise
            normalised_product = self.normaliser.normalise_product(product)
            
            # Evaluate via LLM
            evaluation = await self.evaluator.evaluate(req_dict, normalised_product)
            
            # Combine data
            result_item = {
                "product": normalised_product,
                "evaluation": evaluation
            }

            if evaluation.get("verdict") == "APPROVED":
                approved.append(result_item)
            else:
                rejected.append(result_item)

            # --- RATE LIMIT PACING ---
            # Groq limits requests based on TPM/RPM. Wait 1 second after each 
            # evaluation to strictly maintain a safe pacing and avoid 429 warnings.
            if i < num_products - 1:
                await asyncio.sleep(1.1)

        # ── 6. Final Result (100%) ───────────────────────────────────────────
        update_progress("Search and evaluation complete!", 100)
        
        # Identify alternatives (always check for alternatives, even if approved products exist)
        suggested_alternatives = []
        for item in rejected:
            fails = item.get("evaluation", {}).get("failed_specs", [])
            # Lowercase all failed spec names to check for 'brand'
            fails_lower = [f.lower() for f in fails]
            # If Brand is the ONLY failed spec, it's a valid alternative suggestion
            if len(fails) == 1 and "brand" in fails_lower[0]:
                suggested_alternatives.append(item)

        return {
            "status": "success",
            "search_summary": {
                "total_inspected": num_products,
                "approved_count": len(approved),
                "rejected_count": len(rejected),
                "queries_used": queries[:2]
            },
            "requirements": req_dict,
            "approved": approved,
            "rejected": rejected,
            "suggested_alternatives": suggested_alternatives,
            "execution_time": float(f"{(time.time() - start_time):.2f}")
        }

    def _empty_result(self, message: str, elapsed: float) -> Dict[str, Any]:
        return {
            "status": "error",
            "message": message,
            "search_summary": {
                "total_inspected": 0,
                "approved_count": 0,
                "rejected_count": 0,
                "queries_used": []
            },
            "requirements": {},
            "approved": [],
            "rejected": [],
            "suggested_alternatives": [],
            "execution_time": float(f"{elapsed:.2f}")
        }
