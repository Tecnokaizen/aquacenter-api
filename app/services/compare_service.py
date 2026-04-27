"""
compare_service.py — Servicio reusable de comparación (CLI + HTTP API).
"""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .excel_reporter import exportar_excel
from .normalizer import ResultadoComparacion
from .modules_registry import (
    DEFAULT_MODULE,
    DEFAULT_RULE_SET_VERSION,
    get_module_definition,
)
from .pdf_extractor import OpenAINotConfiguredError, detect_doc_type
from .serializacion import lote_a_dict, resultado_comparacion_a_dict


class DocumentTypeError(ValueError):
    """El par de archivos no cumple tipo pedido + confirmación."""


@dataclass
class ComparisonRun:
    payload: dict[str, Any]
    resultado: ResultadoComparacion
    pedido_path: str
    confirmacion_path: str
    module: str
    rule_set_version: str
    parse_seconds: float
    match_seconds: float
    total_seconds: float
    excel_path: str | None = None
    excel_bytes: bytes | None = None
    warnings: list[str] = field(default_factory=list)


def compare_pair_paths(
    pedido_path: str,
    confirmacion_path: str,
    *,
    module: str = DEFAULT_MODULE,
    rule_set_version: str | None = None,
    batch_id: str | None = None,
    include_excel: bool = False,
    output_xlsx: str | None = None,
    strict_types: bool = False,
) -> ComparisonRun:
    """
    Compara un par de documentos y devuelve payload listo para JSON API/CLI.
    """
    pedido_file = Path(pedido_path).resolve()
    confirmacion_file = Path(confirmacion_path).resolve()
    _assert_input_files(pedido_file, confirmacion_file)

    definition = get_module_definition(module)
    if not rule_set_version:
        rule_set_version = definition.rule_set_version or DEFAULT_RULE_SET_VERSION
    batch_id = batch_id or f"single_{pedido_file.stem}_{int(time.time())}"

    warnings: list[str] = []

    tipo_ped, tipo_conf = detect_doc_type(str(pedido_file)), detect_doc_type(str(confirmacion_file))
    if tipo_ped == "confirmacion" and tipo_conf == "pedido":
        pedido_file, confirmacion_file = confirmacion_file, pedido_file
        warnings.append("args_swapped_autodetected")
        tipo_ped, tipo_conf = tipo_conf, tipo_ped

    if strict_types and not (tipo_ped == "pedido" and tipo_conf == "confirmacion"):
        raise DocumentTypeError(
            "Se esperaba un par pedido+confirmación. "
            f"Detectado pedido={tipo_ped}, confirmacion={tipo_conf}."
        )

    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.time()
    try:
        doc_pedido = definition.parse_document_fn(str(pedido_file))
        doc_confirmacion = definition.parse_document_fn(str(confirmacion_file))
    except OpenAINotConfiguredError:
        raise
    parse_seconds = time.time() - t0

    t1 = time.time()
    resultado = definition.compare_fn(doc_pedido, doc_confirmacion)
    match_seconds = time.time() - t1
    total_seconds = time.time() - t0

    excel_path, excel_bytes = _build_excel(
        resultado=resultado,
        include_excel=include_excel,
        output_xlsx=output_xlsx,
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    pair_result = {
        "pedido_file": str(pedido_file),
        "confirmacion_file": str(confirmacion_file),
        "seconds": round(total_seconds, 3),
        "parse_seconds": round(parse_seconds, 3),
        "match_seconds": round(match_seconds, 3),
        "error": None,
        "resultado": resultado_comparacion_a_dict(resultado),
    }
    totals = {
        "pairs_total": 1,
        "pairs_error": 0,
        "pairs_with_incidents": 1 if resultado.estado_global == "CON INCIDENCIAS" else 0,
        "lines_ok": len(resultado.lineas_ok),
        "lines_incidencia": len(resultado.lineas_incidencia),
        "seconds": round(total_seconds, 3),
    }
    payload = lote_a_dict(
        batch_id=batch_id,
        started_at=started_at,
        finished_at=finished_at,
        pairs=[pair_result],
        totals=totals,
        module=module,
        rule_set_version=rule_set_version,
    )
    payload["warnings"] = warnings
    payload["processing"] = {
        "proveedor_detectado": resultado.proveedor_nombre,
        "parse_seconds": round(parse_seconds, 3),
        "match_seconds": round(match_seconds, 3),
        "total_seconds": round(total_seconds, 3),
    }

    return ComparisonRun(
        payload=payload,
        resultado=resultado,
        pedido_path=str(pedido_file),
        confirmacion_path=str(confirmacion_file),
        module=module,
        rule_set_version=rule_set_version,
        parse_seconds=parse_seconds,
        match_seconds=match_seconds,
        total_seconds=total_seconds,
        excel_path=excel_path,
        excel_bytes=excel_bytes,
        warnings=warnings,
    )


def _assert_input_files(pedido_file: Path, confirmacion_file: Path) -> None:
    if not pedido_file.exists():
        raise FileNotFoundError(f"Archivo pedido no encontrado: {pedido_file}")
    if not confirmacion_file.exists():
        raise FileNotFoundError(f"Archivo confirmación no encontrado: {confirmacion_file}")
    if pedido_file.suffix.lower() != ".pdf" or confirmacion_file.suffix.lower() != ".pdf":
        raise ValueError("Ambos archivos deben ser PDF (.pdf).")


def _build_excel(
    *,
    resultado: ResultadoComparacion,
    include_excel: bool,
    output_xlsx: str | None,
) -> tuple[str | None, bytes | None]:
    excel_bytes: bytes | None = None
    excel_path: Path | None = Path(output_xlsx).resolve() if output_xlsx else None

    temp_path: Path | None = None
    if include_excel and excel_path is None:
        fd, p = tempfile.mkstemp(prefix="aquacenter_", suffix=".xlsx")
        os.close(fd)
        temp_path = Path(p)
        excel_path = temp_path

    if excel_path is not None:
        excel_path.parent.mkdir(parents=True, exist_ok=True)
        exportar_excel(resultado, str(excel_path))
        if include_excel:
            excel_bytes = excel_path.read_bytes()

    if temp_path is not None:
        temp_path.unlink(missing_ok=True)
        return None, excel_bytes
    return str(excel_path) if excel_path else None, excel_bytes
