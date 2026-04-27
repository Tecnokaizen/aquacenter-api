"""
parse_pdf.py — Extractor de líneas para pedidos de Aquacenter y confirmaciones de proveedor.

Estrategia:
  - PEDIDO (emitido por Aquacenter): formato fijo Sage → pdfplumber por coordenadas x
  - CONFIRMACIÓN (emitida por proveedor): parsers locales por proveedor → fallback API OpenAI (Responses + PDF)

Columnas del pedido Sage (posiciones x aproximadas, consistentes en los 3 proveedores):
  x < 90        → Su Código
  90 ≤ x < 280  → Descripción
  280 ≤ x < 330 → Unidades
  330 ≤ x < 490 → Precio Vta / % Dto (según nº de valores numéricos en la fila)
  490 ≤ x < 540 → Precio Neto
  x ≥ 540       → Importe EUR
"""

import os
import re
import base64
import json
from pathlib import Path

import pdfplumber
from openai import OpenAI

OPENAI_MODEL_DEFAULT = "gpt-5-codex"


class OpenAINotConfiguredError(RuntimeError):
    """Falta OPENAI_API_KEY y se requiere el fallback por API para esta confirmación."""


def _model_openai() -> str:
    """Modelo con visión; ver https://platform.openai.com/docs/models"""
    return os.environ.get("OPENAI_MODEL", OPENAI_MODEL_DEFAULT)

# Códigos de referencia en las cabeceras para detectar tipo de documento
AQUACENTER_MARKERS = ["JU JU JU AQUA CENTER", "JUJUJU AQUACENTER", "JU-JU-JU AQUA CENTER"]

# Líneas a ignorar que no son artículos reales
SKIP_DESCRIPTIONS = [
    "TASA RECICLAJE",
    "WEBFREIGHT",
    "TARIFA",
    "CATALOGO",
    "CARGO PORTES",
    "SUMINISTRAR EN",
    "LLAMAR ANTES",
    "CONTACTO",
    "PARTIDA",
    "CAMINO",
]

# Rangos de columnas (x0)
COL_CODIGO    = (0,   90)
COL_DESC      = (90,  280)
COL_NUMS      = (280, 600)   # todo el bloque numérico
COL_PRECIO_N  = (490, 540)   # Precio Neto
COL_IMPORTE   = (540, 700)   # Importe EUR


# ---------------------------------------------------------------------------
# Detección de tipo de documento
# ---------------------------------------------------------------------------

def _extract_raw_text(pdf_path: str) -> str:
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text += (page.extract_text() or "") + "\n"
    return text


def detect_doc_type(pdf_path: str) -> str:
    """
    Devuelve 'pedido' si el PDF es un pedido emitido por Aquacenter,
    o 'confirmacion' si es una confirmación de proveedor.
    """
    text = _extract_raw_text(pdf_path)
    upper = text.upper()
    for marker in AQUACENTER_MARKERS:
        if marker.upper() in upper:
            if "SU CÓDIGO" in upper or "SU CODIGO" in upper:
                return "pedido"
    return "confirmacion"


# ---------------------------------------------------------------------------
# Parser de PEDIDO — por coordenadas x (robusto contra descripciones con números)
# ---------------------------------------------------------------------------

def parse_pedido(pdf_path: str) -> dict:
    header = _parse_pedido_header_coords(pdf_path)
    lines = _parse_pedido_lines_coords(pdf_path)
    return {"tipo": "pedido", "cabecera": header, "lineas": lines}


def _parse_pedido_header_coords(pdf_path: str) -> dict:
    """Extrae cabecera usando la tabla de 2 filas que pdfplumber sí detecta."""
    header = {}
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        tables = page.extract_tables()
        for t in tables:
            if len(t) >= 2 and t[0] and "Pedido" in str(t[0]):
                vals = t[1]
                header["pedido"] = (vals[0] or "").strip()
                header["fecha"] = (vals[1] or "").strip()
                header["proveedor_codigo"] = (vals[2] or "").strip()
                break
        # Proveedor nombre: primera palabra de x>300 antes del header de tabla
        words = page.extract_words()
        candidatos = [w["text"] for w in words if w["x0"] > 300 and w["top"] < 145]
        # Toma las palabras que forman el nombre hasta llegar a un número postal
        nombre_parts = []
        for w in candidatos:
            if re.match(r"^\d{5}$", w):
                break
            nombre_parts.append(w)
        if nombre_parts:
            header["proveedor_nombre"] = " ".join(nombre_parts)
    return header


def _parse_pedido_lines_coords(pdf_path: str) -> list:
    """
    Extrae líneas de artículos usando posición x de cada palabra.

    Agrupa palabras por fila (top ±1px), luego clasifica cada fila:
    - Si tiene una palabra en columna código (x<90) → es una línea de artículo
    - Reconstruye los campos numéricos por posición
    """
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()

            # Encontrar el top del header de líneas ("Su Código")
            header_top = None
            footer_top = None
            for w in words:
                if w["text"] in ("Código", "CÓDIGO") and w["x0"] < 50:
                    header_top = w["top"]
                if "Importe" in w["text"] and w["x0"] < 50 and header_top and w["top"] > header_top + 10:
                    footer_top = w["top"]
                    break

            if header_top is None:
                continue

            # Filtrar palabras en la zona de líneas
            line_words = [
                w for w in words
                if w["top"] > header_top + 5
                and (footer_top is None or w["top"] < footer_top)
            ]

            # Agrupar por fila con tolerancia ±2px
            # Sage genera código/desc 1px más abajo que los valores numéricos
            rows: dict[int, list] = {}
            for w in line_words:
                top = w["top"]
                matched_key = None
                for existing_key in rows:
                    if abs(top - existing_key) <= 2:
                        matched_key = existing_key
                        break
                if matched_key is None:
                    matched_key = top
                    rows[matched_key] = []
                rows[matched_key].append(w)

            for top_key in sorted(rows.keys()):
                row = sorted(rows[top_key], key=lambda w: w["x0"])

                # ¿Tiene palabra en columna código?
                cod_words = [w for w in row if w["x0"] < COL_CODIGO[1]]
                if not cod_words:
                    continue
                cod = " ".join(w["text"] for w in cod_words).strip()

                # Descripción
                desc_words = [w for w in row if COL_DESC[0] <= w["x0"] < COL_DESC[1]]
                desc = " ".join(w["text"] for w in desc_words).strip()

                if _is_skip_line(desc) or _is_skip_line(cod):
                    continue

                # Valores numéricos por zona
                precio_n_words = [w for w in row if COL_PRECIO_N[0] <= w["x0"] < COL_PRECIO_N[1]]
                importe_words = [w for w in row if w["x0"] >= COL_IMPORTE[0]]
                mid_num_words = [
                    w for w in row
                    if COL_NUMS[0] <= w["x0"] < COL_PRECIO_N[0]
                    and re.match(r"[\d\.,]+", w["text"])
                ]

                if not importe_words:
                    continue  # fila sin importe → no es artículo

                importe = _parse_num(importe_words[-1]["text"])
                precio_neto = _parse_num(precio_n_words[0]["text"]) if precio_n_words else None

                # mid_num_words: [unidades, precio_vta] o [unidades, precio_vta, dto]
                nums = [_parse_num(w["text"]) for w in mid_num_words]
                cantidad = nums[0] if len(nums) >= 1 else None
                precio_vta = nums[1] if len(nums) >= 2 else None
                dto = nums[2] if len(nums) >= 3 else 0.0

                lines.append({
                    "cod_proveedor": cod,
                    "descripcion": desc,
                    "cantidad": cantidad,
                    "precio_vta": precio_vta,
                    "dto": dto,
                    "precio_neto": precio_neto,
                    "importe": importe,
                })

    return lines


def _parse_pedido_lines(text: str) -> list:
    """Legacy — ya no se usa, reemplazado por _parse_pedido_lines_coords."""
    lines = []
    m = re.search(r"Su Código\s+Descripción\s+Unidades.*?\n(.*?)Importe neto", text, re.DOTALL)
    if not m:
        return lines

    block = m.group(1)
    pattern = re.compile(
        r"^(\S[\w\.\-/]+)\s+"
        r"(.+?)\s+"
        r"(\d+[,\.]?\d*)\s+"
        r"(\d+[,\.]?\d+)\s+"
        r"(\d+[,\.]?\d+)\s+"
        r"(\d+[,\.]?\d+)\s+"
        r"(\d+[,\.]?\d+)",
        re.MULTILINE
    )
    for m_line in pattern.finditer(block):
        desc = m_line.group(2).strip()
        if _is_skip_line(desc):
            continue
        lines.append({
            "cod_proveedor": m_line.group(1).strip(),
            "descripcion": desc,
            "cantidad": _parse_num(m_line.group(3)),
            "precio_vta": _parse_num(m_line.group(4)),
            "dto": _parse_num(m_line.group(5)),
            "precio_neto": _parse_num(m_line.group(6)),
            "importe": _parse_num(m_line.group(7)),
        })
    return lines


# ---------------------------------------------------------------------------
# Parser de CONFIRMACIÓN — parsers específicos por proveedor conocido
# ---------------------------------------------------------------------------

# Identificadores de proveedor en el texto del documento
_SUPPLIER_SIGNATURES = {
    "fluidra":   ["FLUIDRA COMERCIAL", "FLUIDRA", "Acuse de recibo pedido"],
    "dab":       ["DAB PUMPS IBERICA", "DAB PUMPS"],
    "potermic":  ["POTERMIC, S.A.", "POTERMIC"],
}


def proveedores_locales_confirmacion() -> list[str]:
    """Proveedores cubiertos por parser local para confirmaciones (sin fallback API)."""
    return sorted(_SUPPLIER_SIGNATURES.keys())


def _detect_supplier(text: str) -> str:
    """Devuelve 'fluidra', 'dab', 'potermic' o 'unknown'."""
    upper = text.upper()
    for supplier, signatures in _SUPPLIER_SIGNATURES.items():
        if any(sig.upper() in upper for sig in signatures):
            return supplier
    return "unknown"


def parse_confirmacion_local(pdf_path: str) -> dict:
    """
    Parser sin API para confirmaciones de proveedores conocidos.
    Detecta el proveedor y enruta al parser específico.
    Para proveedores desconocidos devuelve líneas vacías → fallback a API OpenAI.
    """
    text = _extract_raw_text(pdf_path)
    supplier = _detect_supplier(text)

    if supplier == "fluidra":
        return _parse_fluidra_conf(pdf_path, text)
    elif supplier == "dab":
        return _parse_dab_conf(pdf_path, text)
    elif supplier == "potermic":
        return _parse_potermic_conf(pdf_path, text)
    else:
        return {"tipo": "confirmacion", "cabecera": {}, "lineas": []}


# ---- FLUIDRA ----------------------------------------------------------------
# Columnas en el acuse de recibo Fluidra:
#   Artículo(36) | Descripción(151) | Cantidad(372) | Precio(420) | Dto1.(463) | Dto2.(497) | Importe(545)
# Líneas con artículo tienen x0 < 100; sub-líneas (IMPUESTO, WEBFREIGHT) se filtran

def _parse_fluidra_conf(pdf_path: str, text: str) -> dict:
    header = {
        "pedido_proveedor": _re_find(r"Acuse de recibo pedido\s+(\d+)", text),
        "referencia_cliente": _re_find(r"Su\s+Referencia[:\s]+(\S+)", text),
        "fecha": _re_find(r"Documento editado\s+(\d{2}/\d{2}/\d{4})", text),
        "proveedor_nombre": "FLUIDRA COMERCIAL ESPAÑA, S.A.U",
    }

    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        words = pdf.pages[0].extract_words()

    rows = _group_by_row(words)
    # Encontrar top de la cabecera de tabla (fila con "Artículo")
    header_top = next(
        (top for top, row in sorted(rows.items())
         if any(w["text"] == "Artículo" for w in row)), None
    )
    if header_top is None:
        return {"tipo": "confirmacion", "cabecera": header, "lineas": lines}

    # Posiciones x de columnas (Fluidra)
    X_QTY    = 372
    X_PRECIO = 420
    X_DTO    = 463
    X_IMP    = 545

    for top, row in sorted(rows.items()):
        if top <= header_top:
            continue
        row = sorted(row, key=lambda w: w["x0"])

        # El código de artículo está en x < 100
        code_words = [w for w in row if w["x0"] < 100]
        if not code_words:
            continue
        cod = code_words[0]["text"].strip()
        if _is_skip_line(cod) or not re.match(r"^[A-Z0-9]", cod, re.IGNORECASE):
            continue
        # Ignorar sub-líneas de IMPUESTO (valores en col importe ~574 que son < 0.10)
        # y la línea Total/TOTAL
        if cod.upper() in ("TOTAL............", "IMPUESTO", "WEBFREIGHT"):
            continue

        desc_words = [w for w in row if 100 <= w["x0"] < X_QTY - 20
                      and not re.match(r"^\d", w["text"])]
        desc = " ".join(w["text"] for w in desc_words).strip()
        if _is_skip_line(desc):
            continue

        qty    = _first_num_near(row, X_QTY,    tol=40)
        precio = _first_num_near(row, X_PRECIO,  tol=40)
        dto    = _first_num_near(row, X_DTO,     tol=40) or 0.0
        importe = _first_num_near(row, X_IMP,   tol=40)

        if importe is None:
            continue  # fila sin importe → no es artículo

        lines.append({
            "cod_proveedor": cod,
            "descripcion": desc,
            "cantidad": qty,
            "precio_unitario": precio,
            "dto": dto,
            "importe": importe,
        })

    return {"tipo": "confirmacion", "cabecera": header, "lineas": lines}


# ---- DAB --------------------------------------------------------------------
# Columnas en la confirmación DAB:
#   Pos(18) | Artículo(43) | Descripción(128) | Ud.(276) | Cantidad(306) | Precio(373) | Dto.(403) | Importe(441) | VAT(505) | Salida(534)
# El código real es "Artículo" (segunda columna), no "Pos"

def _parse_dab_conf(pdf_path: str, text: str) -> dict:
    # En DAB, "Pedido cliente" y "Referencia" son cabeceras de columna en la misma línea.
    # El valor real de la referencia está en la línea siguiente.
    ref_cliente = _re_find(r"Pedido\s+cliente\s+Referencia\s+Banco\s*\n([A-Z0-9]+(?:\s+[A-Z0-9]+)?)", text)
    if not ref_cliente:
        # Fallback: buscar "DC XX" o similar
        ref_cliente = _re_find(r"\bDC\s+\d+\b", text)
    header = {
        "pedido_proveedor": _re_find(r"Pedido\s+(SS\d+)", text),
        "referencia_cliente": ref_cliente,
        "fecha": _re_find(r"Fecha\s+(\d{2}\.\d{2}\.\d{4})", text),
        "proveedor_nombre": "DAB PUMPS IBERICA, S.L.",
    }

    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        words = pdf.pages[0].extract_words()

    rows = _group_by_row(words)
    header_top = next(
        (top for top, row in sorted(rows.items())
         if any(w["text"] == "Artículo" for w in row)), None
    )
    if header_top is None:
        return {"tipo": "confirmacion", "cabecera": header, "lineas": lines}

    # En DAB, "Artículo" está en x≈43, "Descripción" en x≈128
    X_ART    = 43
    X_DESC   = 128
    X_QTY    = 306
    X_PRECIO = 373
    X_DTO    = 403
    X_IMP    = 441

    for top, row in sorted(rows.items()):
        if top <= header_top:
            continue
        row = sorted(row, key=lambda w: w["x0"])

        # Código está en la columna "Artículo" (x≈43), no en "Pos" (x≈18)
        art_words = [w for w in row if 30 <= w["x0"] <= 120
                     and re.match(r"^[A-Z0-9]", w["text"], re.IGNORECASE)]
        if not art_words:
            continue
        cod = art_words[0]["text"].strip()
        # Filtrar si el código es completamente numérico (peso, volumen, etc.)
        if re.match(r"^[\d,\.]+$", cod):
            continue
        if _is_skip_line(cod) or cod in ("Fecha", "LLAMAR", "CONTACTO:"):
            continue

        desc_words = [w for w in row if w["x0"] >= X_DESC and w["x0"] < X_QTY - 20
                      and not re.match(r"^[\d,\.]+$", w["text"])]
        desc = " ".join(w["text"] for w in desc_words).strip()

        qty    = _first_num_near(row, X_QTY,    tol=40)
        precio = _first_num_near(row, X_PRECIO,  tol=40)
        dto    = _first_num_near(row, X_DTO,     tol=20) or 0.0
        importe = _first_num_near(row, X_IMP,   tol=40)

        if importe is None and qty is None:
            continue

        lines.append({
            "cod_proveedor": cod,
            "descripcion": desc,
            "cantidad": qty,
            "precio_unitario": precio,
            "dto": dto,
            "importe": importe,
        })

    return {"tipo": "confirmacion", "cabecera": header, "lineas": lines}


# ---- POTERMIC ---------------------------------------------------------------
# Columnas en el pedido de venta Potermic:
#   Nº(33) | Descripción(99) | Cantidad(330+383 UDS) | Precio(429) | % Dto.(474) | Importe(531)

def _parse_potermic_conf(pdf_path: str, text: str) -> dict:
    header = {
        "pedido_proveedor": _re_find(r"Pedido\s+(PV\d+)", text),
        "referencia_cliente": _re_find(r"Ref\.\s+(\d+)", text),
        "fecha": _re_find(r"Fecha\s+emisi[oó]n\s+documento[:\s]+(\d{2}/\d{2}/\d{4})", text),
        "proveedor_nombre": "POTERMIC, S.A.",
    }

    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        words = pdf.pages[0].extract_words()

    rows = _group_by_row(words)
    header_top = next(
        (top for top, row in sorted(rows.items())
         if any(w["text"] in ("Nº", "N°") for w in row)), None
    )
    if header_top is None:
        return {"tipo": "confirmacion", "cabecera": header, "lineas": lines}

    X_QTY    = 349   # cantidad (antes de "UDS")
    X_PRECIO = 430
    X_DTO    = 474
    X_IMP    = 535

    for top, row in sorted(rows.items()):
        if top <= header_top:
            continue
        row = sorted(row, key=lambda w: w["x0"])

        code_words = [w for w in row if w["x0"] < 90
                      and re.match(r"^[A-Z0-9]", w["text"], re.IGNORECASE)]
        if not code_words:
            continue
        cod = code_words[0]["text"].strip()
        if _is_skip_line(cod) or cod in ("Forma", "Base", "Total", "Importe"):
            continue

        desc_words = [w for w in row if 90 <= w["x0"] < X_QTY - 20
                      and w["text"] not in ("UDS",)]
        desc = " ".join(w["text"] for w in desc_words).strip()
        if _is_skip_line(desc):
            continue

        qty     = _first_num_near(row, X_QTY,    tol=40)
        precio  = _first_num_near(row, X_PRECIO,  tol=30)
        dto     = _first_num_near(row, X_DTO,     tol=20) or 0.0
        importe = _first_num_near(row, X_IMP,     tol=30)

        if importe is None:
            continue

        lines.append({
            "cod_proveedor": cod,
            "descripcion": desc,
            "cantidad": qty,
            "precio_unitario": precio,
            "dto": dto,
            "importe": importe,
        })

    return {"tipo": "confirmacion", "cabecera": header, "lineas": lines}


# ---- Helpers ----------------------------------------------------------------

def _re_find(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _first_num_near(row: list, x_target: float, tol: float = 30) -> float | None:
    """Devuelve el primer valor numérico de la fila más cercano a x_target."""
    candidates = [
        w for w in row
        if abs(w["x0"] - x_target) <= tol and re.match(r"^[\d][0-9\.,]*$", w["text"])
    ]
    if not candidates:
        return None
    best = min(candidates, key=lambda w: abs(w["x0"] - x_target))
    try:
        return _parse_num(best["text"])
    except ValueError:
        return None


def _group_by_row(words: list, tol: float = 2.0) -> dict:
    """Agrupa palabras por fila con tolerancia tol px."""
    rows: dict[float, list] = {}
    for w in words:
        top = w["top"]
        matched_key = None
        for key in rows:
            if abs(top - key) <= tol:
                matched_key = key
                break
        if matched_key is None:
            matched_key = top
            rows[matched_key] = []
        rows[matched_key].append(w)
    return rows


# ---------------------------------------------------------------------------
# Parser de CONFIRMACIÓN (OpenAI Responses API — fallback para proveedores desconocidos)
# ---------------------------------------------------------------------------

def parse_confirmacion(pdf_path: str) -> dict:
    """
    Extrae cabecera y líneas de una confirmación de proveedor vía OpenAI (PDF como input_file).
    Requiere OPENAI_API_KEY y un modelo con visión (OPENAI_MODEL, por defecto gpt-5-codex).
    """
    client = OpenAI()

    with open(pdf_path, "rb") as f:
        pdf_b64 = base64.b64encode(f.read()).decode("utf-8")

    filename = Path(pdf_path).name

    prompt = """Eres un extractor de datos de documentos comerciales. Analiza este documento
(confirmación de pedido, acuse de recibo o pedido de venta de proveedor) y extrae:

1. CABECERA con estos campos (usa null si no existe):
   - pedido_proveedor: número de pedido del proveedor
   - referencia_cliente: referencia del pedido del cliente (busca "Su Referencia", "Ref.", "Pedido cliente", "Pedido cliente DC XX")
   - fecha: fecha del documento (formato DD/MM/YYYY o YYYY-MM-DD)
   - proveedor_nombre: nombre del proveedor emisor

2. LÍNEAS de artículos. Para cada artículo:
   - cod_proveedor: código/referencia del artículo (campo "Artículo", "N°", "Pos", etc.)
   - descripcion: descripción del producto
   - cantidad: cantidad numérica
   - precio_unitario: precio unitario sin IVA
   - dto: porcentaje de descuento (0 si no hay)
   - importe: importe total de la línea sin IVA

IMPORTANTE:
- Excluye líneas que NO sean artículos: portes, tasas, catálogos, cargos de tramitación, líneas en blanco
- Los números usan coma o punto decimal — normaliza siempre a float con punto decimal
- Si una línea de artículo tiene campo "Cantidad" con texto "UN" o "NR", extrae solo el número

Devuelve ÚNICAMENTE un JSON con esta estructura exacta, sin explicaciones:
{
  "tipo": "confirmacion",
  "cabecera": {
    "pedido_proveedor": "...",
    "referencia_cliente": "...",
    "fecha": "...",
    "proveedor_nombre": "..."
  },
  "lineas": [
    {
      "cod_proveedor": "...",
      "descripcion": "...",
      "cantidad": 0.0,
      "precio_unitario": 0.0,
      "dto": 0.0,
      "importe": 0.0
    }
  ]
}"""

    response = client.responses.create(
        model=_model_openai(),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "filename": filename,
                        "file_data": f"data:application/pdf;base64,{pdf_b64}",
                    },
                    {"type": "input_text", "text": prompt},
                ],
            },
        ],
    )

    raw = (response.output_text or "").strip()
    # Extraer JSON si viene envuelto en ```json ... ```
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            "La API OpenAI no devolvió JSON válido para la confirmación. "
            f"Fragmento inicial: {raw[:500]!r}..."
        ) from e


# ---------------------------------------------------------------------------
# Entry point unificado
# ---------------------------------------------------------------------------

def parse_pdf(pdf_path: str) -> dict:
    """
    Detecta el tipo de documento y extrae cabecera + líneas.

    Para pedidos: parser determinista por coordenadas (sin API).
    Para confirmaciones: intenta parser local primero; si extrae 0 líneas,
    usa OpenAI Responses API como fallback (requiere OPENAI_API_KEY).
    """
    doc_type = detect_doc_type(pdf_path)
    if doc_type == "pedido":
        return parse_pedido(pdf_path)

    # Confirmación: intentar parser local
    result = parse_confirmacion_local(pdf_path)
    if result["lineas"]:
        return result

    if not os.environ.get("OPENAI_API_KEY"):
        raise OpenAINotConfiguredError(
            "Se necesita OPENAI_API_KEY en el entorno para confirmaciones que el parser local no cubre."
        )

    print("      → Parser local sin resultado, usando API OpenAI como fallback...")
    return parse_confirmacion(pdf_path)


def extract_pdf_document(pdf_path: str, use_ai: bool = False) -> tuple[dict, list[str]]:
    """
    Extracción orientada a endpoint /extract.
    - use_ai=False: evita fallback a API para MVP.
    - use_ai=True: permite fallback OpenAI en confirmaciones desconocidas.
    """
    warnings: list[str] = []
    doc_type = detect_doc_type(pdf_path)
    if doc_type == "pedido":
        return parse_pedido(pdf_path), warnings

    local = parse_confirmacion_local(pdf_path)
    if local.get("lineas"):
        return local, warnings

    warnings.append("parser_local_sin_lineas")
    if not use_ai:
        return local, warnings

    if not os.environ.get("OPENAI_API_KEY"):
        raise OpenAINotConfiguredError(
            "use_ai=true requiere OPENAI_API_KEY para fallback en confirmaciones no soportadas localmente."
        )
    ai = parse_confirmacion(pdf_path)
    warnings.append("fallback_openai_usado")
    return ai, warnings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_skip_line(descripcion: str) -> bool:
    desc_upper = descripcion.upper()
    return any(s in desc_upper for s in SKIP_DESCRIPTIONS)


def _parse_num(s: str) -> float:
    """
    Convierte un número en formato español a float.
    Ejemplos:
      '1.720,80' → 1720.80   (punto = miles, coma = decimal)
      '573,6000' → 573.6     (coma = decimal)
      '50,00'    → 50.0
      '3,00'     → 3.0
      '277,30'   → 277.3
    """
    s = s.strip()
    # Patrón miles+decimal español: dígitos con puntos de miles y coma decimal
    if re.match(r"^\d{1,3}(\.\d{3})+,\d+$", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        # Solo coma decimal
        s = s.replace(",", ".")
    return float(s)
