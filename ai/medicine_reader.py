"""
ai/medicine_reader.py
Turns an uploaded photo of a medicine — a box/strip/label with printed
text, OR a bare pill/tablet with no text at all — into a description the
patient's chat agent can explain from.

Two extraction paths, tried in order:
  1. OCR (reuses ai/report_reader.py's own image path as-is) — cheap and
     exact for anything with printed text: box, strip, label.
  2. Vision LLM (ai/llm.py's get_vision_llm()) — used whenever OCR finds
     little or no text, i.e. a bare pill/tablet with nothing printed on
     it to read. Sends the image directly to a multimodal Groq model and
     asks it to describe shape/color/imprint, naming the medicine only
     if it's confident enough to.

Nothing here writes to the database or disk — the file only ever exists
in memory for this one request, same contract as report_reader.py.
"""
import base64

from ai.report_reader import _extract_image as _ocr_image, ReportReadError

_MAX_CHARS = 4000

# Below this many OCR'd characters, treat the image as "no readable text"
# (stray specks/noise pytesseract sometimes reports on a blank pill) and
# fall back to the vision model instead of feeding the agent a near-empty
# OCR string.
_OCR_MIN_USEFUL_CHARS = 12

_SUMMARY_PROMPT_TEMPLATE = (
    "The patient uploaded a photo of a medicine ({filename}) and wants "
    "thorough, detailed information about it — more than a brief "
    "overview. Below is what was extracted from the image.\n\n"
    "{extracted_section}\n\n"
    "First, try to confidently identify the medicine (by name, using "
    "the extracted text and the pharmacy catalog tools available to "
    "you). If you can identify it with reasonable confidence, give a "
    "thorough profile combining what's actually printed on the label "
    "with your general pharmacological knowledge of that specific "
    "medicine — clearly separated so the patient always knows which is "
    "which:\n\n"
    "## From the label\n"
    "A markdown TABLE (Field | Details) of only what's actually printed "
    "and legible — omit any row that isn't there, never fill one in "
    "from general knowledge here:\n"
    "  - Name, form & strength\n"
    "  - Active ingredients / composition\n"
    "  - Pack size/quantity, manufacturer, batch number, MRP\n"
    "  - Any dosage/frequency/administration instructions actually printed\n"
    "  - Any warnings or storage instructions actually printed\n\n"
    "## What this medicine is (general information)\n"
    "Only include this section if you identified the medicine with "
    "reasonable confidence — otherwise say you couldn't confirm it "
    "clearly enough and stop here. Cover, in real detail, not one-liners:\n"
    "  - Drug class and how it works, in plain language\n"
    "  - What it's used for — the main condition(s) it treats, and any "
    "well-known secondary uses\n"
    "  - Common side effects\n"
    "  - Who should typically avoid it / notable contraindications\n"
    "  - Notable drug or food interactions worth being aware of\n\n"
    "Do not give a personalized dosage recommendation, and do not tell "
    "the patient to start, stop, or change anything — this is general "
    "and label information, not a prescription. End by reminding them "
    "to confirm anything here with their doctor or pharmacist before "
    "acting on it, since a photo — especially of a bare pill with no "
    "packaging — can be misidentified, and general drug information "
    "doesn't account for their personal health history."
)


class MedicineReadError(Exception):
    pass


def is_supported(filename: str) -> bool:
    return filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))


def identify(file_bytes: bytes, filename: str) -> str:
    """Returns a text description to feed into build_summary_prompt():
    OCR'd label text if there's enough of it, otherwise a vision model's
    description of the pill/tablet itself. Raises MedicineReadError only
    if BOTH paths are unavailable/fail — never lets a raw OCR/vision
    exception escape, same contract as report_reader.py."""
    ocr_text = ""
    try:
        ocr_text = _ocr_image(file_bytes).strip()
    except ReportReadError:
        pass  # OCR unavailable or failed — fall through to vision

    if len(ocr_text) >= _OCR_MIN_USEFUL_CHARS:
        return f"Text read from the packaging/label:\n{ocr_text[:_MAX_CHARS]}"

    try:
        description = _describe_with_vision(file_bytes)
        return (
            "No readable label text was found — visual description of "
            f"the pill/tablet itself:\n{description}"
        )
    except Exception as e:
        if ocr_text:
            # A little OCR text, just under the "useful" threshold —
            # still better than nothing if vision isn't available.
            return f"Text read from the packaging/label:\n{ocr_text[:_MAX_CHARS]}"
        raise MedicineReadError(
            "Couldn't read any text off this image, and the vision model "
            f"needed to describe it directly isn't available. ({e})"
        ) from e


def build_summary_prompt(filename: str, extracted_section: str) -> str:
    """The `message` passed to ai.smartcare_agent.ask() — same handoff
    pattern as ai/report_reader.py's build_summary_prompt()."""
    return _SUMMARY_PROMPT_TEMPLATE.format(filename=filename, extracted_section=extracted_section)


def _describe_with_vision(file_bytes: bytes) -> str:
    from ai.llm import get_vision_llm, is_configured
    if not is_configured():
        raise MedicineReadError("Vision identification needs GROQ_API_KEY set in .env.")

    b64 = base64.b64encode(file_bytes).decode("ascii")
    llm = get_vision_llm()
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": (
                "Describe this pill/tablet/medicine precisely: shape, "
                "color, any imprinted text or numbers, relative size if "
                "visible, and any packaging. If the imprint or packaging "
                "lets you confidently name the medicine, say so; "
                "otherwise just describe what you see — do not guess a "
                "name you're not reasonably confident about."
            )},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    }
    reply = llm.invoke([message])
    return reply.content.strip()