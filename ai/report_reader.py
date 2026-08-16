"""
ai/report_reader.py
Turns an uploaded health-report FILE (PDF, or a photo/scan of one) into
plain text, and wraps that text into a prompt for the patient's chat
agent to summarize. Nothing here writes to the database or disk — the
file only ever exists in memory for this one request, matching how the
rest of the chatbot (ai/speech.py, ai/booking_flow.py) treats anything
that touches a real clinical document.

PDF text extraction (pypdf) is a core, no-system-dependency requirement.
Image OCR (pytesseract) is optional — it needs the Tesseract OCR *binary*
installed on the machine, not just the Python package — so this module
degrades the same way ai/embeddings.py and ai/vectorstore.py do: PDFs
with a real text layer always work; photos/scans only work if OCR is
available, and raise a clear, actionable error otherwise rather than a
raw traceback.

Summarization itself is NOT a separate LLM call — build_summary_prompt()
produces the `message` that views/chatbot_view.py hands to the normal
ai.smartcare_agent.ask() pipeline, so an uploaded report gets the same
language-matching, response formatting, and (if relevant) cross-checking
against the patient's own stored vitals via their existing tools that
any typed question gets.
"""
import io

SUPPORTED_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".webp")

# Keep the prompt sent to the LLM bounded — a multi-page discharge summary
# or lab report shouldn't blow up latency/cost. Same reasoning as
# services/chat_service.py's HISTORY_CONTEXT_LIMIT capping chat history.
_MAX_CHARS = 12000

_SUMMARY_PROMPT_TEMPLATE = (
    "The patient just uploaded a health report file ({filename}) and "
    "wants detailed information about it, not just a brief summary. "
    "Below is the raw text extracted from that file — it may contain "
    "OCR artifacts, odd spacing, or broken table formatting that didn't "
    "survive extraction; read past small glitches and reconstruct the "
    "intended meaning where it's obviously a formatting issue rather "
    "than guessing at content that truly isn't there.\n\n"
    "Respond with:\n"
    "1. A one- or two-sentence opener: what kind of report this is "
    "(e.g. lab panel, discharge summary, ECG report, prescription) and "
    "when/where it's from, if that's stated.\n"
    "2. A markdown TABLE of every measurable value or finding in the "
    "report (columns: Finding | Value | Normal range | Flag). Use the "
    "cardiac reference knowledge available to you to fill in a normal "
    "range where relevant, and mark Flag as Normal / High / Low / "
    "Abnormal accordingly — leave Normal range and Flag blank for "
    "findings that aren't a numeric/measurable value (e.g. a written "
    "impression like 'normal sinus rhythm'). If it's useful, check the "
    "patient's own stored vitals/history to note in the Flag column "
    "whether a value has changed since their last recorded reading.\n"
    "3. A short 'What this means' section in plain language, plainly "
    "noting anything flagged as abnormal — without diagnosing or "
    "telling the patient what to do about it.\n\n"
    "End by reminding them to go over this report with their doctor, "
    "especially anything flagged as abnormal. If the extracted text is "
    "too garbled or incomplete to work with confidently, say so "
    "honestly rather than guessing at missing values, and just describe "
    "what you actually can make out.\n\n"
    "--- Extracted report text ---\n{report_text}\n--- End of report text ---"
)


class ReportReadError(Exception):
    pass


def is_supported(filename: str) -> bool:
    return filename.lower().endswith(SUPPORTED_EXTENSIONS)


def extract_text(file_bytes: bytes, filename: str) -> str:
    """
    Returns extracted plain text, trimmed to _MAX_CHARS. Raises
    ReportReadError — never lets a raw parsing/OCR exception escape — for
    anything from "unsupported file type" to "no OCR engine installed" to
    "this file has no extractable text", so callers can show a friendly
    message instead of a traceback.
    """
    name = filename.lower()
    if name.endswith(".pdf"):
        text = _extract_pdf(file_bytes)
    elif name.endswith((".png", ".jpg", ".jpeg", ".webp")):
        text = _extract_image(file_bytes)
    else:
        raise ReportReadError(
            "That file type isn't supported — upload a PDF, PNG, or JPG of the report."
        )

    text = text.strip()
    if not text:
        raise ReportReadError(
            "Couldn't find any readable text in that file — try a clearer "
            "photo/scan, or a PDF with real (selectable) text rather than "
            "a scanned image with no text layer."
        )
    return text[:_MAX_CHARS]


def build_summary_prompt(filename: str, report_text: str) -> str:
    """The `message` passed to ai.smartcare_agent.ask() for an uploaded
    report. views/chatbot_view.py shows a short, clean line in the chat
    UI instead of this full wrapped prompt — see its display_text param
    on _handle_turn()."""
    return _SUMMARY_PROMPT_TEMPLATE.format(filename=filename, report_text=report_text)


def _extract_pdf(file_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ReportReadError(
            "PDF reading needs the 'pypdf' package — run `pip install pypdf` and restart the app."
        ) from e

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as e:
        raise ReportReadError(
            f"Couldn't read that PDF — it may be corrupted or password-protected. ({e})"
        ) from e

    text = "\n\n".join(p for p in pages if p.strip())
    if not text.strip():
        raise ReportReadError(
            "This PDF doesn't have any selectable text (it looks like a "
            "scanned image with no text layer) — try uploading it as a "
            "PNG/JPG photo instead so OCR can read it."
        )
    return text


def _extract_image(file_bytes: bytes) -> str:
    try:
        from PIL import Image
    except ImportError as e:
        raise ReportReadError(
            "Image reading needs the 'Pillow' package — run `pip install Pillow` and restart the app."
        ) from e
    try:
        img = Image.open(io.BytesIO(file_bytes))
    except Exception as e:
        raise ReportReadError(f"Couldn't open that image. ({e})") from e

    try:
        import pytesseract
        # Point directly at the installed binary rather than relying on
        # PATH — Windows PATH edits don't apply to a terminal/IDE that
        # was already open when Tesseract was installed, which is a
        # common cause of "tesseract is not installed or it's not in
        # your PATH" even when it genuinely is installed. Adjust this
        # path if Tesseract was installed somewhere else on this machine.
        import os
        _default_win_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.name == "nt" and os.path.exists(_default_win_path):
            pytesseract.pytesseract.tesseract_cmd = _default_win_path
    except ImportError as e:
        raise ReportReadError(
            "Reading text from a photo/scan needs OCR support — install "
            "the 'pytesseract' package AND the Tesseract OCR engine on "
            "this machine (see github.com/tesseract-ocr/tesseract), then "
            "restart the app. In the meantime, a PDF with selectable text "
            "works without OCR."
        ) from e
    try:
        return pytesseract.image_to_string(img)
    except Exception as e:
        raise ReportReadError(
            f"Couldn't run OCR on that image — confirm the Tesseract OCR "
            f"engine is installed and on your PATH. ({e})"
        ) from e