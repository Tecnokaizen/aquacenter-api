from __future__ import annotations

from app.services.compare_mvp import compare_documents_mvp
from app.services.pdf_extractor import extract_pdf_document


def run_compare_pair(
    *,
    origin_path: str,
    target_path: str,
    job_id: str,
    module: str,
    use_ai: bool,
    output_dir: str,
) -> tuple[dict, list[str]]:
    origin_doc, origin_warnings = extract_pdf_document(origin_path, use_ai=use_ai)
    target_doc, target_warnings = extract_pdf_document(target_path, use_ai=use_ai)
    payload = compare_documents_mvp(
        origin_doc=origin_doc,
        target_doc=target_doc,
        job_id=job_id,
        module=module,
        output_dir=output_dir,
    )
    return payload, origin_warnings + target_warnings

