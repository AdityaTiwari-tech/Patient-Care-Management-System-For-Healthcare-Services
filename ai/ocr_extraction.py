"""
ai/ocr_extraction.py
PDF/image -> text extraction for the standalone OCR Extractor app
(ocr_portal_app.py), using PaddleOCR for images and scanned PDF pages.

Mirrors ai/report_reader.py's philosophy on purpose (same _MAX_CHARS cap,
same "never let a raw library exception escape" contract), but swaps
pytesseract for PaddleOCR, and PDFs additionally fall back to
page-rasterize-then-OCR when there's no real text layer, instead of
telling the user to re-upload as an image.

Nothing here writes to disk or the database — the file only ever exists
in memory for one request, same as report_reader.py.
"""
import io
from functools import lru_cache

SUPPORTED_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif")

# Same reasoning as report_reader.py's _MAX_CHARS: bound what gets sent
# to the LLM for summarization so a long scanned document doesn't blow
# up latency/cost.
_MAX_CHARS = 12000

_SUMMARY_PROMPT_TEMPLATE = (
    "A health-related document ({filename}) was scanned with OCR and the "
    "following text was extracted below. It may contain OCR artifacts, "
    "odd spacing, or broken table formatting that didn't survive "
    "extraction — read past small glitches and reconstruct the intended "
    "meaning where it's obviously a formatting issue rather than "
    "guessing at content that truly isn't there.\n\n"
    "Write a concise, clearly organized summary for the doctor who will "
    "receive it: what kind of document this is, and the key facts, "
    "values, or findings it contains. Do not diagnose or recommend "
    "treatment. If the extracted text is too garbled or incomplete to "
    "summarize confidently, say so honestly rather than guessing at "
    "missing values.\n\n"
    "--- Extracted text ---\n{extracted_text}\n--- End of extracted text ---"
)


class OCRExtractionError(Exception):
    pass


@lru_cache(maxsize=1)
def _get_ocr_engine():
    """Cached PaddleOCR instance. Raising here (rather than returning
    None) is intentional — callers wrap this in a try/except that turns
    it into a friendly OCRExtractionError instead of a traceback."""
    from paddleocr import PaddleOCR
    return PaddleOCR(use_angle_cls=True, lang="en", show_log=False)


def is_supported(filename: str) -> bool:
    return filename.lower().endswith(SUPPORTED_EXTENSIONS)


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Returns extracted plain text, trimmed to _MAX_CHARS. Raises
    OCRExtractionError for anything from "unsupported file type" to
    "PaddleOCR isn't installed" to "nothing readable in this file"."""
    name = filename.lower()
    if name.endswith(".pdf"):
        text = _extract_pdf(file_bytes)
    elif name.endswith(SUPPORTED_EXTENSIONS[1:]):
        text = _extract_image(file_bytes)
    else:
        raise OCRExtractionError(
            "That file type isn't supported — upload a PDF, PNG, JPG, BMP, or TIFF."
        )

    text = text.strip()
    if not text:
        raise OCRExtractionError(
            "Couldn't find any readable text in that file — try a clearer "
            "photo/scan, or a higher-resolution copy."
        )
    return text[:_MAX_CHARS]


def build_summary_prompt(filename: str, extracted_text: str) -> str:
    return _SUMMARY_PROMPT_TEMPLATE.format(filename=filename, extracted_text=extracted_text)


def summarize(filename: str, extracted_text: str) -> str:
    """Summarizes via the same Groq LLM the main SmartCare app uses
    (ai/llm.py) — reused as-is so both apps stay on one model/config.
    Falls back to a plain truncation if GROQ_API_KEY isn't set, so the
    OCR app still works end-to-end without an LLM configured."""
    from ai.llm import get_llm, is_configured

    if not is_configured():
        snippet = extracted_text.strip().replace("\n", " ")
        return snippet[:600] + ("…" if len(snippet) > 600 else "")

    llm = get_llm()
    prompt = build_summary_prompt(filename, extracted_text)
    reply = llm.invoke(prompt)
    return reply.content.strip()


def _extract_pdf(file_bytes: bytes) -> str:
    # 1. Try a real text layer first — cheap and exact, no OCR needed.
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(t for t in pages_text if t.strip())
        if text.strip():
            return text
    except ImportError as e:
        raise OCRExtractionError(
            "PDF reading needs the 'pypdf' package — run `pip install pypdf`."
        ) from e
    except Exception:
        pass  # fall through to OCR — likely a scanned/image-only PDF

    # 2. No text layer — rasterize each page and OCR it.
    try:
        from pdf2image import convert_from_bytes
    except ImportError as e:
        raise OCRExtractionError(
            "This PDF has no selectable text, so it needs OCR — install "
            "'pdf2image' (`pip install pdf2image`) plus the poppler "
            "binary (e.g. `apt install poppler-utils` / `brew install poppler`)."
        ) from e
    try:
        images = convert_from_bytes(file_bytes)
    except Exception as e:
        raise OCRExtractionError(f"Couldn't render this PDF for OCR. ({e})") from e

    ocr = _get_paddle_or_raise()
    import numpy as np

    all_pages = []
    for img in images:
        result = ocr.ocr(np.array(img.convert("RGB")), cls=True)
        lines = [line[1][0] for block in (result or []) for line in block]
        all_pages.append("\n".join(lines))
    return "\n\n".join(t for t in all_pages if t.strip())


def _extract_image(file_bytes: bytes) -> str:
    try:
        import numpy as np
        from PIL import Image
    except ImportError as e:
        raise OCRExtractionError(
            "Image reading needs 'Pillow' and 'numpy' — run `pip install Pillow numpy`."
        ) from e
    try:
        img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    except Exception as e:
        raise OCRExtractionError(f"Couldn't open that image. ({e})") from e

    ocr = _get_paddle_or_raise()
    try:
        result = ocr.ocr(np.array(img), cls=True)
    except Exception as e:
        raise OCRExtractionError(f"OCR failed on this image. ({e})") from e

    lines = [line[1][0] for block in (result or []) for line in block]
    return "\n".join(lines)


def _get_paddle_or_raise():
    try:
        return _get_ocr_engine()
    except ImportError as e:
        raise OCRExtractionError(
            "OCR needs the 'paddleocr' and 'paddlepaddle' packages — run "
            "`pip install paddlepaddle paddleocr` and restart the app."
        ) from e
    except Exception as e:
        raise OCRExtractionError(f"Couldn't start the OCR engine. ({e})") from e