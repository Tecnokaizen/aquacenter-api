from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.core.config import settings
from app.core.security import require_bearer_auth
from app.services.compare_mvp import compare_documents_mvp
from app.services.pdf_extractor import OpenAINotConfiguredError, extract_pdf_document

router = APIRouter()
logger = logging.getLogger("aquacenter.api.compare")

ALLOWED_COMPARE_MODULES = {"confirmacion_pedidos"}


@router.post("/compare")
async def compare(
    request: Request,
    origin_file: UploadFile = File(...),
    target_file: UploadFile = File(...),
    module: str = Form(...),
    use_ai: bool = Form(False),
    _auth: None = Depends(require_bearer_auth),
) -> dict:
    _assert_module(module)

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
    _assert_pdf_upload(
        filename=origin_file.filename or "",
        content_type=origin_file.content_type,
        content=origin_bytes,
        field_name="origin_file",
    )
    _assert_pdf_upload(
        filename=target_file.filename or "",
        content_type=target_file.content_type,
        content=target_bytes,
        field_name="target_file",
    )

    origin_path = Path(settings.uploads_dir) / f"{job_id}_origin_{_safe_name(origin_file.filename or 'origin.pdf')}"
    target_path = Path(settings.uploads_dir) / f"{job_id}_target_{_safe_name(target_file.filename or 'target.pdf')}"
    origin_path.parent.mkdir(parents=True, exist_ok=True)
    origin_path.write_bytes(origin_bytes)
    target_path.write_bytes(target_bytes)

    try:
        payload, warnings = await asyncio.wait_for(
            asyncio.to_thread(
                _compare_sync,
                origin_path=str(origin_path),
                target_path=str(target_path),
                job_id=job_id,
                module=module,
                use_ai=use_ai,
            ),
            timeout=settings.request_timeout_seconds,
        )
    except OpenAINotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail={"overall_status": "failed", "message": str(exc)},
        ) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail={"overall_status": "failed", "message": "Timeout procesando comparación."},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"overall_status": "failed", "message": str(exc)},
        ) from exc

    if warnings:
        payload["warnings"] = warnings
    output_excel = payload.get("output_excel")
    if isinstance(output_excel, str):
        payload["output_excel_url"] = str(request.base_url).rstrip("/") + output_excel

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


def _assert_pdf_upload(
    *,
    filename: str,
    content_type: str | None,
    content: bytes,
    field_name: str,
) -> None:
    normalized_type = (content_type or "").split(";")[0].lower().strip()
    filename_lower = filename.lower()
    allowed_pdf_mime = {"application/pdf", "application/x-pdf"}
    relaxed_mime = {"application/octet-stream", "binary/octet-stream", ""}

    mime_ok = normalized_type in allowed_pdf_mime or (
        normalized_type in relaxed_mime and filename_lower.endswith(".pdf")
    )
    if not mime_ok:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{field_name} debe ser PDF. content-type recibido: "
                f"{normalized_type or '(vacío)'}"
            ),
        )
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail=f"{field_name} no parece un PDF válido.")


def _safe_name(name: str) -> str:
    return Path(name).name.replace("/", "_").replace("\\", "_")


def _compare_sync(
    *,
    origin_path: str,
    target_path: str,
    job_id: str,
    module: str,
    use_ai: bool,
) -> tuple[dict, list[str]]:
    origin_doc, origin_warnings = extract_pdf_document(origin_path, use_ai=use_ai)
    target_doc, target_warnings = extract_pdf_document(target_path, use_ai=use_ai)
    payload = compare_documents_mvp(
        origin_doc=origin_doc,
        target_doc=target_doc,
        job_id=job_id,
        module=module,
        output_dir=settings.outputs_dir,
    )
    return payload, origin_warnings + target_warnings


def _log(event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, ensure_ascii=False))
