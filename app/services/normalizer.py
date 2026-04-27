"""
matcher.py — Motor de matching y clasificación de incidencias para el Módulo A.

Compara líneas del pedido de Aquacenter contra líneas de la confirmación del proveedor.
Utiliza `cod_proveedor` como clave primaria de matching.

Catálogo de incidencias:
  MA-01  Línea en pedido sin match en confirmación
  MA-02  Línea en confirmación sin match en pedido
  MA-03  Precio distinto (pedido vs confirmación)
  MA-04  Cantidad distinta
  MA-05  Descuento distinto
  MA-06  Código proveedor ausente en confirmación
  MA-07  EAN inconsistente entre documentos
  MA-08  Coincidencia ambigua (múltiples candidatos)
  MA-09  Documento no interpretable
  MA-10  Proveedor no identificado en cabecera
  MA-11  Línea duplicada en pedido
  MA-12  Línea duplicada en confirmación
"""

from dataclasses import dataclass, field
from typing import Optional

# Tolerancia para comparación de precios (diferencias de redondeo centavo)
PRICE_TOLERANCE = 0.02
DTO_TOLERANCE = 0.01
QTY_TOLERANCE = 0.001


@dataclass
class LineaResultado:
    cod_proveedor: str
    descripcion_pedido: str
    descripcion_confirmacion: Optional[str]
    cantidad_pedido: float
    cantidad_confirmacion: Optional[float]
    precio_pedido: float           # precio_vta del pedido
    precio_confirmacion: Optional[float]
    dto_pedido: float
    dto_confirmacion: Optional[float]
    estado: str                    # "OK" | "incidencia"
    motivos: list = field(default_factory=list)   # ["MA-03", "MA-04", ...]


@dataclass
class ResultadoComparacion:
    pedido_ref: str
    proveedor_nombre: str
    total_lineas_pedido: int
    total_lineas_confirmacion: int
    lineas_ok: list = field(default_factory=list)
    lineas_incidencia: list = field(default_factory=list)

    @property
    def estado_global(self) -> str:
        return "OK" if not self.lineas_incidencia else "CON INCIDENCIAS"

    @property
    def total_lineas(self):
        return len(self.lineas_ok) + len(self.lineas_incidencia)


def comparar(doc_pedido: dict, doc_confirmacion: dict) -> ResultadoComparacion:
    """
    Compara las líneas del pedido contra las de la confirmación.

    Args:
        doc_pedido: salida de parse_pdf() para el pedido
        doc_confirmacion: salida de parse_pdf() para la confirmación

    Returns:
        ResultadoComparacion con líneas clasificadas
    """
    cab_ped = doc_pedido.get("cabecera", {})
    cab_conf = doc_confirmacion.get("cabecera", {})
    lineas_ped = doc_pedido.get("lineas", [])
    lineas_conf = doc_confirmacion.get("lineas", [])

    resultado = ResultadoComparacion(
        pedido_ref=cab_ped.get("pedido", "?"),
        proveedor_nombre=cab_ped.get("proveedor_nombre", cab_conf.get("proveedor_nombre", "?")),
        total_lineas_pedido=len(lineas_ped),
        total_lineas_confirmacion=len(lineas_conf),
    )

    # Detectar duplicados en pedido
    dup_ped = _find_duplicates(lineas_ped, "cod_proveedor")
    dup_conf = _find_duplicates(lineas_conf, "cod_proveedor")

    # Índice de confirmación por cod_proveedor
    conf_idx: dict[str, list] = {}
    for lc in lineas_conf:
        cod = (lc.get("cod_proveedor") or "").strip().upper()
        if not cod:
            continue
        conf_idx.setdefault(cod, []).append(lc)

    # Conjunto de códigos de confirmación ya emparejados
    matched_conf_codes: set[str] = set()

    for lp in lineas_ped:
        cod = (lp.get("cod_proveedor") or "").strip().upper()
        desc_ped = lp.get("descripcion", "")
        qty_ped = lp.get("cantidad", 0.0)
        precio_ped = lp.get("precio_vta", 0.0)
        dto_ped = lp.get("dto", 0.0)

        motivos = []

        # MA-11: duplicado en pedido
        if cod in dup_ped:
            motivos.append("MA-11")

        # MA-06: código ausente
        if not cod:
            motivos.append("MA-06")
            resultado.lineas_incidencia.append(LineaResultado(
                cod_proveedor="(vacío)",
                descripcion_pedido=desc_ped,
                descripcion_confirmacion=None,
                cantidad_pedido=qty_ped,
                cantidad_confirmacion=None,
                precio_pedido=precio_ped,
                precio_confirmacion=None,
                dto_pedido=dto_ped,
                dto_confirmacion=None,
                estado="incidencia",
                motivos=motivos,
            ))
            continue

        candidatos = conf_idx.get(cod, [])

        # MA-01: línea en pedido sin match en confirmación
        if not candidatos:
            motivos.append("MA-01")
            resultado.lineas_incidencia.append(LineaResultado(
                cod_proveedor=cod,
                descripcion_pedido=desc_ped,
                descripcion_confirmacion=None,
                cantidad_pedido=qty_ped,
                cantidad_confirmacion=None,
                precio_pedido=precio_ped,
                precio_confirmacion=None,
                dto_pedido=dto_ped,
                dto_confirmacion=None,
                estado="incidencia",
                motivos=motivos,
            ))
            continue

        # MA-08: coincidencia ambigua
        if len(candidatos) > 1:
            motivos.append("MA-08")

        # Tomar el primer candidato (o el único)
        lc = candidatos[0]
        matched_conf_codes.add(cod)

        qty_conf = lc.get("cantidad")
        precio_conf = lc.get("precio_unitario", lc.get("precio_vta", None))
        dto_conf = lc.get("dto", 0.0)
        desc_conf = lc.get("descripcion", "")

        # MA-12: duplicado en confirmación
        if cod in dup_conf:
            motivos.append("MA-12")

        # MA-03: precio distinto
        if precio_conf is not None and not _approx_equal(precio_ped, precio_conf, PRICE_TOLERANCE):
            motivos.append("MA-03")

        # MA-04: cantidad distinta
        if qty_conf is not None and not _approx_equal(qty_ped, qty_conf, QTY_TOLERANCE):
            motivos.append("MA-04")

        # MA-05: descuento distinto
        if dto_conf is not None and not _approx_equal(dto_ped, dto_conf, DTO_TOLERANCE):
            motivos.append("MA-05")

        linea = LineaResultado(
            cod_proveedor=cod,
            descripcion_pedido=desc_ped,
            descripcion_confirmacion=desc_conf,
            cantidad_pedido=qty_ped,
            cantidad_confirmacion=qty_conf,
            precio_pedido=precio_ped,
            precio_confirmacion=precio_conf,
            dto_pedido=dto_ped,
            dto_confirmacion=dto_conf,
            estado="incidencia" if motivos else "OK",
            motivos=motivos,
        )

        if motivos:
            resultado.lineas_incidencia.append(linea)
        else:
            resultado.lineas_ok.append(linea)

    # MA-02: líneas en confirmación sin match en pedido
    ped_codes = {(lp.get("cod_proveedor") or "").strip().upper() for lp in lineas_ped}
    for lc in lineas_conf:
        cod = (lc.get("cod_proveedor") or "").strip().upper()
        if not cod or cod in ped_codes:
            continue
        resultado.lineas_incidencia.append(LineaResultado(
            cod_proveedor=cod,
            descripcion_pedido="",
            descripcion_confirmacion=lc.get("descripcion", ""),
            cantidad_pedido=0.0,
            cantidad_confirmacion=lc.get("cantidad"),
            precio_pedido=0.0,
            precio_confirmacion=lc.get("precio_unitario"),
            dto_pedido=0.0,
            dto_confirmacion=lc.get("dto"),
            estado="incidencia",
            motivos=["MA-02"],
        ))

    return resultado


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _approx_equal(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def _find_duplicates(lineas: list, key: str) -> set:
    seen = set()
    duplicates = set()
    for l in lineas:
        v = (l.get(key) or "").strip().upper()
        if v in seen:
            duplicates.add(v)
        seen.add(v)
    return duplicates


# Descripciones legibles para el Excel
MOTIVO_DESCRIPCION = {
    "MA-01": "Línea en pedido sin match en confirmación",
    "MA-02": "Línea en confirmación sin match en pedido",
    "MA-03": "Precio distinto (pedido vs confirmación)",
    "MA-04": "Cantidad distinta",
    "MA-05": "Descuento distinto",
    "MA-06": "Código proveedor ausente en confirmación",
    "MA-07": "EAN inconsistente entre documentos",
    "MA-08": "Coincidencia ambigua (múltiples candidatos)",
    "MA-09": "Documento no interpretable",
    "MA-10": "Proveedor no identificado en cabecera",
    "MA-11": "Línea duplicada en pedido",
    "MA-12": "Línea duplicada en confirmación",
}
