import os


def extract_from_file(file_path: str) -> dict:
    """根据文件扩展名自动选择提取方式，返回结构化字段。"""
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".docx":
        return _extract_docx(file_path)
    elif ext == ".pdf":
        return _extract_pdf(file_path)
    elif ext == ".pptx":
        return _extract_pptx(file_path)
    elif ext in (".xlsx", ".xls"):
        return _extract_xlsx(file_path)
    else:
        return {"raw_input": f"Unsupported file type: {ext}"}


def _extract_docx(file_path: str) -> dict:
    from docx import Document
    doc = Document(file_path)

    # 提取核心属性
    props = doc.core_properties
    text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

    return {
        "title":     props.title or _guess_title(text),
        "author":    props.author or "",
        "date":      str(props.created.date()) if props.created else "",
        "publisher": props.last_modified_by or "",
        "raw_text":  text[:3000],
    }


def _extract_pdf(file_path: str) -> dict:
    import pdfplumber

    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages[:5]:  # 只取前5页提取关键信息
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"

    return {
        "title":    _guess_title(text),
        "raw_text": text[:3000],
    }


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


def classify_document_type(raw_text: str) -> str:
    """Use LLM to classify the document type from its raw text.

    Returns one of: journal_article, book, book_chapter, thesis, report,
    newspaper, case, legislation, government_document, website, other.
    """
    if not raw_text or not raw_text.strip():
        return "other"

    text_sample = raw_text[:800].strip()
    prompt = f"""You are a document type classifier for legal citations.
Analyze the following text and return ONE type that best describes the document.
Only return the type string, nothing else.

Types:
- journal_article: academic journal article
- book: full book or monograph
- book_chapter: a chapter within a book
- thesis: thesis or dissertation
- report: report from an organization, NGO, or government
- newspaper: newspaper or news article
- case: court decision or judgment
- legislation: statute, act, or regulation
- government_document: official government publication (not legislation)
- website: web page, blog post, or online article
- other: none of the above

Text:
{text_sample}"""

    from llm_api.deepseek_api import ask_deepseek
    try:
        result = ask_deepseek(prompt).strip().lower()
        valid = {"journal_article", "book", "book_chapter", "thesis", "report",
                 "newspaper", "case", "legislation", "government_document", "website", "other"}
        if result in valid:
            return result
    except Exception:
        pass
    return "other"