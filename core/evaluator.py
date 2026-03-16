"""
core/evaluator.py — LLM Evaluator (Gemini)
==================================================
Uses Gemini 1.5 Flash to strictly evaluate each scraped product
against the procurement requirements.
"""

import os
import re
import json
import logging
import asyncio
from typing import Dict, Any
from dotenv import load_dotenv
from groq import AsyncGroq

load_dotenv()

log = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

class LLMEvaluator:
    """
    Evaluates a product against requirements using Gemini 1.5 Flash.
    """

    SYSTEM_PROMPT = """
You are an IT Procurement Verification Agent. Your job is to strictly evaluate if a scraped product EXACTLY matches the user's requirements.

EVALUATION RULES — follow strictly, no exceptions:
1. ONLY evaluate the product against the specific fields provided in 'USER REQUIREMENTS'.
2. MISSING SPECS: If a spec (like screen size, panel type, refresh rate, etc.) is NOT explicitly mentioned in 'USER REQUIREMENTS', DO NOT reject the product because of it. IGNORE IT entirely. A product is not "missing" a spec if the user didn't ask for it.
   - Example: DO NOT reject because "Screen size is 0" or "Screen size missing" if it wasn't requested. Just assume it passes.
3. FAILURES: If a required spec IS in 'USER REQUIREMENTS' but is MISSING or WRONG in the product data → REJECTED.
4. PRICE HIERARCHY: Price is the most important constraint. If the product's price exceeds the maximum price specified by the user, the reason for rejection MUST be the price.
5. TECHNICAL TERMINOLOGY & HUMAN LOGIC (CRITICAL):
   - RAM vs STORAGE CONFUSION: NEVER confuse RAM and Storage! "DDR4", "DDR5", "LPDDR5", "LPDDR4x", "Memory", "SDRAM" ALWAYS mean RAM (e.g. 8GB, 16GB, 32GB). "SSD", "HDD", "NVMe", "PCIe", "Solid State Drive", "eMMC" ALWAYS mean Storage (e.g. 256GB, 512GB, 1TB, 2TB). 
   - 🚨 **CRITICAL RULE**: Storage is ALMOST ALWAYS 128GB, 256GB, 512GB, 1TB. RAM is almost always 4GB, 8GB, 16GB, 32GB. If you think the user asked for "16GB Storage" or "16GB SSD", YOU ARE WRONG. They asked for 16GB RAM. Do not reject a 512GB SSD requirement just because you see the number 16 next to the word GB!
   - 🚨 **STRICT STORAGE MATCHING**: If the user asks for 512GB Storage, a 256GB product is a FAILURE. You must REJECT IT. If the product title says 256GB, and the requirement is 512GB, REJECT IT. If the product data has NO storage information at all, and the user required 512GB, REJECT IT.
   - PROCESSOR GENERATIONS: "1235U", "1255U", "12700H" all mean "12th Gen". "1334U", "13500H" mean "13th Gen". "14700HX" means "14th Gen", and so on. If '12th Gen or higher' is required, 12th, 13th, 14th Gen are all matches.
   - PROCESSOR TIERS: If "Core i5" is required, "i5-1235U" and "i5-1334U" are perfect matches. DO NOT reject them.
   - RESOLUTION: "FHD", "1920x1080", "1080p", "1920 x 1080 pixels" are all exactly the SAME thing. Do not reject one for the other.
   - OS: "Windows 11 Home", "Win 11", "Windows 11" are the same.
   - SCREEN SIZE DECIMAL ALLOWANCE: Screen sizes have decimal variations (e.g. 15.6" vs 15", 14" vs 14.1"). DO NOT reject a laptop if the screen size is within 0.6 inches of the requested size. For example, if the user asks for 15 inch, accept 15.6 inch. If the user asks for 16 inch, accept 15.6 inch.
6. BRAND MATCHING: If multiple brands are given in requirements (e.g. "Asus, Acer"), the product MUST be one of them. Do not reject if it matched either one. 
7. ALTERNATIVE BRANDS: If a product PERFECTLY matches all requirements (Price, RAM, Storage, CPU, etc.) but fails ONLY on the "Brand" requirement, you must still output "REJECTED" but make sure that "Brand" is the EXACT and ONLY item in "failed_specs". Do not hallucinate other failures like RAM or Storage if they actually match.
8. Be conservative but fair. Do not hallucinate failures for requirements that were never stated. Do not punish the product if the scraper formatted a spec weirdly but a human would understand it matches.

You must output valid JSON only. Output a JSON object with this structure:
{
  "verdict": "APPROVED" or "REJECTED",
  "reason": "One sentence explaining the decision. If rejected due to price, explicitly state that.",
  "matched_specs": {"Field": "Value"},
  "failed_specs": ["list of fields that failed or were missing"],
  "confidence": "high" or "medium" or "low"
}
"""

    def __init__(self, model_name: str = "llama-3.3-70b-versatile"):
        self.model_name = model_name
        self.client_configured = bool(GROQ_API_KEY)
        if self.client_configured:
            self.client = AsyncGroq(api_key=GROQ_API_KEY)

    async def evaluate(self, requirement_dict: Dict[str, Any], product_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate a single product against requirements.
        Returns the verdict dict.
        """
        if not self.client_configured:
            return {
                "verdict": "REJECTED",
                "reason": "LLM evaluation unavailable (missing Groq API key)",
                "matched_specs": {},
                "failed_specs": ["API Key"],
                "confidence": "high"
            }

        # Prepare the prompt
        prompt = f"""
USER REQUIREMENTS:
{json.dumps(requirement_dict, indent=2)}

SCRAPED PRODUCT DATA:
Title: {product_dict.get('title')}
Price: {product_dict.get('price_raw')} (Numeric: {product_dict.get('price_num')})
Platform: {product_dict.get('platform')}
URL: {product_dict.get('url')}

SPECIFICATIONS (including normalised values):
{json.dumps(product_dict.get('specs', {}), indent=2)}

Evaluate this product. BE STRICT. Output JSON ONLY.
"""

        # Retry logic with exponential backoff for rate limits
        for attempt in range(5):
            try:
                chat_completion = await self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0
                )
                
                response_text = chat_completion.choices[0].message.content
                if response_text:
                    return self._parse_llm_response(response_text)
                
            except Exception as e:
                err_str = str(e).lower()
                if "rate limit" in err_str or "429" in err_str:
                    log.warning(f"Groq API rate limit hit (attempt {attempt+1}): {e}")
                else:
                    log.warning(f"Groq API attempt {attempt+1} failed: {e}")
                
                if attempt < 4:
                    wait_time = (2 ** attempt) * 2  # 2, 4, 8, 16 seconds
                    log.info(f"Sleeping for {wait_time}s before retrying...")
                    await asyncio.sleep(wait_time)
                else:
                    log.error("Groq API totally failed after 5 attempts.")

        return {
            "verdict": "REJECTED",
            "reason": "LLM evaluation failed after retries",
            "matched_specs": {},
            "failed_specs": ["LLM API Error"],
            "confidence": "high"
        }

    def _parse_llm_response(self, raw: str) -> Dict[str, Any]:
        """Robustly parse JSON from LLM response."""
        try:
            return json.loads(raw)
        except Exception as e:
            # Fallback to regex if LLM adds chatter despite json_object mode
            try:
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            except:
                pass
                
            log.error(f"Failed to parse LLM response: {e}\nRaw response: {raw}")
            return {
                "verdict": "REJECTED",
                "reason": f"System error: Failed to parse evaluator response",
                "matched_specs": {},
                "failed_specs": ["Parsing Error"],
                "confidence": "low"
            }
