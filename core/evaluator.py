"""
core/evaluator.py — LLM Evaluator
==================================================
Uses Groq (via LangChain) to strictly evaluate each scraped product
against the procurement requirements using Structured Outputs and TOML
compression for optimal token usage.
"""

import os
import json
import logging
import asyncio
from typing import Dict, Any, List
from dotenv import load_dotenv

from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from core.utils import dict_to_toml

load_dotenv()

log = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

class EvaluationResult(BaseModel):
    verdict: str = Field(description="Must be exactly 'APPROVED' or 'REJECTED'")
    reason: str = Field(description="One sentence explaining the decision. If rejected due to price, explicitly state that.")
    matched_specs: Dict[str, str] = Field(description="Dictionary of specs that passed the constraints.")
    failed_specs: List[str] = Field(description="List of constraint fields that failed or were missing.")
    confidence: str = Field(description="'high', 'medium', or 'low'")

class LLMEvaluator:
    """
    Evaluates a product against requirements using LangChain + Groq.
    """

    SYSTEM_PROMPT = """
You are an IT Procurement Verification Agent. Evaluate each product SPEC BY SPEC.

STEP 1 — CHECKLIST: For every field in USER REQUIREMENTS, look it up in this order:
  (a) NORMALISED SPECS section (these are authoritative — use for all numeric comparisons)
  (b) Raw SCRAPED PRODUCT DATA
  (c) Product title as last resort
  Mark each field: PASS / FAIL / NOT-IN-REQUIREMENTS / MISSING

STEP 2 — VERDICT RULES (in strict priority order):
  1. If Price > max_price -> REJECTED. Reason must mention price. No exceptions.
  2. If RAM normalised value is less than required -> REJECTED. (If GREATER or equal to required -> PASS).
  3. If Storage normalised value is less than required -> REJECTED. (If GREATER or equal to required -> PASS).
  4. If Processor generation is below minimum -> REJECTED. (If HIGHER or equal -> PASS).
  5. If Brand required and product is not any of the listed brands -> REJECTED.
     BUT only add "Brand" to failed_specs if ALL other specs (RAM, Storage, Processor, Price) PASS.
  6. If a required spec is MISSING from both NORMALISED and raw data -> REJECTED.
  7. If all required specs PASS -> APPROVED.

ANTI-HALLUCINATION RULES:
  - Never invent a spec that is not in the data.
  - Never fail a spec that was NOT in USER REQUIREMENTS.
  - Do not penalise missing specs the user never asked for.
  - Never add vague failures like "incomplete specs" or "insufficient data".
  - "16GB" in context of storage is physically impossible — it means RAM. Do not confuse them.
  - 1TB is EXACTLY equal to 1024GB and 1000GB. If the user asks for 1024GB or 1TB Storage, and the product has 1TB, it is a PERFECT MATCH! DO NOT REJECT IT.
  - Screen size within ±0.6 inches of requested = PASS. So 15.6" matches 15", 16", and vice versa.
  - FHD / 1920x1080 / 1080p / Full HD = identical. Never fail one for another.
  - Windows 11 / Win 11 = identical.
  - i5-1235U, i5-1334U, i5-1340P are all "Core i5" — never reject on the specific model suffix.
  - Gen detection: first 2 digits of model number = generation (e.g. i5-1235U → gen 12, i7-13700H → gen 13).
  - "12th gen or higher" means 12th, 13th, 14th, 15th gen all PASS. Only generations BELOW 12 should FAIL.
  - DDR4/DDR5/LPDDR5 are RAM keywords. SSD/NVMe/HDD are Storage keywords. Never mix them.

CONFIDENCE RULES:
  - confidence = "high" when all key specs are present in NORMALISED SPECS.
  - confidence = "medium" when some specs are inferred from raw text.
  - confidence = "low" only when critical specs (RAM, storage, price) are completely absent.
  - A low-confidence APPROVED must list all inferred specs in matched_specs with "(inferred)" suffix.
"""

    def __init__(self, model_name: str = "llama-3.3-70b-versatile"):
        self.model_name = model_name
        self.client_configured = bool(GROQ_API_KEY)
        if self.client_configured:
            llm = ChatGroq(model=self.model_name, temperature=0, groq_api_key=GROQ_API_KEY)
            self.structured_llm = llm.with_structured_output(EvaluationResult)
            self.prompt_template = ChatPromptTemplate.from_messages([
                ("system", self.SYSTEM_PROMPT),
                ("human", "{user_prompt}")
            ])
            self.chain = self.prompt_template | self.structured_llm

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

        # Convert the dictionaries to compressed TOML strings to save tokens
        req_toml = dict_to_toml(requirement_dict)
        
        # Extract the normalised keys to a clean dict for the LLM
        normalised_summary = {
            k.replace("_","").replace("normalised","").strip(): v
            for k, v in product_dict.get("specs", {}).items()
            if k.startswith("_") and v
        }
        
        # Product dict has title, price, specs, etc. We just convert the top level dict
        # Strip _normalised keys from the raw specs sent to LLM to avoid confusion
        raw_specs = {
            k: v for k, v in product_dict.get("specs", {}).items()
            if not k.startswith("_")
        }
        compressed_product_dict = {
            "title": product_dict.get('title'),
            "price_raw": product_dict.get('price_raw'),
            "platform": product_dict.get('platform'),
            "url": product_dict.get('url'),
            "specs": raw_specs
        }
        prod_toml = dict_to_toml(compressed_product_dict)

        # Prepare the prompt payload
        user_prompt = f"""
USER REQUIREMENTS (TOML format):
{req_toml}

SCRAPED PRODUCT DATA (TOML format):
{prod_toml}

PRE-PARSED NORMALISED SPECS (use these for numeric comparisons — they are authoritative):
{dict_to_toml(normalised_summary)}

Evaluate this product. Use the NORMALISED SPECS section for all numeric comparisons (RAM, storage, price, screen size, processor generation). Use the raw specs only to verify brand and panel type. BE STRICT.
"""

        # Retry logic with exponential backoff for rate limits
        for attempt in range(5):
            try:
                # Async invoke
                response_obj = await self.chain.ainvoke({"user_prompt": user_prompt})
                result = response_obj.model_dump()
                
                # ── Confidence gating ─────────────────────────────────────────
                # If the LLM is uncertain, re-evaluate with a harder prompt
                if result.get("confidence") == "low" and attempt == 0:
                    harder_prompt = user_prompt + """

IMPORTANT: You returned 'low' confidence. Re-read every spec carefully.
- Do NOT leave failed_specs empty if you are unsure.
- If data is missing for a required spec, it is a FAILURE.
- If you cannot confirm a spec matches, mark it failed.
- Do NOT return 'low' confidence again.
Give a definitive verdict now.
"""
                    response_obj2 = await self.chain.ainvoke({"user_prompt": harder_prompt})
                    result2 = response_obj2.model_dump()
                    
                    # Only adopt phase 2 if confidence improved
                    if result2.get("confidence") in ("high", "medium"):
                        result = result2
                    result["_phase2_reviewed"] = True

                return result
                
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
