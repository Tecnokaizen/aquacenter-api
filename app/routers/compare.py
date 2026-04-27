from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.core.config import settings
from app.core.security import require_bearer_auth
from app.services.compare_mvp import compare_documents_mvp
from app.services.pdf_extractor import OpenAINotConfiguredError, extract_pdf_document

router = APIRouter()
logger = logging.getLogger("aquacenter.api.compare")

ALLOWED_COMPARE_MODULES = {"confirmacion_pedidos"}


@router.post("/compare")
async def compare(
    origin_file: UploadFile = File(...),
    target_file: UploadFile = File(...),
    module: str = Form(...),
    use_ai: bool = Form(False),
    _auth: None = Depends(require_bearer_auth),
) -> dict:
    _assert_module(module)
    _assert_pdf_content_type(origin_file, field_name="origin_file")
    _assert_pdf_content_type(target_file, field_name="target_file")

    job_id = f"job_{uuid.uuid4().hex[:10]}"
    _log(
        "compare_received",
        job_id=job_id,
        origin_filename=origin_file.filename,
        target_filename=target_file.filename,
        module=module,
        use_ai=use_ai,
    )

    max_bytes = settings.max_upload_mb * 1024 * 1024
    origin_bytes = await origin_file.read()
    target_bytes = await target_file.read()

    if not origin_bytes or not target_bytes:
        raise HTTPException(status_code=400, detail="origin_file y target_file son obligatorios y no vacíos.")
    if len(origin_bytes) > max_bytes or len(target_bytes) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"Archivo demasiado grande. Máximo: {settings.max_upload_mb} MB por archivo.",
        )

    origin_path = Path(settings.uploads_dir) / f"{job_id}_origin_{_safe_name(origin_file.filename or 'origin.pdf')}"
    target_path = Path(settings.uploads_dir) / f"{job_id}_target_{_safe_name(target_file.filename or 'target.pdf')}"
    origin_path.parent.mkdir(parents=True, exist_ok=True)
    origin_path.write_bytes(origin_bytes)
    target_path.write_bytes(target_bytes)

    try:
        origin_doc, origin_warnings = extract_pdf_document(str(origin_path), use_ai=use_ai)
        target_doc, target_warnings = extract_pdf_document(str(target_path), use_ai=use_ai)
    except OpenAINotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload = compare_documents_mvp(
        origin_doc=origin_doc,
        target_doc=target_doc,
        job_id=job_id,
        module=module,
        output_dir=settings.outputs_dir,
    )
    warnings = origin_warnings + target_warnings
    if warnings:
        payload["warnings"] = warnings

    _log(
        "compare_completed",
        job_id=job_id,
        supplier=payload.get("supplier"),
        lines_origin=payload.get("lines_origin"),
        lines_target=payload.get("lines_target"),
        lines_ok=payload.get("lines_ok"),
        incidents_total=payload.get("incidents_total"),
    )
    return payload


def _assert_module(module: str) -> None:
    if module not in ALLOWED_COMPARE_MODULES:
        raise HTTPException(
            status_code=400,
            detail=f"module inválido: {module}. Valores: {', '.join(sorted(ALLOWED_COMPARE_MODULES))}",
        )


def _assert_pdf_content_type(file: UploadFile, *, field_name: str) -> None:
    content_type = (file.content_type or "").split(";")[0].lower().strip()
    if content_type not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{field_name} debe enviarse con content-type application/pdf, "
                f"recibido: {content_type or '(vacío)'}"
            ),
        )


def _safe_name(name: str) -> str:
    return Path(name).name.replace("/", "_").replace("\\", "_")


def _log(event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, ensure_ascii=False))

