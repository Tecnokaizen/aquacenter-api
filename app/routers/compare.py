from __future__ import annotations

import asyncio
import html
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.core.security import require_bearer_auth
from app.core.url_builder import build_public_url, ensure_relative_path
from app.services.compare_batch_mvp import BatchInputDocument, run_compare_batch_mvp
from app.services.compare_runtime import run_compare_pair
from app.services.pdf_extractor import OpenAINotConfiguredError

router = APIRouter()
logger = logging.getLogger("aquacenter.api.compare")

ALLOWED_COMPARE_MODULES = {"confirmacion_pedidos"}


@router.get("/ui/compare", response_class=HTMLResponse)
async def compare_ui() -> HTMLResponse:
    return HTMLResponse(_render_compare_page())


@router.post("/ui/compare", response_class=HTMLResponse)
async def compare_ui_submit(
    origin_file: UploadFile = File(...),
    target_file: UploadFile = File(...),
    module: str = Form("confirmacion_pedidos"),
    use_ai: bool = Form(False),
) -> HTMLResponse:
    try:
        _assert_module(module)
        job_id = f"job_{uuid.uuid4().hex[:10]}"
        origin_path = await _store_pdf_upload(
            upload=origin_file,
            job_prefix=job_id,
            role="origin",
            field_name="origin_file",
        )
        target_path = await _store_pdf_upload(
            upload=target_file,
            job_prefix=job_id,
            role="target",
            field_name="target_file",
        )

        payload, warnings = await asyncio.wait_for(
            asyncio.to_thread(
                run_compare_pair,
                origin_path=str(origin_path),
                target_path=str(target_path),
                job_id=job_id,
                module=module,
                use_ai=use_ai,
                output_dir=settings.outputs_dir,
            ),
            timeout=settings.request_timeout_seconds,
        )
        if warnings:
            payload["warnings"] = warnings

        output_excel_href = ensure_relative_path(str(payload.get("output_excel") or ""))
        return HTMLResponse(_render_compare_page(result=payload, output_excel_href=output_excel_href, use_ai=use_ai))
    except HTTPException as exc:
        return HTMLResponse(
            _render_compare_page(
                error=_extract_http_error_message(exc.detail),
                use_ai=use_ai,
            ),
            status_code=exc.status_code,
        )
    except OpenAINotConfiguredError as exc:
        return HTMLResponse(
            _render_compare_page(error=str(exc), use_ai=use_ai),
            status_code=503,
        )
    except asyncio.TimeoutError:
        return HTMLResponse(
            _render_compare_page(error="Timeout procesando comparación.", use_ai=use_ai),
            status_code=503,
        )
    except Exception:
        logger.exception("ui_compare_unhandled_error")
        return HTMLResponse(
            _render_compare_page(error="Error interno procesando la comparación.", use_ai=use_ai),
            status_code=500,
        )


@router.get("/ui/batch", response_class=HTMLResponse)
async def compare_batch_ui() -> HTMLResponse:
    return HTMLResponse(_render_batch_page())


@router.post("/ui/batch", response_class=HTMLResponse)
async def compare_batch_ui_submit(
    files: list[UploadFile] = File(...),
    module: str = Form("confirmacion_pedidos"),
    use_ai: bool = Form(False),
) -> HTMLResponse:
    try:
        _assert_module(module)
        if not files:
            raise HTTPException(status_code=400, detail="Debes subir al menos un PDF.")

        batch_id = f"batch_{uuid.uuid4().hex[:10]}"
        input_documents: list[BatchInputDocument] = []
        for i, file in enumerate(files, start=1):
            stored_path = await _store_pdf_upload(
                upload=file,
                job_prefix=batch_id,
                role=f"doc_{i:03d}",
                field_name=f"files[{i - 1}]",
            )
            input_documents.append(
                BatchInputDocument(
                    filename=file.filename or f"document_{i:03d}.pdf",
                    path=str(stored_path),
                )
            )

        timeout_seconds = max(settings.request_timeout_seconds, settings.request_timeout_seconds * len(input_documents))
        payload = await asyncio.wait_for(
            asyncio.to_thread(
                run_compare_batch_mvp,
                batch_id=batch_id,
                module=module,
                use_ai=use_ai,
                input_documents=input_documents,
                output_dir=settings.outputs_dir,
            ),
            timeout=timeout_seconds,
        )
        batch_excel_href = ensure_relative_path(str(payload.get("batch_excel") or ""))
        return HTMLResponse(
            _render_batch_page(
                result=payload,
                batch_excel_href=batch_excel_href,
                overall_status=_batch_overall_status(payload),
                use_ai=use_ai,
            )
        )
    except HTTPException as exc:
        return HTMLResponse(
            _render_batch_page(
                error=_extract_http_error_message(exc.detail),
                overall_status="failed",
                use_ai=use_ai,
            ),
            status_code=exc.status_code,
        )
    except OpenAINotConfiguredError as exc:
        return HTMLResponse(
            _render_batch_page(error=str(exc), overall_status="failed", use_ai=use_ai),
            status_code=503,
        )
    except asyncio.TimeoutError:
        return HTMLResponse(
            _render_batch_page(error="Timeout procesando lote.", overall_status="failed", use_ai=use_ai),
            status_code=503,
        )
    except Exception:
        logger.exception("ui_batch_unhandled_error")
        return HTMLResponse(
            _render_batch_page(error="Error interno procesando lote.", overall_status="failed", use_ai=use_ai),
            status_code=500,
        )


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

    origin_path = await _store_pdf_upload(
        upload=origin_file,
        job_prefix=job_id,
        role="origin",
        field_name="origin_file",
    )
    target_path = await _store_pdf_upload(
        upload=target_file,
        job_prefix=job_id,
        role="target",
        field_name="target_file",
    )

    try:
        payload, warnings = await asyncio.wait_for(
            asyncio.to_thread(
                run_compare_pair,
                origin_path=str(origin_path),
                target_path=str(target_path),
                job_id=job_id,
                module=module,
                use_ai=use_ai,
                output_dir=settings.outputs_dir,
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
    except Exception as exc:  # pragma: no cover - seguridad defensiva en producción
        logger.exception("compare_unhandled_error", extra={"job_id": job_id})
        raise HTTPException(
            status_code=500,
            detail={"overall_status": "failed", "message": "Error interno en comparación."},
        ) from exc

    if warnings:
        payload["warnings"] = warnings
    output_excel = payload.get("output_excel")
    if isinstance(output_excel, str):
        payload["output_excel_url"] = build_public_url(request, output_excel)

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


@router.post("/compare-batch")
async def compare_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    module: str = Form(...),
    use_ai: bool = Form(False),
    _auth: None = Depends(require_bearer_auth),
) -> dict:
    _assert_module(module)
    if not files:
        raise HTTPException(status_code=400, detail="Debes enviar al menos un PDF en el campo files.")

    batch_id = f"batch_{uuid.uuid4().hex[:10]}"
    _log("compare_batch_received", batch_id=batch_id, documents_total=len(files), module=module, use_ai=use_ai)

    input_documents: list[BatchInputDocument] = []
    for i, file in enumerate(files, start=1):
        stored_path = await _store_pdf_upload(
            upload=file,
            job_prefix=batch_id,
            role=f"doc_{i:03d}",
            field_name=f"files[{i - 1}]",
        )
        input_documents.append(
            BatchInputDocument(
                filename=file.filename or f"document_{i:03d}.pdf",
                path=str(stored_path),
            )
        )

    timeout_seconds = max(settings.request_timeout_seconds, settings.request_timeout_seconds * len(input_documents))
    try:
        payload = await asyncio.wait_for(
            asyncio.to_thread(
                run_compare_batch_mvp,
                batch_id=batch_id,
                module=module,
                use_ai=use_ai,
                input_documents=input_documents,
                output_dir=settings.outputs_dir,
            ),
            timeout=timeout_seconds,
        )
    except OpenAINotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail={"overall_status": "failed", "message": str(exc)},
        ) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail={"overall_status": "failed", "message": "Timeout procesando lote."},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"overall_status": "failed", "message": str(exc)},
        ) from exc
    except Exception as exc:  # pragma: no cover - seguridad defensiva en producción
        logger.exception("compare_batch_unhandled_error", extra={"batch_id": batch_id})
        raise HTTPException(
            status_code=500,
            detail={"overall_status": "failed", "message": "Error interno procesando lote."},
        ) from exc

    batch_excel = payload.get("batch_excel")
    if isinstance(batch_excel, str):
        payload["batch_excel_url"] = build_public_url(request, batch_excel)
    else:
        payload["batch_excel_url"] = ""
    for pair in payload.get("pairs") or []:
        output_excel = pair.get("output_excel")
        if isinstance(output_excel, str):
            pair["output_excel_url"] = build_public_url(request, output_excel)

    _log(
        "compare_batch_completed",
        batch_id=batch_id,
        pairs_detected=payload.get("pairs_detected"),
        comparisons_ok=payload.get("comparisons_ok"),
        comparisons_with_incidents=payload.get("comparisons_with_incidents"),
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


async def _store_pdf_upload(
    *,
    upload: UploadFile,
    job_prefix: str,
    role: str,
    field_name: str,
) -> Path:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    content = await upload.read()
    if not content:
        raise HTTPException(status_code=400, detail=f"{field_name} es obligatorio y no puede estar vacío.")
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"Archivo demasiado grande. Máximo: {settings.max_upload_mb} MB por archivo.",
        )
    _assert_pdf_upload(
        filename=upload.filename or "",
        content_type=upload.content_type,
        content=content,
        field_name=field_name,
    )

    file_name = upload.filename or f"{role}.pdf"
    out_path = Path(settings.uploads_dir) / f"{job_prefix}_{role}_{_safe_name(file_name)}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(content)
    return out_path


def _safe_name(name: str) -> str:
    return Path(name).name.replace("/", "_").replace("\\", "_")


def _extract_http_error_message(detail: object) -> str:
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str):
            return message
    return str(detail)


def _render_compare_page(
    *,
    result: dict | None = None,
    error: str | None = None,
    output_excel_href: str = "",
    use_ai: bool = False,
) -> str:
    checked = "checked" if use_ai else ""
    result_html = ""
    if result:
        supplier = html.escape(str(result.get("supplier") or "-"))
        result_html = f"""
        <section class="card ok">
          <h2>Resultado</h2>
          <ul>
            <li><strong>Proveedor:</strong> {supplier}</li>
            <li><strong>Líneas origen:</strong> {result.get("lines_origin", 0)}</li>
            <li><strong>Líneas destino:</strong> {result.get("lines_target", 0)}</li>
            <li><strong>Líneas OK:</strong> {result.get("lines_ok", 0)}</li>
            <li><strong>Incidencias:</strong> {result.get("incidents_total", 0)}</li>
            <li><strong>Estado global:</strong> {html.escape(str(result.get("overall_status", "-")))}</li>
          </ul>
          <p><a href="{html.escape(output_excel_href)}" target="_blank" rel="noopener">Descargar Excel</a></p>
        </section>
        """
    if error:
        result_html = f"""
        <section class="card error">
          <h2>Error</h2>
          <p>{html.escape(error)}</p>
        </section>
        """

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Aquacenter Compare UI</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; background: #f4f7fb; margin: 0; padding: 24px; color: #0f172a; }}
    .wrap {{ max-width: 900px; margin: 0 auto; }}
    .card {{ background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08); margin-bottom: 16px; }}
    .ok {{ border-left: 6px solid #15803d; }}
    .error {{ border-left: 6px solid #b91c1c; }}
    h1, h2 {{ margin: 0 0 12px; }}
    form {{ display: grid; gap: 12px; }}
    label {{ display: grid; gap: 6px; font-weight: 600; }}
    input[type=file], select {{ padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px; background: #fff; }}
    .row {{ display: flex; gap: 10px; align-items: center; }}
    button {{ border: 0; background: #0f4c81; color: #fff; padding: 10px 16px; border-radius: 8px; cursor: pointer; font-weight: 600; }}
    ul {{ margin: 0; padding-left: 18px; }}
    a {{ color: #0b65c2; }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="card">
      <h1>Comparación manual de PDFs</h1>
      <form action="/ui/compare" method="post" enctype="multipart/form-data">
        <label>PDF pedido (origin_file)
          <input type="file" name="origin_file" accept="application/pdf,.pdf" required>
        </label>
        <label>PDF confirmación (target_file)
          <input type="file" name="target_file" accept="application/pdf,.pdf" required>
        </label>
        <label>Módulo
          <select name="module">
            <option value="confirmacion_pedidos">confirmacion_pedidos</option>
          </select>
        </label>
        <label class="row"><input type="checkbox" name="use_ai" value="true" {checked}> Usar fallback IA</label>
        <button type="submit">Comparar</button>
      </form>
    </section>
    {result_html}
  </main>
</body>
</html>"""


def _batch_overall_status(result: dict | None) -> str:
    if not result:
        return "failed"
    with_incidents = int(result.get("comparisons_with_incidents", 0) or 0)
    unmatched = len(result.get("unmatched_documents") or [])
    if with_incidents > 0 or unmatched > 0:
        return "with_incidents"
    return "ok"


def _render_batch_page(
    *,
    result: dict | None = None,
    error: str | None = None,
    batch_excel_href: str = "",
    overall_status: str = "",
    use_ai: bool = False,
) -> str:
    checked = "checked" if use_ai else ""
    result_html = ""
    if result:
        pairs = result.get("pairs") or []
        rows_html = []
        for pair in pairs:
            excel_href = ensure_relative_path(str(pair.get("output_excel") or ""))
            rows_html.append(
                f"""
                <tr>
                  <td>{html.escape(str(pair.get("origin_file") or "-"))}</td>
                  <td>{html.escape(str(pair.get("target_file") or "-"))}</td>
                  <td>{html.escape(str(pair.get("supplier") or "-"))}</td>
                  <td>{pair.get("lines_origin", 0)}</td>
                  <td>{pair.get("lines_target", 0)}</td>
                  <td>{pair.get("lines_ok", 0)}</td>
                  <td>{pair.get("incidents_total", 0)}</td>
                  <td>{html.escape(str(pair.get("overall_status") or "-"))}</td>
                  <td><a href="{html.escape(excel_href)}" target="_blank" rel="noopener">Descargar</a></td>
                </tr>
                """
            )
        table_html = (
            """
            <table>
              <thead>
                <tr>
                  <th>Pedido</th>
                  <th>Confirmación</th>
                  <th>Proveedor</th>
                  <th>Líneas origen</th>
                  <th>Líneas destino</th>
                  <th>Líneas OK</th>
                  <th>Incidencias</th>
                  <th>Estado</th>
                  <th>Excel</th>
                </tr>
              </thead>
              <tbody>
            """
            + "".join(rows_html)
            + """
              </tbody>
            </table>
            """
        )
        result_html = f"""
        <section class="card ok">
          <h2>Resumen de lote</h2>
          <ul>
            <li><strong>Documentos procesados:</strong> {result.get("documents_total", 0)}</li>
            <li><strong>Parejas detectadas:</strong> {result.get("pairs_detected", 0)}</li>
            <li><strong>Comparaciones OK:</strong> {result.get("comparisons_ok", 0)}</li>
            <li><strong>Comparaciones con incidencias:</strong> {result.get("comparisons_with_incidents", 0)}</li>
            <li><strong>Documentos no emparejados:</strong> {len(result.get("unmatched_documents") or [])}</li>
            <li><strong>Incidencias totales:</strong> {result.get("incidents_total", 0)}</li>
            <li><strong>Estado global del lote:</strong> {html.escape(overall_status or _batch_overall_status(result))}</li>
          </ul>
          <p><a href="{html.escape(batch_excel_href)}" target="_blank" rel="noopener">Descargar Excel global</a></p>
        </section>
        <section class="card">
          <h2>Parejas detectadas</h2>
          {table_html}
        </section>
        """
    if error:
        result_html = f"""
        <section class="card error">
          <h2>Error</h2>
          <p>{html.escape(error)}</p>
        </section>
        """

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Aquacenter Batch UI</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; background: #f4f7fb; margin: 0; padding: 24px; color: #0f172a; }}
    .wrap {{ max-width: 1200px; margin: 0 auto; }}
    .card {{ background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08); margin-bottom: 16px; }}
    .ok {{ border-left: 6px solid #15803d; }}
    .error {{ border-left: 6px solid #b91c1c; }}
    h1, h2 {{ margin: 0 0 12px; }}
    form {{ display: grid; gap: 12px; }}
    label {{ display: grid; gap: 6px; font-weight: 600; }}
    input[type=file], select {{ padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px; background: #fff; }}
    .row {{ display: flex; gap: 10px; align-items: center; }}
    button {{ border: 0; background: #0f4c81; color: #fff; padding: 10px 16px; border-radius: 8px; cursor: pointer; font-weight: 600; }}
    ul {{ margin: 0; padding-left: 18px; }}
    a {{ color: #0b65c2; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border: 1px solid #e2e8f0; padding: 8px; text-align: left; font-size: 14px; }}
    th {{ background: #f8fafc; font-weight: 700; }}
    .toolbar {{ display: flex; gap: 12px; margin-bottom: 12px; }}
  </style>
</head>
<body>
  <main class="wrap">
    <div class="toolbar">
      <a href="/ui/compare">Ir a comparación manual</a>
    </div>
    <section class="card">
      <h1>Comparación masiva de PDFs</h1>
      <form action="/ui/batch" method="post" enctype="multipart/form-data">
        <label>Subir múltiples PDFs
          <input type="file" name="files" accept="application/pdf,.pdf" multiple required>
        </label>
        <label>Módulo
          <select name="module">
            <option value="confirmacion_pedidos">confirmacion_pedidos</option>
          </select>
        </label>
        <label class="row"><input type="checkbox" name="use_ai" value="true" {checked}> Usar fallback IA</label>
        <button type="submit">Procesar lote</button>
      </form>
    </section>
    {result_html}
  </main>
</body>
</html>"""


def _log(event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, ensure_ascii=False))
