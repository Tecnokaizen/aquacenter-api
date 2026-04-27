from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.security import require_bearer_auth
from app.services.email_extractor import extract_email_document
from app.services.excel_extractor import extract_table_document
from app.services.excel_reporter import exportar_excel_extraccion
from app.services.pdf_extractor import OpenAINotConfiguredError, extract_pdf_document
from app.services.supplier_cleaner import clean_supplier_name

router = APIRouter()
logger = logging.getLogger("aquacenter.api.extract")

ALLOWED_MODULES = {
    "confirmacion_pedidos",
    "revision_facturas",
    "actualizacion_tarifas",
}
ALLOWED_SUFFIXES = {".pdf", ".xlsx", ".xls", ".csv", ".eml"}


@router.post("/extract")
async def extract(
    request: Request,
    file: UploadFile = File(...),
    module: str = Form(...),
    use_ai: bool = Form(False),
    _auth: None = Depends(require_bearer_auth),
) -> dict:
    _assert_module(module)

    job_id = f"job_{uuid.uuid4().hex[:10]}"
    _log("extract_received", job_id=job_id, filename=file.filename, module=module, use_ai=use_ai)

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Extensión no soportada: {suffix}")
    if suffix == ".pdf":
        _assert_pdf_content_type(file)

    max_bytes = settings.max_upload_mb * 1024 * 1024
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"Archivo demasiado grande. Máximo: {settings.max_upload_mb} MB.",
        )

    upload_path = Path(settings.uploads_dir) / f"{job_id}_{_safe_name(file.filename or 'input')}"
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(content)

    try:
        data, warnings, extraction_method, confidence = await asyncio.wait_for(
            asyncio.to_thread(_dispatch_extract, str(upload_path), suffix, use_ai),
            timeout=settings.request_timeout_seconds,
        )
    except OpenAINotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=503, detail="Timeout procesando el documento.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    output_name = f"{job_id}.xlsx"
    output_path = Path(settings.outputs_dir) / output_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    exportar_excel_extraccion(
        output_path=str(output_path),
        job_id=job_id,
        module=module,
        source_filename=file.filename or "input",
        data=data,
        warnings=warnings,
    )

    doc_type = data.get("tipo", "desconocido")
    supplier = clean_supplier_name((data.get("cabecera") or {}).get("proveedor_nombre"))
    if supplier and isinstance(data.get("cabecera"), dict):
        data["cabecera"]["proveedor_nombre"] = supplier
    lines_count = len(data.get("lineas", []) or [])
    output_excel_rel = f"/outputs/{output_name}"
    output_excel_url = str(request.base_url).rstrip("/") + output_excel_rel

    _log(
        "extract_completed",
        job_id=job_id,
        filename=file.filename,
        module=module,
        document_type=doc_type,
        supplier=supplier,
        lines_count=lines_count,
        extraction_method=extraction_method,
        confidence=confidence,
    )

    return {
        "success": True,
        "job_id": job_id,
        "document_type": doc_type,
        "supplier": supplier,
        "extraction_method": extraction_method,
        "confidence": confidence,
        "lines_count": lines_count,
        "output_excel": output_excel_rel,
        "output_excel_url": output_excel_url,
        "warnings": warnings,
    }


@router.get("/outputs/{file_name}")
def download_output(file_name: str) -> FileResponse:
    safe = _safe_name(file_name)
    if safe != file_name:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido.")
    output_path = Path(settings.outputs_dir) / safe
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    return FileResponse(
        str(output_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=output_path.name,
    )


def _assert_module(module: str) -> None:
    if module not in ALLOWED_MODULES:
        raise HTTPException(
            status_code=400,
            detail=f"module inválido: {module}. Valores: {', '.join(sorted(ALLOWED_MODULES))}",
        )


def _assert_pdf_content_type(file: UploadFile) -> None:
    content_type = (file.content_type or "").split(";")[0].lower().strip()
    if content_type not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(
            status_code=400,
            detail=(
                "El fichero PDF debe enviarse con content-type application/pdf, "
                f"recibido: {content_type or '(vacío)'}"
            ),
        )


def _safe_name(name: str) -> str:
    return Path(name).name.replace("/", "_").replace("\\", "_")


def _dispatch_extract(path: str, suffix: str, use_ai: bool) -> tuple[dict, list[str], str, float]:
    warnings: list[str] = []
    if suffix == ".pdf":
        data, warnings = extract_pdf_document(path, use_ai=use_ai)
        extraction_method = "openai_fallback" if "fallback_openai_usado" in warnings else "local_parser"
        return data, warnings, extraction_method, _estimate_confidence(extraction_method, data)
    if suffix in {".xlsx", ".xls", ".csv"}:
        data = extract_table_document(path)
        if use_ai:
            warnings.append("use_ai_ignorado_para_tabular")
        extraction_method = "tabular_parser"
        return data, warnings, extraction_method, _estimate_confidence(extraction_method, data)
    if suffix == ".eml":
        data = extract_email_document(path)
        if use_ai:
            warnings.append("use_ai_ignorado_para_email")
        extraction_method = "email_parser"
        return data, warnings, extraction_method, _estimate_confidence(extraction_method, data)
    raise ValueError(f"Formato no soportado: {suffix}")


def _estimate_confidence(extraction_method: str, data: dict) -> float:
    lines_count = len(data.get("lineas", []) or [])
    if extraction_method == "tabular_parser":
        return 0.99 if lines_count > 0 else 0.6
    if extraction_method == "email_parser":
        return 0.9 if lines_count > 0 else 0.75
    if extraction_method == "openai_fallback":
        return 0.86 if lines_count > 0 else 0.55
    # local_parser
    return 0.96 if lines_count > 0 else 0.5


def _log(event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, ensure_ascii=False))
