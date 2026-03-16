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
You are an IT Procurement Verification Agent. Your job is to strictly evaluate if a scraped product EXACTLY matches the user's requirements.

EVALUATION RULES — follow strictly, no exceptions:
1. ONLY evaluate the product against the specific fields provided in 'USER REQUIREMENTS'.
2. MISSING SPECS: If a spec (like screen size, panel type, refresh rate, etc.) is NOT explicitly mentioned in 'USER REQUIREMENTS', DO NOT reject the product because of it. IGNORE IT entirely. A product is not "missing" a spec if the user didn't ask for it.
   - Example: DO NOT reject because "Screen size is 0" or "Screen size missing" if it wasn't requested. Just assume it passes.
3. FAILURES: If a required spec IS in 'USER REQUIREMENTS' but is MISSING or WRONG in the product data -> REJECTED.
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
        
        # Product dict has title, price, specs, etc. We just convert the top level dict
        compressed_product_dict = {
            "title": product_dict.get('title'),
            "price_raw": product_dict.get('price_raw'),
            "platform": product_dict.get('platform'),
            "url": product_dict.get('url'),
            "specs": product_dict.get('specs', {})
        }
        prod_toml = dict_to_toml(compressed_product_dict)

        # Prepare the prompt payload
        user_prompt = f"""
USER REQUIREMENTS (TOML format):
{req_toml}

SCRAPED PRODUCT DATA (TOML format):
{prod_toml}

Evaluate this product. BE STRICT.
"""

        # Retry logic with exponential backoff for rate limits
        for attempt in range(5):
            try:
                # Async invoke
                response_obj = await self.chain.ainvoke({"user_prompt": user_prompt})
                # Convert the Pydantic instance back to dictionary for existing codebase
                return response_obj.model_dump()
                
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
