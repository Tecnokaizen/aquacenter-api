from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any


def extract_email_document(path: str) -> dict[str, Any]:
    """
    Extracción básica para .eml en MVP.
    """
    p = Path(path)
    raw = p.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(raw)

    attachments: list[dict[str, Any]] = []
    for part in msg.iter_attachments():
        attachments.append(
            {
                "filename": part.get_filename(),
                "content_type": part.get_content_type(),
                "size": len(part.get_payload(decode=True) or b""),
            }
        )

    return {
        "tipo": "email",
        "cabecera": {
            "subject": msg.get("subject"),
            "from": msg.get("from"),
            "to": msg.get("to"),
            "date": msg.get("date"),
            "attachments_count": len(attachments),
        },
        "lineas": attachments,
    }

