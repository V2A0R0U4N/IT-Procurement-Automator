"""
core/normaliser.py — Spec Normaliser
=======================================
Normalises raw scraped product specs into standardised comparable values.
Must run BEFORE the LLM evaluator to eliminate string comparison issues.

Examples:
  "8 GB DDR4 3200MHz"  → "8GB"
  "512GB NVMe SSD"     → ("512GB", "SSD")
  "Intel Core i5-1235U" → generation 12
  "₹1,23,456"          → 123456.0
  "3840 x 2160"        → "4K"
"""

import re
import logging
from typing import Optional, Tuple

log = logging.getLogger(__name__)


class SpecNormaliser:
    """
    Normalises raw product specs and adds underscore-prefixed keys
    with the normalised values to the specs dict.
    """

    def normalise_product(self, product_dict: dict) -> dict:
        """
        Normalise all specs in a product dict (as returned by vars(ScrapedProduct)).
        Adds _normalised keys to the specs sub-dict.
        Returns the same dict with additions.
        """
        specs = product_dict.get("specs", {})

        # ── RAM ──────────────────────────────────────────────────────────────
        ram_raw = self._find_spec(specs, ["ram", "memory", "system memory", "ram memory"])
        if ram_raw:
            specs["_ram_normalised"] = self.normalise_ram(ram_raw)

        # ── Storage ──────────────────────────────────────────────────────────
        storage_raw = self._find_spec(specs, [
            "storage", "hard drive", "hard disk", "ssd", "hdd",
            "flash memory", "internal storage", "ssd capacity"
        ])
        if storage_raw:
            size, stype = self.normalise_storage(storage_raw)
            specs["_storage_size_normalised"] = size
            specs["_storage_type_normalised"] = stype

        # ── Processor / Generation ───────────────────────────────────────────
        proc_raw = self._find_spec(specs, [
            "processor", "cpu", "processor name", "processor brand",
            "processor type", "model number", "processor model number"
        ])
        # Also check the product title for processor info
        title = product_dict.get("title", "")
        combined_text = f"{proc_raw or ''} {title}"

        gen = self.normalise_processor_gen(combined_text)
        if gen:
            specs["_processor_gen_normalised"] = str(gen)

        proc_model = self.normalise_processor_model(combined_text)
        if proc_model:
            specs["_processor_model_normalised"] = proc_model

        # ── Price ────────────────────────────────────────────────────────────
        price_num = product_dict.get("price_num", 0.0)
        if price_num > 0:
            specs["_price_normalised"] = str(price_num)
        else:
            price_raw = product_dict.get("price_raw", "")
            if price_raw:
                specs["_price_normalised"] = str(self.normalise_price(price_raw))

        # ── Screen Size ──────────────────────────────────────────────────────
        screen_raw = self._find_spec(specs, [
            "screen size", "display size", "screen", "display",
            "monitor size", "standing screen display size"
        ])
        if screen_raw:
            size = self.normalise_screen_size(screen_raw)
            if size:
                specs["_screen_size_normalised"] = str(size)

        # ── Resolution ───────────────────────────────────────────────────────
        res_raw = self._find_spec(specs, [
            "resolution", "display resolution", "screen resolution",
            "native resolution", "max resolution"
        ])
        if res_raw:
            specs["_resolution_normalised"] = self.normalise_resolution(res_raw)

        # ── Panel Type ───────────────────────────────────────────────────────
        panel_raw = self._find_spec(specs, [
            "panel type", "display type", "display technology",
            "screen type", "panel technology"
        ])
        if panel_raw:
            specs["_panel_type_normalised"] = self.normalise_panel_type(panel_raw)

        # ── Brand ────────────────────────────────────────────────────────────
        brand_raw = self._find_spec(specs, ["brand", "manufacturer", "brand name"])
        if brand_raw:
            specs["_brand_normalised"] = brand_raw.strip().title()
        elif title:
            # Try extracting brand from title (first word usually)
            first_word = title.split()[0] if title.split() else ""
            if first_word and len(first_word) > 1:
                specs["_brand_normalised"] = first_word.strip().title()

        product_dict["specs"] = specs
        return product_dict

    # ─────────────────────────────────────────────────────────────────────────
    # Individual normalisers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def normalise_ram(raw: str) -> str:
        """'8 GB DDR4 3200MHz' → '8GB', '16384 MB' → '16GB'"""
        raw = raw.strip()
        # Remove DDRx/LPDDRx to avoid extracting '4' from DDR4
        clean_raw = re.sub(r"(?:LP)?(G)?DDR\d[X]?", "", raw, flags=re.I)
        
        # Check specifically for GB RAM / GB Memory to avoid grabbing "512" from "512GB SSD"
        specific_ram = re.search(r"(\d+)\s*GB\s*(?:RAM|Memory|System Memory)", clean_raw, re.I)
        if specific_ram:
            return f"{specific_ram.group(1)}GB"

        mb = re.search(r"(\d+)\s*MB", clean_raw, re.I)
        if mb:
            return f"{int(mb.group(1)) // 1024}GB"
            
        # Before falling back to generic GB, check if there are multiple GB values in the string.
        # RAM is usually 4, 8, 16, 32, 64.
        all_gb = re.findall(r"(\d+)\s*GB", clean_raw, re.I)
        for val in all_gb:
            num = int(val)
            if num in [4, 8, 12, 16, 24, 32, 64, 128]:
                return f"{num}GB"
                
        # If there's only one generic GB match, use it
        gb = re.search(r"(\d+)\s*GB", clean_raw, re.I)
        if gb:
            return f"{gb.group(1)}GB"
            
        # Fallback — just extract any number
        num = re.search(r"(\d+)", clean_raw)
        if num:
            return f"{num.group(1)}GB"
        return raw

    @staticmethod
    def normalise_storage(raw: str) -> tuple[str, str]:
        """'512GB NVMe SSD' → ('512GB', 'SSD'), '1TB HDD' → ('1024GB', 'HDD')"""
        raw = raw.strip().upper()

        # Detect type
        stype = "SSD"  # default
        if "HDD" in raw or "HARD DISK" in raw.upper():
            stype = "HDD"
        elif "NVME" in raw:
            stype = "NVMe"
        elif "SSD" in raw:
            stype = "SSD"
        elif "EMMC" in raw:
            stype = "eMMC"

        # Detect size
        tb = re.search(r"(\d+)\s*TB", raw, re.I)
        if tb:
            return f"{int(tb.group(1)) * 1024}GB", stype
            
        # Same check for storage specific keywords first
        specific_storage = re.search(r"(\d+)\s*GB\s*(?:SSD|HDD|NVMe|eMMC|Storage)", raw, re.I)
        if specific_storage:
            return f"{specific_storage.group(1)}GB", stype

        # Handle multiple GBs, Storage is rarely 8, 16, 32 (too small). 
        # Usually 128, 240, 256, 480, 512, 1000, 1024.
        all_gb = re.findall(r"(\d+)\s*GB", raw, re.I)
        for val in all_gb:
            num = int(val)
            if num >= 120 and num not in [128, 256, 512] and num != int(val): # Safety for weird parsed values
                pass # Continue searching
            elif num >= 120:
                 return f"{num}GB", stype

        # Fallback to the largest GB value found to avoid grabbing "16" (RAM) from "16GB RAM 512GB SSD"
        if all_gb:
            largest = max(int(v) for v in all_gb)
            return f"{largest}GB", stype

        gb = re.search(r"(\d+)\s*GB", raw, re.I)
        if gb:
            return f"{gb.group(1)}GB", stype

        return raw, stype

    @staticmethod
    def normalise_processor_gen(raw: str) -> Optional[int]:
        """
        'Intel Core i5-1235U' → 12
        'Intel Core i7-13700H' → 13
        'AMD Ryzen 5 5600H'   → None (AMD doesn't use this convention)
        """
        if not raw:
            return None

        # Method 1: First 2 digits of 4-5 digit model number after i3/i5/i7/i9
        model_match = re.search(r"i[3579][-\s](\d{4,5})", raw, re.I)
        if model_match:
            model_str = str(model_match.group(1))
            return int(model_str[:2])

        # Method 2: Explicit "Nth Gen" text
        gen_match = re.search(r"(\d+)(?:st|nd|rd|th)\s*gen", raw, re.I)
        if gen_match:
            return int(gen_match.group(1))

        return None

    @staticmethod
    def normalise_processor_model(raw: str) -> Optional[str]:
        """Extract processor model like 'i5', 'i7', 'Ryzen 5'"""
        if not raw:
            return None
        m = re.search(r"\b(i[3579])\b", raw, re.I)
        if m:
            return m.group(1).lower()
        m = re.search(r"(ryzen\s*\d)", raw, re.I)
        if m:
            return m.group(1).title()
        return None

    @staticmethod
    def normalise_price(raw: str) -> float:
        """'₹1,23,456' → 123456.0"""
        cleaned = re.sub(r"[₹$,\s]", "", raw)
        m = re.search(r"[\d.]+", cleaned)
        try:
            return float(m.group(0)) if m else 0.0
        except ValueError:
            return 0.0

    @staticmethod
    def normalise_screen_size(raw: str) -> Optional[float]:
        """'27" Full HD IPS' → 27.0, '15.6 inches' → 15.6"""
        m = re.search(r"(\d+\.?\d*)\s*(?:inch|inches|\"|in\b|''|cm)?", raw, re.I)
        if m:
            val = float(m.group(1))
            # If value seems to be in cm, convert
            if "cm" in raw.lower() and val > 40:
                val = float(f"{val / 2.54:.1f}") # Ensure float with one decimal place
            return val
        return None

    @staticmethod
    def normalise_resolution(raw: str) -> str:
        """'3840 x 2160' → '4K', '1920x1080' → '1080p'"""
        raw_lower = raw.lower()

        # Direct keyword mapping
        if any(x in raw_lower for x in ["4k", "uhd", "ultra hd"]):
            return "4K"
        if any(x in raw_lower for x in ["2k", "qhd", "quad hd"]):
            return "2K"
        if any(x in raw_lower for x in ["fhd", "full hd", "1080"]):
            return "1080p"
        if "720" in raw_lower:
            return "HD"

        # Pixel dimensions
        m = re.search(r"(\d{3,4})\s*[x×X]\s*(\d{3,4})", raw)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if w >= 3840 or h >= 2160:
                return "4K"
            if w >= 2560 or h >= 1440:
                return "2K"
            if w >= 1920 or h >= 1080:
                return "1080p"
            return "HD"

        return raw.strip()

    @staticmethod
    def normalise_panel_type(raw: str) -> str:
        """'In-Plane Switching (IPS)' → 'IPS'"""
        raw_upper = raw.upper()
        if "IPS" in raw_upper or "IN-PLANE" in raw_upper:
            return "IPS"
        if "VA" in raw_upper:
            return "VA"
        if "TN" in raw_upper:
            return "TN"
        if "OLED" in raw_upper:
            return "OLED"
        if "AMOLED" in raw_upper:
            return "AMOLED"
        return raw.strip().upper()

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _find_spec(specs: dict, keys: list[str]) -> Optional[str]:
        """Find a spec value by checking multiple possible key names (case-insensitive)."""
        # Pass 1: exact matches to avoid partial collision (e.g. 'RAM Size' matching before 'Graphics RAM')
        for target_key in keys:
            t_low = target_key.lower()
            for spec_key, spec_val in specs.items():
                if spec_key.startswith("_"):
                    continue
                s_low = spec_key.lower()
                if t_low == s_low or (t_low in s_low and "graphics" not in s_low and "technology" not in s_low):
                    return str(spec_val)
                    
        # Pass 2: partial matches
        for target_key in keys:
            for spec_key, spec_val in specs.items():
                if spec_key.startswith("_"):
                    continue
                if target_key.lower() in spec_key.lower():
                    return str(spec_val)
        return None
