"""
modules_registry.py — Registro de módulos y reglas versionadas.

Permite crecer a Módulos B/C sin romper el contrato del Módulo A.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .normalizer import ResultadoComparacion, comparar
from .pdf_extractor import parse_pdf, proveedores_locales_confirmacion

ParseFn = Callable[[str], dict[str, Any]]
CompareFn = Callable[[dict[str, Any], dict[str, Any]], ResultadoComparacion]

DEFAULT_MODULE = "confirmacion_pedidos"
DEFAULT_RULE_SET_VERSION = "module_a.v1"


@dataclass(frozen=True)
class ModuleDefinition:
    module: str
    rule_set_version: str
    parser_name: str
    matcher_name: str
    parse_document_fn: ParseFn
    compare_fn: CompareFn
    proveedores_locales: list[str]


MODULE_DEFINITIONS: dict[str, ModuleDefinition] = {
    DEFAULT_MODULE: ModuleDefinition(
        module=DEFAULT_MODULE,
        rule_set_version=DEFAULT_RULE_SET_VERSION,
        parser_name="parse_pdf.v1",
        matcher_name="matcher.comparar.v1",
        parse_document_fn=parse_pdf,
        compare_fn=comparar,
        proveedores_locales=proveedores_locales_confirmacion(),
    ),
}


def get_module_definition(module: str = DEFAULT_MODULE) -> ModuleDefinition:
    try:
        return MODULE_DEFINITIONS[module]
    except KeyError as exc:
        known = ", ".join(sorted(MODULE_DEFINITIONS.keys()))
        raise ValueError(
            f"Módulo no registrado: {module!r}. Disponibles: {known or '(ninguno)'}"
        ) from exc


def list_module_definitions() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for md in MODULE_DEFINITIONS.values():
        rows.append(
            {
                "module": md.module,
                "rule_set_version": md.rule_set_version,
                "parser_name": md.parser_name,
                "matcher_name": md.matcher_name,
                "proveedores_locales": md.proveedores_locales,
            }
        )
    return sorted(rows, key=lambda x: x["module"])
