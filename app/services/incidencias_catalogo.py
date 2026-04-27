"""
incidencias_catalogo.py — Catálogo versionado de incidencias.
"""

from __future__ import annotations

from .normalizer import MOTIVO_DESCRIPCION

INCIDENCIAS_CATALOGO_VERSION = "module_a.catalog.v1"
INCIDENCIAS_CATALOGO = dict(sorted(MOTIVO_DESCRIPCION.items()))
