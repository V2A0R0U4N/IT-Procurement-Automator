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

# ── Valid sizes for sanity checking ─────────────────────────────────────────
VALID_RAM_SIZES = {2, 3, 4, 6, 8, 10, 12, 16, 24, 32, 48, 64, 96, 128}
VALID_STORAGE_SIZES = {16, 32, 64, 120, 128, 240, 256, 480, 512, 960, 1000, 1024, 2000, 2048, 4000, 4096}

KNOWN_BRANDS = {
    "asus", "acer", "dell", "hp", "lenovo", "apple", "samsung", "lg",
    "msi", "microsoft", "razer", "gigabyte", "huawei", "xiaomi", "redmi",
    "realme", "infinix", "honor", "toshiba", "fujitsu", "panasonic",
    "vaio", "nokia", "google", "benq", "viewsonic", "aoc",
}


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
        title = product_dict.get("title", "")

        # ── RAM ──────────────────────────────────────────────────────────────
        ram_raw = self._find_spec(specs, ["ram", "memory", "system memory", "ram memory"])
        if ram_raw:
            specs["_ram_normalised"] = self.normalise_ram(ram_raw)
        elif title:
            # Title fallback
            ram_from_title = self._extract_ram_from_text(title)
            if ram_from_title:
                specs["_ram_normalised"] = ram_from_title

        # ── Storage ──────────────────────────────────────────────────────────
        storage_raw = self._find_spec(specs, [
            "storage capacity", "ssd capacity", "hdd capacity", "internal storage",
            "storage", "hard drive", "hard disk", "flash memory", "ssd", "hdd",
            "hard drive size",
        ])
        if storage_raw:
            size, stype = self.normalise_storage(storage_raw)
            if size:
                specs["_storage_size_normalised"] = size
                specs["_storage_type_normalised"] = stype
        if "_storage_size_normalised" not in specs and title:
            # Title fallback
            storage_from_title = self._extract_storage_from_text(title)
            if storage_from_title:
                specs["_storage_size_normalised"] = storage_from_title[0]
                specs["_storage_type_normalised"] = storage_from_title[1]

        # ── Processor / Generation ───────────────────────────────────────────
        proc_raw = self._find_spec(specs, [
            "processor", "cpu", "processor name", "processor brand",
            "processor type", "model number", "processor model number"
        ])
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
        if "_screen_size_normalised" not in specs and title:
            screen_from_title = self._extract_screen_from_text(title)
            if screen_from_title:
                specs["_screen_size_normalised"] = str(screen_from_title)

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
            brand_from_title = self._extract_brand_from_title(title)
            if brand_from_title:
                specs["_brand_normalised"] = brand_from_title

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
        clean_raw = re.sub(r"(?:LP)?(?:G)?DDR\d[X]?", "", raw, flags=re.I)
        # Remove storage keywords to prevent cross-contamination
        clean_raw = re.sub(r"\b(?:SSD|HDD|NVMe|eMMC|SATA|M\.2|PCIe)\b", "", clean_raw, flags=re.I)

        # Check specifically for GB RAM / GB Memory
        specific_ram = re.search(r"(\d+)\s*GB\s*(?:RAM|Memory|System Memory)", clean_raw, re.I)
        if specific_ram:
            val = int(specific_ram.group(1))
            if val in VALID_RAM_SIZES:
                return f"{val}GB"

        # Convert MB to GB
        mb = re.search(r"(\d+)\s*MB", clean_raw, re.I)
        if mb:
            gb_val = int(mb.group(1)) // 1024
            if gb_val in VALID_RAM_SIZES:
                return f"{gb_val}GB"

        # Check all GB values and find valid RAM sizes
        all_gb = re.findall(r"(\d+)\s*GB", clean_raw, re.I)
        for val in all_gb:
            num = int(val)
            if num in VALID_RAM_SIZES:
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
        """'512GB NVMe SSD' → ('512GB', 'SSD'), '1TB HDD' → ('1TB', 'HDD')"""
        raw_upper = raw.strip().upper()
        # Strip RAM keywords to prevent cross-contamination
        cleaned = re.sub(r"\b(?:LP)?(?:G)?DDR\d[X]?\b", "", raw_upper, flags=re.I)
        cleaned = re.sub(r"\b(?:RAM|SDRAM|MEMORY|SYSTEM MEMORY)\b", "", cleaned, flags=re.I)

        # Detect type
        stype = "SSD"  # default
        if "HDD" in raw_upper or "HARD DISK" in raw_upper:
            stype = "HDD"
        elif "NVME" in raw_upper or "PCIE" in raw_upper or "M.2" in raw_upper:
            stype = "NVMe"
        elif "SSD" in raw_upper:
            stype = "SSD"
        elif "EMMC" in raw_upper:
            stype = "eMMC"

        # Detect size — TB first
        tb = re.search(r"(\d+)\s*TB", cleaned, re.I)
        if tb:
            return f"{tb.group(1)}TB", stype

        # Check for storage-specific keywords first
        specific_storage = re.search(r"(\d+)\s*GB\s*(?:SSD|HDD|NVMe|eMMC|Storage|SATA)", cleaned, re.I)
        if specific_storage:
            val = int(specific_storage.group(1))
            if val in VALID_STORAGE_SIZES or val >= 120:
                return f"{val}GB", stype

        # Handle multiple GBs — pick the one that's a valid storage size
        all_gb = re.findall(r"(\d+)\s*GB", cleaned, re.I)
        for val_str in all_gb:
            num = int(val_str)
            if num in VALID_STORAGE_SIZES and num >= 120:
                return f"{num}GB", stype

        # Fallback to the largest GB value found (storage is almost always bigger than RAM)
        if all_gb:
            largest = max(int(v) for v in all_gb)
            if largest >= 64:  # anything below 64GB is likely RAM
                return f"{largest}GB", stype

        gb = re.search(r"(\d+)\s*GB", cleaned, re.I)
        if gb:
            return f"{gb.group(1)}GB", stype

        return None, stype

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
            gen = int(model_str[:2])
            if 8 <= gen <= 20:
                return gen

        # Method 2: Explicit "Nth Gen" text
        gen_match = re.search(r"(\d+)(?:st|nd|rd|th)\s*gen", raw, re.I)
        if gen_match:
            gen = int(gen_match.group(1))
            if 1 <= gen <= 20:
                return gen

        # Method 3: "Gen N" variant
        gen_n_match = re.search(r"gen\s*(\d+)", raw, re.I)
        if gen_n_match:
            gen = int(gen_n_match.group(1))
            if 8 <= gen <= 20:
                return gen

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
        # Apple Silicon
        m = re.search(r"\b(M[1-4])\b", raw)
        if m:
            return m.group(1).upper()
        return None

    @staticmethod
    def normalise_price(raw: str) -> float:
        """'₹1,23,456' → 123456.0"""
        cleaned = re.sub(r"[₹$£€,\s]", "", raw)
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
                val = float(f"{val / 2.54:.1f}")  # Ensure float with one decimal place
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
        # Check in order of specificity
        if "AMOLED" in raw_upper:
            return "AMOLED"
        if "OLED" in raw_upper:
            return "OLED"
        if "IPS" in raw_upper or "IN-PLANE" in raw_upper:
            return "IPS"
        if "VA" in raw_upper:
            return "VA"
        if "TN" in raw_upper:
            return "TN"
        if "LED" in raw_upper:
            return "LED"
        return raw.strip().upper()

    # ─────────────────────────────────────────────────────────────────────────
    # Title fallback extractors
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_ram_from_text(text: str) -> Optional[str]:
        """Extract RAM from title like '16GB RAM' or '16GB DDR5'."""
        # Pattern: NGB followed by RAM/DDR keyword
        m = re.search(r"(\d+)\s*GB\s*(?:RAM|DDR\d|LPDDR\d|Memory)", text, re.I)
        if m:
            val = int(m.group(1))
            if val in VALID_RAM_SIZES:
                return f"{val}GB"
        # Pattern: NGB where N is a common RAM size, NOT followed by SSD/HDD
        m = re.search(r"\b(\d+)\s*GB\b(?!\s*(?:SSD|HDD|NVMe|Storage|eMMC))", text, re.I)
        if m:
            val = int(m.group(1))
            if val in VALID_RAM_SIZES and val <= 128:
                return f"{val}GB"
        return None

    @staticmethod
    def _extract_storage_from_text(text: str) -> Optional[tuple[str, str]]:
        """Extract storage from title like '512GB SSD' or '1TB NVMe'."""
        # TB pattern
        m = re.search(r"(\d+)\s*TB\s*(?:SSD|HDD|NVMe|Storage|PCIe|SATA)?", text, re.I)
        if m:
            stype = "SSD"
            if "HDD" in text.upper():
                stype = "HDD"
            elif "NVMe" in text.upper() or "PCIe" in text.upper():
                stype = "NVMe"
            return f"{m.group(1)}TB", stype

        # GB pattern followed by storage keyword
        m = re.search(r"(\d+)\s*GB\s*(?:SSD|HDD|NVMe|Storage|eMMC|PCIe|SATA)", text, re.I)
        if m:
            val = int(m.group(1))
            stype = "SSD"
            if "HDD" in text.upper():
                stype = "HDD"
            elif "NVMe" in text.upper() or "PCIe" in text.upper():
                stype = "NVMe"
            return f"{val}GB", stype

        return None

    @staticmethod
    def _extract_screen_from_text(text: str) -> Optional[float]:
        """Extract screen size from title like '15.6 inch' or '15.6\"'."""
        m = re.search(r"(\d+\.?\d*)\s*(?:inch|inches|\"|\u201D)", text, re.I)
        if m:
            val = float(m.group(1))
            if 10 <= val <= 40:  # reasonable screen sizes
                return val
        # Also handle cm
        m = re.search(r"(\d+\.?\d*)\s*cm", text, re.I)
        if m:
            val = float(m.group(1))
            if val > 25:
                return round(val / 2.54, 1)
        return None

    @staticmethod
    def _extract_brand_from_title(title: str) -> Optional[str]:
        """Extract brand name from the first 2 words of the title."""
        words = title.strip().split()[:3]
        for word in words:
            clean = re.sub(r"[^a-zA-Z]", "", word).lower()
            if clean in KNOWN_BRANDS:
                return clean.title()
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _find_spec(specs: dict, keys: list[str]) -> Optional[str]:
        """Find a spec value by checking multiple possible key names (case-insensitive)."""
        # Pass 1: exact matches to capacity/size keys containing digits
        for target_key in keys:
            t_low = target_key.lower()
            for spec_key, spec_val in specs.items():
                if spec_key.startswith("_"):
                    continue
                s_low = spec_key.lower()
                val_str = str(spec_val)
                # Ensure the value actually contains digits if we are looking for numeric specs
                has_digit = any(char.isdigit() for char in val_str)
                # Exclude graphics/video keys to prevent false matches
                if "graphics" in s_low or "video" in s_low or "technology" in s_low:
                    continue
                if t_low == s_low or t_low in s_low:
                    if has_digit or target_key in ["brand", "panel type", "manufacturer", "brand name"]:
                        return val_str

        # Pass 2: partial matches where value contains digits
        for target_key in keys:
            for spec_key, spec_val in specs.items():
                if spec_key.startswith("_"):
                    continue
                val_str = str(spec_val)
                s_low = spec_key.lower()
                has_digit = any(char.isdigit() for char in val_str)
                if "graphics" in s_low or "video" in s_low:
                    continue
                if target_key.lower() in s_low and (has_digit or target_key in ["brand", "panel type", "manufacturer"]):
                    return val_str

        # Pass 3: Fallback ignoring digits (e.g. for "Yes" or boolean flags)
        for target_key in keys:
            t_low = target_key.lower()
            for spec_key, spec_val in specs.items():
                if spec_key.startswith("_"):
                    continue
                s_low = spec_key.lower()
                if "graphics" in s_low or "video" in s_low:
                    continue
                if t_low == s_low or t_low in s_low:
                    return str(spec_val)

        return None
