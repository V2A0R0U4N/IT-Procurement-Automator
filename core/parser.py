"""
core/parser.py — Requirement Parser
======================================
Converts plain-English procurement requests into structured ProcurementRequirement objects.
Uses regex-based extraction for all fields.
"""

import re
from dataclasses import dataclass, field
from typing import Optional


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


def parse_requirement(text: str) -> ProcurementRequirement:
    """
    Parse a natural language or structured procurement request into
    a ProcurementRequirement object.

    Handles inputs like:
    - "Brand must be Asus or Samsung"
    - "I need an Asus laptop with at least 16 gigs of RAM"
    - "Processor must be Intel Core i5 (12th Gen or higher)"
    """
    req = ProcurementRequirement(raw_text=text)
    lower = text.lower()

    # ── Category ─────────────────────────────────────────────────────────────
    categories = {
        "laptop":   ["laptop", "laptops", "notebook", "notebooks", "macbook", "macbooks"],
        "monitor":  ["monitor", "monitors", "display", "displays", "screen"],
        "keyboard": ["keyboard", "keyboards"],
        "mouse":    ["mouse", "mice"],
        "desktop":  ["desktop", "desktops", "pc", "workstation"],
        "printer":  ["printer", "printers"],
        "headset":  ["headset", "headsets", "headphone", "headphones"],
    }
    for cat, keywords in categories.items():
        if any(kw in lower for kw in keywords):
            req.category = cat
            break

    # ── Brands ───────────────────────────────────────────────────────────────
    # Pattern: "Brand must be Asus or Samsung", "Brand: Asus, Samsung"
    brand_match = re.search(
        r"brand\s*(?:must\s+be|should\s+be|:|is|=)\s*(.+?)(?:\n|$|\.)",
        lower
    )
    if brand_match:
        brand_text = brand_match.group(1)
        # Split on "or", "and", "/", ","
        parts = re.split(r"\s+or\s+|\s+and\s+|[/,]", brand_text)
        req.brands = [p.strip().title() for p in parts if p.strip()]
    else:
        # Try to find brand names directly in text
        known_brands = [
            "Asus", "Samsung", "Dell", "HP", "Lenovo", "Acer", "MSI",
            "Apple", "LG", "BenQ", "ViewSonic", "Logitech", "Razer",
            "Microsoft", "Sony", "Toshiba", "Gigabyte",
        ]
        for brand in known_brands:
            if brand.lower() in lower:
                req.brands.append(brand)
        if "macbook" in lower or "mac mini" in lower or "imac" in lower or "ipad" in lower:
            if "Apple" not in req.brands:
                req.brands.append("Apple")

    # ── Processor Model ──────────────────────────────────────────────────────
    proc_match = re.search(
        r"(?:intel\s+)?(?:core\s+)?(i[3579]|ryzen\s*\d|m[1-4](?:\s*pro|\s*max)?)",
        lower
    )
    if proc_match:
        model = proc_match.group(1).strip()
        # Normalise: "i5" → "i5", "ryzen 5" → "Ryzen 5", "m4" -> "M4"
        if model.startswith("i"):
            req.processor_model = model
        elif model.startswith("m") and len(model) >= 2 and model[1].isdigit():
            req.processor_model = model.upper()
        else:
            req.processor_model = model.title()

    # ── Processor Generation ─────────────────────────────────────────────────
    gen_match = re.search(r"(\d+)(?:st|nd|rd|th)\s*gen", lower)
    if gen_match:
        req.processor_gen_min = int(gen_match.group(1))

    # ── RAM ──────────────────────────────────────────────────────────────────
    # Look closely for RAM keywords to avoid grabbing Storage Size (like 512gb)
    ram_match = re.search(r"(\d+)\s*(?:gb|gigs?)\s+(?:ram|memory|ddr\d*|unified)", lower)
    if ram_match:
        req.ram_gb = int(ram_match.group(1))
    else:
        # Fallback: Check if there's a standalone standalone GB value that looks purely like RAM (4, 8, 16, 32, 64)
        for val_str in re.findall(r"(\d+)\s*(?:gb|gigs?)", lower):
            val = int(val_str)
            if val in [4, 8, 16, 32, 64]:
                req.ram_gb = val
                break

    # ── Storage ──────────────────────────────────────────────────────────────
    # Make sure we don't accidentally grab "16" in "16GB RAM and 512GB SSD"
    # By looking specifically for sizes like 128, 256, 512, 1000, 1024, or requiring a storage keyword
    # We first look for a number near a storage keyword
    storage_kw_match = re.search(r"(\d+)\s*(?:gb|tb)\s*(?:ssd|hdd|nvme)", lower)
    if storage_kw_match:
        val = int(storage_kw_match.group(1))
        unit_text = storage_kw_match.group(0).lower()
        if "tb" in unit_text:
            val *= 1024
        req.storage_gb = val
    else:
        # If no explicit keyword, look for generic GB/TB, but be careful of RAM
        all_matches = re.findall(r"(\d+)\s*(?:gb|tb)", lower)
        for val_str in all_matches:
            val = int(val_str)
            if val >= 100 and val not in [128, 256, 512, 1000, 1024] and val != int(val_str):
                continue
            idx = lower.find(val_str)
            if idx != -1:
                try:
                    after_str = lower.split(val_str, 1)[1]
                    is_tb = after_str.strip().startswith("tb")
                    if val >= 120 or is_tb:
                        if is_tb:
                            req.storage_gb = val * 1024
                        else:
                            req.storage_gb = val
                        break
                except Exception:
                    pass

    # Storage type
    if "nvme" in lower:
        req.storage_type = "NVMe"
    elif "ssd" in lower:
        req.storage_type = "SSD"
    elif "hdd" in lower:
        req.storage_type = "HDD"

    # ── Screen Size ──────────────────────────────────────────────────────────
    screen_match = re.search(r"(\d+\.?\d*)\s*(?:inch|inches|\"|in\b|'')", lower)
    if screen_match:
        req.screen_size_inches = float(screen_match.group(1))

    # ── Resolution ───────────────────────────────────────────────────────────
    res_patterns = {
        "4K":    [r"4k", r"uhd", r"2160p?", r"3840\s*x\s*2160"],
        "2K":    [r"2k", r"qhd", r"1440p?", r"2560\s*x\s*1440"],
        "1080p": [r"1080p?", r"fhd", r"full\s*hd", r"1920\s*x\s*1080"],
        "HD":    [r"\b720p?\b", r"\bhd\b"],
    }
    for res_name, patterns in res_patterns.items():
        if any(re.search(p, lower) for p in patterns):
            req.resolution = res_name
            break

    # ── Panel Type ───────────────────────────────────────────────────────────
    panel_match = re.search(r"\b(ips|va|tn|oled|amoled)\b", lower)
    if panel_match:
        req.panel_type = panel_match.group(1).upper()

    # ── Price ────────────────────────────────────────────────────────────────
    price_match = re.search(
        r"(?:under|below|max(?:imum)?|within|budget|less\s+than|up\s+to)"
        r"\s*[₹$]?\s*([\d,]+(?:\.\d+)?)",
        lower
    )
    if price_match:
        price_str = price_match.group(1).replace(",", "")
        try:
            req.max_price = float(price_str)
        except ValueError:
            pass

    # Detect currency
    if "$" in text or "usd" in lower:
        req.currency = "USD"
    else:
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
