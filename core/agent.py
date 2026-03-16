"""
core/agent.py — Procurement Agent Orchestrator
===============================================
Ties everything together:
1. Parse requirement
2. Build queries
3. Scrape platforms concurrently
4. Normalise specs
5. Pre-filter obvious rejects
6. Evaluate products with LLM
7. Score alternatives
8. Return structured results
"""

import asyncio
import logging
import time
from typing import Callable, Optional, Dict, Any, List

from .parser import parse_requirement, build_search_queries, requirement_to_dict
from .normaliser import SpecNormaliser
from .evaluator import LLMEvaluator
from .prefilter import PreFilter
from scraper.browser import run_scraper

log = logging.getLogger(__name__)

# Spec weights for alternative scoring
SPEC_WEIGHTS = {
    "price": 40,
    "ram": 20,
    "storage": 15,
    "processor": 10,
    "brand": 5,
    "screen": 5,
    "resolution": 3,
    "panel": 2,
}


class ProcurementAgent:
    """
    Orchestrates the full procurement pipeline.
    """

    def __init__(self):
        self.normaliser = SpecNormaliser()
        self.evaluator = LLMEvaluator()
        self.prefilter = PreFilter()

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
        try:
            req = parse_requirement(request_text)
            req_dict = requirement_to_dict(req)
        except ValueError as e:
            return self._empty_result(str(e), time.time() - start_time)

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
                elif not pid:  # Fallback for items without ID
                    all_products.append(p)

        # ── 5. Normalise and Evaluate (50%+) ─────────────────────────────────
        update_progress(f"Scraping complete. Found {len(all_products)} products. Starting evaluation...", 50)
        
        approved = []
        rejected = []
        prefilter_knocked = 0
        llm_evaluated = 0
        
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
            
            # Pre-filter checks before touching the costly LLM
            passes, reason, field = self.prefilter.check(req_dict, normalised_product)
            
            if not passes:
                result_item = {
                    "product": normalised_product,
                    "evaluation": {
                        "verdict": "REJECTED",
                        "reason": f"[Pre-filter] {reason}",
                        "matched_specs": {},
                        "failed_specs": [field] if field else [reason.split(" ")[0]],
                        "confidence": "high",
                        "source": "prefilter"
                    }
                }
                rejected.append(result_item)
                prefilter_knocked += 1
                continue
            
            # Evaluate via LLM
            evaluation = await self.evaluator.evaluate(req_dict, normalised_product)
            llm_evaluated += 1
            
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

        # ── 6. Rank Alternatives ─────────────────────────────────────────────
        update_progress("Ranking alternatives...", 96)
        suggested_alternatives = self._rank_alternatives(rejected, req_dict)

        # Remove alternatives from rejected list so they don't appear in both tabs
        alt_ids = set()
        for alt in suggested_alternatives:
            alt_product = alt.get("product", {})
            alt_id = alt_product.get("product_id") or alt_product.get("title", "")
            alt_ids.add(alt_id)
        
        rejected = [
            item for item in rejected
            if (item.get("product", {}).get("product_id") or item.get("product", {}).get("title", "")) not in alt_ids
        ]

        # ── 7. Final Result (100%) ───────────────────────────────────────────
        update_progress("Search and evaluation complete!", 100)
        
        return {
            "status": "success",
            "search_summary": {
                "total_inspected": num_products,
                "prefilter_knocked": prefilter_knocked,
                "llm_evaluated": llm_evaluated,
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

    def _rank_alternatives(self, rejected: list, req_dict: dict) -> list:
        """
        Identify near-miss products as alternatives.
        Only LLM-rejected items that fail on Brand or Price are candidates.
        Pre-filter rejects (hard fails on core specs) are never alternatives.
        """
        possible_alts = []
        allowed_alt_fails = {"brand", "price"}

        for item in rejected:
            eval_data = item.get("evaluation", {})

            # Skip pre-filter rejects — they are hard fails, not near-misses
            if eval_data.get("source") == "prefilter":
                continue

            # Skip low-confidence items — unreliable verdicts
            if eval_data.get("confidence") == "low":
                continue

            fails = [str(f).lower() for f in eval_data.get("failed_specs", [])]
            
            # Check if all failures are allowed (e.g. only brand or price)
            if len(fails) > 0 and all(
                any(allowed in f for allowed in allowed_alt_fails) for f in fails
            ):
                scored = self._score_alternative(item, req_dict)
                possible_alts.append(scored)

        return sorted(
            possible_alts,
            key=lambda x: x["match_score"],
            reverse=True
        )[:5]  # top 5 alternatives, ranked

    def _score_alternative(self, item: dict, req_dict: dict) -> dict:
        """Score a rejected product as a partial match (0-100)."""
        eval_data = item.get("evaluation", {})
        failed = eval_data.get("failed_specs", [])
        
        # Calculate total weight from actual requirement keys
        total_weight = sum(
            self._weight_for_spec(k) for k in req_dict.keys()
        )
        failed_weight = sum(
            self._weight_for_spec(f) for f in failed
        )
        score = max(0, 100 - int((failed_weight / max(total_weight, 1)) * 100))
        
        return {
            **item,
            "match_score": score,
            "miss_reasons": failed
        }

    @staticmethod
    def _weight_for_spec(spec_name: str) -> int:
        """Map a spec name to its weight using substring matching."""
        spec_lower = str(spec_name).lower()
        for key, weight in SPEC_WEIGHTS.items():
            if key in spec_lower:
                return weight
        return 3  # default weight for unknown specs

    def _empty_result(self, message: str, elapsed: float) -> Dict[str, Any]:
        return {
            "status": "error",
            "message": message,
            "search_summary": {
                "total_inspected": 0,
                "prefilter_knocked": 0,
                "llm_evaluated": 0,
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
