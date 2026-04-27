from __future__ import annotations

import re


def clean_supplier_name(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = " ".join(name.split()).strip(" ,;-")

    legal_match = re.search(r"\bS\.?\s*(?:A|L)\.?\s*(?:U\.?)?\b", cleaned, flags=re.IGNORECASE)
    if legal_match:
        cleaned = cleaned[: legal_match.end()].strip(" ,;-")
    else:
        upper = cleaned.upper()
        for marker in (" AV/", " C/", " CALLE ", " POL.", " POLIGONO ", " CAMINO "):
            idx = upper.find(marker)
            if idx > 0:
                cleaned = cleaned[:idx].strip(" ,;-")
                break
    return cleaned

