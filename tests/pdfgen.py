"""Tiny PDFs for tests: one text block per page, optional document metadata."""

import pymupdf


def make_pdf(pages: list[str], metadata: dict | None = None) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(pymupdf.Rect(50, 50, 545, 800), text, fontsize=10)
    if metadata:
        doc.set_metadata(metadata)
    data = doc.tobytes()
    doc.close()
    return data
