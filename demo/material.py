"""Supports de cours deposes par l'eleve : texte, markdown, PDF, image.

Le texte est renvoye au navigateur, qui le garde et le renvoie a chaque tour :
le serveur ne conserve rien (decision D2). Une image devient une data URL,
transmise au modele vision.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

TEXT_EXT = {".txt", ".md", ".markdown", ".tex", ".csv"}
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}
MAX_IMAGE_BYTES = 6 * 1024 * 1024


def _pdf_text(data: bytes) -> str:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise ValueError("Lecture PDF : installez pypdfium2 (pip install pypdfium2)") from exc
    pdf = pdfium.PdfDocument(io.BytesIO(data))
    pages = []
    for i in range(len(pdf)):
        page = pdf[i]
        pages.append(page.get_textpage().get_text_range())
    text = "\n\n".join(pages).strip()
    if not text:
        raise ValueError("PDF sans texte (scan ?) : envoyez-le plutot en photo.")
    return text


def extract(filename: str, data: bytes) -> dict:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXT:
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Image trop lourde (6 Mo max).")
        b64 = base64.b64encode(data).decode()
        return {"kind": "image", "name": filename, "dataUrl": f"data:{IMAGE_EXT[ext]};base64,{b64}"}
    if ext == ".pdf":
        return {"kind": "text", "name": filename, "text": _pdf_text(data)}
    if ext in TEXT_EXT or not ext:
        return {"kind": "text", "name": filename, "text": data.decode("utf-8", "replace")}
    raise ValueError(f"Format {ext} non pris en charge (texte, markdown, PDF ou image).")
