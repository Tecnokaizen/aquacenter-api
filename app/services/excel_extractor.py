from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def extract_table_document(path: str) -> dict[str, Any]:
    """
    Extracción básica para CSV/XLSX en MVP.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(p)
        source_type = "csv"
    elif suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(p)
        source_type = "xlsx"
    else:
        raise ValueError(f"Formato tabular no soportado: {suffix}")

    columns = [str(c) for c in df.columns]
    lines = []
    for _, row in df.head(2000).iterrows():
        lines.append({k: _safe_val(v) for k, v in row.to_dict().items()})

    return {
        "tipo": source_type,
        "cabecera": {
            "file_name": p.name,
            "columns": columns,
            "rows": int(len(df)),
        },
        "lineas": lines,
    }


def _safe_val(v: Any) -> Any:
    if pd.isna(v):
        return None
    return v

