"""
core/prefilter.py
Pre-filter: eliminates obvious rejects before touching the LLM.
Cuts ~40% of Groq calls, prevents rate-limit pile-ups on garbage products.
Returns (passes: bool, reason: str, field: str).
"""
import re
from typing import Dict, Any, Tuple

# Category word sets for sanity checking
CATEGORY_MAP = {
    "laptop":  {"laptop", "notebook", "ultrabook", "chromebook", "macbook", "laptops"},
    "monitor": {"monitor", "display", "screen", "monitors"},
    "desktop": {"desktop", "pc", "tower", "workstation"},
    "printer": {"printer", "printing", "inkjet", "laserjet"},
    "tablet":  {"tablet", "ipad", "tab"},
}


class PreFilter:
    PRICE_TOLERANCE = 1.05   # allow 5% over max price as buffer for scraped noise

    def check(self, req: Dict[str, Any], product: Dict[str, Any]) -> Tuple[bool, str, str]:
        """
        Returns (passes: bool, reason: str, field: str).
        Only fails on hard numeric mismatches that are unambiguous.
        """
        specs = product.get("specs", {})

        # ── 1. Price hard-cap ─────────────────────────────────────────────
        max_price = req.get("Max Price")
        if max_price:
            max_price_num = self._parse_price_from_req(str(max_price))
            price_num = product.get("price_num", 0.0)
            if price_num > 0 and max_price_num > 0 and price_num > max_price_num * self.PRICE_TOLERANCE:
                return False, f"Price ₹{price_num:,.0f} exceeds max ₹{max_price_num:,.0f}", "Price"

        # ── 2. RAM hard mismatch ──────────────────────────────────────────
        req_ram = req.get("RAM")           # e.g. "16GB"
        if req_ram:
            req_ram_gb = self._parse_gb(str(req_ram))
            norm_ram = specs.get("_ram_normalised", "")  # e.g. "8GB"
            if norm_ram and req_ram_gb > 0:
                prod_ram_gb = self._parse_gb(norm_ram)
                if prod_ram_gb > 0 and prod_ram_gb < req_ram_gb:
                    return False, f"RAM {prod_ram_gb}GB < required {req_ram_gb}GB", "RAM"

        # ── 3. Storage hard mismatch ──────────────────────────────────────
        req_storage = req.get("Storage")   # e.g. "512GB SSD"
        if req_storage:
            req_storage_gb = self._parse_gb(str(req_storage))
            norm_storage = specs.get("_storage_size_normalised", "")  # e.g. "256GB"
            if norm_storage and req_storage_gb > 0:
                prod_storage_gb = self._parse_gb(norm_storage)
                if prod_storage_gb > 0 and prod_storage_gb < req_storage_gb:
                    return (False,
                            f"Storage {self._format_storage(prod_storage_gb)} < required {self._format_storage(req_storage_gb)}",
                            "Storage")

        # ── 4. Processor generation hard mismatch ────────────────────────
        req_gen_min = req.get("Min Processor Generation")
        if req_gen_min:
            try:
                req_gen_num = int(re.sub(r"[^0-9]", "", str(req_gen_min)))
                norm_gen = specs.get("_processor_gen_normalised", "")
                if norm_gen:
                    prod_gen = int(norm_gen)
                    if prod_gen < req_gen_num:
                        return False, f"Processor Gen {prod_gen} < required Gen {req_gen_num}", "Processor"
            except ValueError:
                pass

        # ── 5. Category sanity check ─────────────────────────────────────
        req_category = req.get("Category", "").lower().strip()
        if req_category and req_category in CATEGORY_MAP:
            right_words = CATEGORY_MAP[req_category]
            title_lower = product.get("title", "").lower()
            has_right = any(w in title_lower for w in right_words)
            # Check if title contains a word from a WRONG category
            has_wrong = False
            for other_cat, other_words in CATEGORY_MAP.items():
                if other_cat != req_category:
                    if any(w in title_lower for w in other_words):
                        has_wrong = True
                        break
            if has_wrong and not has_right:
                return False, f"Product category doesn't match required '{req_category}'", "Category"

        return True, "passed", ""

    @staticmethod
    def _parse_gb(value: str) -> int:
        """
        Parse a storage/RAM string to GB integer.
        '512GB' → 512, '512GB SSD' → 512, '1TB' → 1024, '16GB' → 16
        """
        value = str(value).strip().upper()
        # Check TB first
        tb_match = re.search(r"(\d+)\s*TB", value)
        if tb_match:
            return int(tb_match.group(1)) * 1024
        # Then GB
        gb_match = re.search(r"(\d+)\s*GB", value)
        if gb_match:
            return int(gb_match.group(1))
        return 0

    @staticmethod
    def _parse_price_from_req(price_str: str) -> float:
        """Parse price string like '₹65,000' → 65000.0"""
        cleaned = re.sub(r"[₹$£€,\s]", "", price_str)
        m = re.search(r"[\d.]+", cleaned)
        try:
            return float(m.group(0)) if m else 0.0
        except ValueError:
            return 0.0

    @staticmethod
    def _format_storage(gb: int) -> str:
        """Convert GB value to human-readable: 1024 → '1TB', 512 → '512GB'."""
        if gb >= 1024 and gb % 1024 == 0:
            return f"{gb // 1024}TB"
        return f"{gb}GB"
