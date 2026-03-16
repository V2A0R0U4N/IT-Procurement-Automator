"""
core/parser.py — Requirement Parser
======================================
Converts plain-English procurement requests into structured ProcurementRequirement objects.
Uses LangChain and Groq LLM (llama-3.1-8b-instant) for robust NLP extraction.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional, List
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ProcurementRequirement:
    """Structured representation of a hardware procurement request."""
    category:           str            = ""
    brands:             list[str]      = field(default_factory=list)
    processor_model:    str            = ""       # i3, i5, i7, i9, Ryzen 5, etc.
    processor_gen_min:  Optional[int]  = None     # minimum generation number
    ram_gb:             Optional[int]  = None     # exact RAM in GB
    storage_gb:         Optional[int]  = None     # storage size in GB
    storage_type:       str            = ""       # SSD, HDD, NVMe
    screen_size_inches: Optional[float] = None    # e.g. 27.0
    resolution:         str            = ""       # 4K, 2K, 1080p, HD
    panel_type:         str            = ""       # IPS, VA, TN, OLED
    max_price:          Optional[float] = None    # numeric price cap
    currency:           str            = "INR"    # INR or USD
    raw_text:           str            = ""       # original input for reference


class LLMProcurementRequirement(BaseModel):
    category: str = Field(default="", description="The product category like laptop, monitor, desktop, printer. Default to empty if unknown. 'MacBook' is a laptop.")
    brands: List[str] = Field(default_factory=list, description="List of recognized brands requested. If MacBook or iPad is mentioned, include 'Apple'.")
    processor_model: str = Field(default="", description="The processor model (e.g., 'i5', 'i7', 'Ryzen 5', 'M1', 'M4'). Standardize Apple Silicon (m4 -> M4).")
    processor_gen_min: Optional[int] = Field(default=None, description="The minimum processor generation (e.g., 12 for 12th gen).")
    ram_gb: Optional[int] = Field(default=None, description="The requested RAM size in GB (e.g., 8, 16, 32). MUST NOT be confused with storage. Standard memory sizes: 4, 8, 16, 32, 64.")
    storage_gb: Optional[int] = Field(default=None, description="The requested Storage size in GB (e.g., 256, 512, 1024). MUST NOT be confused with RAM. Valid storage sizes: 128, 256, 512, 1000, 1024, 2000, etc.")
    storage_type: str = Field(default="", description="The storage type (e.g., SSD, HDD, NVMe).")
    screen_size_inches: Optional[float] = Field(default=None, description="The requested screen size in inches (e.g., 15.6, 27.0).")
    resolution: str = Field(default="", description="The resolution (e.g., 4K, 2K, 1080p, HD).")
    panel_type: str = Field(default="", description="The display panel type (e.g., IPS, VA, OLED, TN).")
    max_price: Optional[float] = Field(default=None, description="The maximum price in numeric format without currency symbols.")
    currency: str = Field(default="INR", description="The currency specified, 'INR' or 'USD'. Defaults to 'INR'.")


_parser_llm = None

def get_parser_llm():
    global _parser_llm
    if _parser_llm is None:
        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            raise ValueError("GROQ_API_KEY environment variable is not set.")
        # Setup Langchain LLM with structure output
        _llm = ChatGroq(model="llama-3.1-8b-instant", temperature=0, groq_api_key=groq_api_key)
        _parser_llm = _llm.with_structured_output(LLMProcurementRequirement)
    return _parser_llm


def parse_requirement(text: str) -> ProcurementRequirement:
    """
    Parse a natural language or structured procurement request into
    a ProcurementRequirement object.
    
    Uses LangChain to perfectly map ambiguous request language.
    """
    parser = get_parser_llm()
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an expert IT Procurement requirement extractor. Your job is to extract exact specifications requested by the user. Pay extreme attention to the context. A 'MacBook' implies an 'Apple' 'laptop'. '16GB' implies RAM, '512GB' implies Storage. Do not hallucinate fields that were not explicitly mentioned. For missing fields, return null or empty string."),
        ("human", "{text}")
    ])
    
    chain = prompt | parser
    extracted_pydantic = chain.invoke({"text": text})
    
    req = ProcurementRequirement(
        category=extracted_pydantic.category,
        brands=extracted_pydantic.brands,
        processor_model=extracted_pydantic.processor_model,
        processor_gen_min=extracted_pydantic.processor_gen_min,
        ram_gb=extracted_pydantic.ram_gb,
        storage_gb=extracted_pydantic.storage_gb,
        storage_type=extracted_pydantic.storage_type,
        screen_size_inches=extracted_pydantic.screen_size_inches,
        resolution=extracted_pydantic.resolution,
        panel_type=extracted_pydantic.panel_type,
        max_price=extracted_pydantic.max_price,
        currency=extracted_pydantic.currency,
        raw_text=text
    )
    
    # Fallback to INR if USD wasn't mentioned
    if "usd" not in text.lower() and "$" not in text:
        req.currency = "INR"
        
    return req


def build_search_queries(req: ProcurementRequirement) -> list[str]:
    """
    Build search queries from a parsed requirement.
    Returns primary query + up to 2 fallbacks (broadest last).
    """
    queries = []

    # ── Primary query — most specific ────────────────────────────────────────
    parts = []
    if req.brands:
        parts.append(req.brands[0])
    if req.category:
        parts.append(req.category)
    if req.processor_model:
        parts.append(f"Intel Core {req.processor_model}" if req.processor_model.startswith("i") else req.processor_model)
    if req.processor_gen_min:
        parts.append(f"{req.processor_gen_min}th gen")
    if req.ram_gb:
        parts.append(f"{req.ram_gb}GB RAM")
    if req.storage_gb and req.storage_type:
        parts.append(f"{req.storage_gb}GB {req.storage_type}")
    elif req.storage_gb:
        parts.append(f"{req.storage_gb}GB SSD")
    if req.screen_size_inches:
        parts.append(f"{req.screen_size_inches:.0f} inch")
    if req.resolution:
        parts.append(req.resolution)
    if req.panel_type:
        parts.append(req.panel_type)

    if parts:
        queries.append(" ".join(parts))

    # ── Fallback 1 — drop generation number ──────────────────────────────────
    fallback1_parts = [p for p in parts if "gen" not in p.lower()]
    fb1 = " ".join(fallback1_parts)
    if fb1 and fb1 != queries[0] if queries else True:
        queries.append(fb1)

    # ── Fallback 2 — broadest: brand + category + one key spec ───────────────
    broad_parts = []
    if req.brands:
        broad_parts.append(req.brands[0])
    if req.category:
        broad_parts.append(req.category)
    if req.ram_gb:
        broad_parts.append(f"{req.ram_gb}GB RAM")
    elif req.screen_size_inches:
        broad_parts.append(f"{req.screen_size_inches:.0f} inch")
    if req.resolution:
        broad_parts.append(req.resolution)

    fb2 = " ".join(broad_parts)
    if fb2 and fb2 not in queries:
        queries.append(fb2)

    # ── Extra queries for additional brands ──────────────────────────────────
    if len(req.brands) > 1 and queries:
        primary_brand = req.brands[0]
        primary_query = queries[0]
        for i in range(1, len(req.brands)):
            other_brand = req.brands[i]
            alt = primary_query.replace(primary_brand, other_brand)
            if alt not in queries:
                queries.append(alt)

    return queries


def requirement_to_dict(req: ProcurementRequirement) -> dict:
    """Convert requirement to a readable dict for display or LLM prompt."""
    result = {}
    if req.category:
        result["Category"] = req.category
    if req.brands:
        result["Brands"] = ", ".join(req.brands)
    if req.processor_model:
        result["Processor"] = req.processor_model
    if req.processor_gen_min is not None:
        result["Min Processor Generation"] = f"{req.processor_gen_min}th Gen"
    if req.ram_gb is not None:
        result["RAM"] = f"{req.ram_gb}GB"
    if req.storage_gb is not None:
        storage_label = f"{req.storage_gb}GB"
        if req.storage_type:
            storage_label += f" {req.storage_type}"
        result["Storage"] = storage_label
    if req.screen_size_inches is not None:
        result["Screen Size"] = f'{req.screen_size_inches}"'
    if req.resolution:
        result["Resolution"] = req.resolution
    if req.panel_type:
        result["Panel Type"] = req.panel_type
    if req.max_price is not None:
        symbol = "$" if req.currency == "USD" else "₹"
        result["Max Price"] = f"{symbol}{req.max_price:,.0f}"
    return result
