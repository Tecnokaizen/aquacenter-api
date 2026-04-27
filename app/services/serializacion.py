"""
serializacion.py — Contrato JSON para integraciones (Make, n8n, frontend).

Convierte resultados del matcher a estructuras serializables con json.dumps.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .normalizer import LineaResultado, ResultadoComparacion


def linea_resultado_a_dict(l: LineaResultado) -> dict[str, Any]:
    return asdict(l)


def resultado_comparacion_a_dict(r: ResultadoComparacion) -> dict[str, Any]:
    return {
        "pedido_ref": r.pedido_ref,
        "proveedor_nombre": r.proveedor_nombre,
        "total_lineas_pedido": r.total_lineas_pedido,
        "total_lineas_confirmacion": r.total_lineas_confirmacion,
        "estado_global": r.estado_global,
        "total_lineas": r.total_lineas,
        "lineas_ok": [linea_resultado_a_dict(x) for x in r.lineas_ok],
        "lineas_incidencia": [linea_resultado_a_dict(x) for x in r.lineas_incidencia],
    }


def lote_a_dict(
    batch_id: str,
    started_at: str,
    finished_at: str,
    pairs: list[dict[str, Any]],
    totals: dict[str, Any],
    module: str = "confirmacion_pedidos",
    rule_set_version: str | None = None,
) -> dict[str, Any]:
    """
    pairs: cada elemento típico:
      pedido_file, confirmacion_file, seconds, error (str|None), resultado (dict|None)
    """
    return {
        "batch_id": batch_id,
        "module": module,
        "rule_set_version": rule_set_version,
        "started_at": started_at,
        "finished_at": finished_at,
        "pairs": pairs,
        "totals": totals,
    }
