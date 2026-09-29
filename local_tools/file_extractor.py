import os

# ── 扫描件判定阈值：文字不足此数视为扫描件 ──
SCANNED_THRESHOLD = 50


def extract_from_file(file_path: str) -> dict:
    """根据文件扩展名自动选择提取方式，返回结构化字段。

    图片（.jpg/.jpeg/.png/.webp）→ 视觉模型提取。
    PDF 文字不足 {SCANNED_THRESHOLD} 字 → 降级为视觉模型提取（前 3 页渲染）。
    PDF 文字足够 → 现有文本管线。
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".docx":
        return _extract_docx(file_path)
    elif ext == ".pdf":
        return _extract_pdf(file_path)
    elif ext == ".pptx":
        return _extract_pptx(file_path)
    elif ext in (".xlsx", ".xls"):
        return _extract_xlsx(file_path)
    elif ext in (".jpg", ".jpeg", ".png", ".webp"):
        from llm_api.vision import extract_from_image
        return extract_from_image(file_path)
    else:
        return {"raw_input": f"Unsupported file type: {ext}"}


def head_tail(text: str, head: int, tail: int) -> str:
    """Keep the start and the end of long text.

    Source notes ("All passages are from ...", reference lists) usually sit at
    the end of a document, so a plain prefix would cut them off.
    """
    if len(text) <= head + tail:
        return text
    return text[:head].rstrip() + "\n[…]\n" + text[-tail:].lstrip()


def _extract_docx(file_path: str) -> dict:
    from docx import Document
    doc = Document(file_path)

    props = doc.core_properties
    text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

    return {
        "title":     props.title or _guess_title(text),
        "author":    props.author or "",
        "date":      str(props.created.date()) if props.created else "",
        "publisher": props.last_modified_by or "",
        "raw_text":  head_tail(text, 3000, 1500),
    }


def _extract_pdf(file_path: str) -> dict:
    import pdfplumber

    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages[:5]:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"

    # ── 无文字层降级：不足阈值 → 视觉提取 ──
    if len(text.strip()) < SCANNED_THRESHOLD:
        return _extract_pdf_scanned(file_path)

    return {
        "title":    _guess_title(text),
        "raw_text": text[:3000],
    }


def _extract_pdf_scanned(file_path: str) -> dict:
    """Render first 3 pages of a scanned PDF as images and run vision extraction."""
    import fitz

    image_paths = []
    try:
        doc = fitz.open(file_path)
        try:
            for i in range(min(3, len(doc))):
                page = doc.load_page(i)
                # Cap rendered pixels: a crafted oversized page at dpi=200
                # would otherwise allocate a giant pixmap.
                _MAX_PIXELS = 16_000_000  # ≈ 4000×4000
                w, h = page.rect.width, page.rect.height
                scale = 200 / 72
                if w * h * scale * scale > _MAX_PIXELS:
                    scale = (_MAX_PIXELS / (w * h)) ** 0.5
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
                # Write to a temporary PNG file
                tmp_path = f"{file_path}.page{i}.png"
                pix.save(tmp_path)
                image_paths.append(tmp_path)
        finally:
            doc.close()

        from llm_api.vision import extract_from_images
        result = extract_from_images(image_paths)
        return result
    finally:
        # Cleanup temporary files
        for p in image_paths:
            try:
                os.unlink(p)
            except Exception:
                pass


def _extract_pptx(file_path: str) -> dict:
    from pptx import Presentation

    prs = Presentation(file_path)
    props = prs.core_properties
    text_parts = []

    for slide in prs.slides:
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                text_parts.append(shape.text.strip())

    text = "\n".join(text_parts)

    return {
        "title":    props.title or _guess_title(text),
        "author":   props.author or "",
        "date":     str(props.created.date()) if props.created else "",
        "raw_text": text[:3000],
    }


def _extract_xlsx(file_path: str) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    props = wb.properties
    text_parts = []

    for sheet in wb.worksheets:
        for row in sheet.iter_rows(max_row=50, values_only=True):
            row_text = " ".join([str(c) for c in row if c is not None])
            if row_text.strip():
                text_parts.append(row_text)

    text = "\n".join(text_parts)

    return {
        "title":    props.title or _guess_title(text),
        "author":   props.creator or "",
        "date":     str(props.created.date()) if props.created else "",
        "raw_text": text[:3000],
    }


def _guess_title(text: str) -> str:
    """用第一行非空文字作为标题猜测。"""
    for line in text.splitlines():
        line = line.strip()
        if len(line) > 5:
            return line[:100]
    return ""

