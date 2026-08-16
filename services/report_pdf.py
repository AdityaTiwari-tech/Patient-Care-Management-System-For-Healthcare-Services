"""
services/report_pdf.py
Renders a report dict (from services/report_service.get_report()) as a
standalone HTML document, and converts that HTML to PDF bytes via
xhtml2pdf — a pure-Python HTML-to-PDF renderer (no wkhtmltopdf/Chromium
binary to install, which matters on a Windows dev machine).

xhtml2pdf's rendering engine (built on ReportLab) only understands a
subset of CSS — no flexbox/grid, no @import'd Google Fonts, no CSS
variables. This file's HTML/CSS is written to that subset deliberately,
using tables for layout and only the standard PDF-safe font families
(Helvetica/Times), rather than reusing the app's Fraunces/Inter styling
from assets/styles.css. This is a report meant to be printed/archived,
not a themed UI screen.

Nothing in this file imports streamlit or touches the database — it's a
pure function: dict in, string/bytes out. views/*.py decide when to call
it and what to do with the result (preview inline, or st.download_button).
"""
from datetime import datetime

from core.config import settings

_TABLE_HEADERS = ["Medicine", "Dosage", "Frequency", "Duration", "Quantity", "Instructions"]


def _doctor_label(name) -> str:
    """'Dr. {name}', without double-prefixing when the stored name already
    starts with 'Dr.' (as this app's seed_data.py doctors do — their
    User.full_name is stored as "Dr. Arjun Mehta", not "Arjun Mehta").
    Used everywhere this template shows a doctor's name, so a report
    never reads "Dr. Dr. Arjun Mehta" regardless of which convention the
    underlying name follows."""
    text = (name or "").strip()
    if text[:3].lower() in ("dr.", "dr "):
        return text
    return f"Dr. {text}" if text else "Dr. —"


def render_report_html(report: dict) -> str:
    items_rows = "".join(
        f"""<tr>
            <td>{_esc(it['medicine_name'])}</td>
            <td>{_esc(it['dosage']) or '&mdash;'}</td>
            <td>{_esc(it['frequency']) or '&mdash;'}</td>
            <td>{_esc(it['duration']) or '&mdash;'}</td>
            <td>{it['quantity']}</td>
            <td>{_esc(it['instructions']) or '&mdash;'}</td>
        </tr>"""
        for it in report["items"]
    )
    header_cells = "".join(f"<th>{h}</th>" for h in _TABLE_HEADERS)

    vitals_section = _vitals_html(report.get("vitals"))
    advice_section = (
        f'<div class="section"><h3>Advice for the patient</h3><p>{_esc(report["advice_note"])}</p></div>'
        if report.get("advice_note") else ""
    )
    diagnosis_line = (
        f'<p><strong>Diagnosis:</strong> {_esc(report["diagnosis"])}</p>'
        if report.get("diagnosis") else ""
    )

    generated_on = datetime.now().strftime("%d %b %Y, %I:%M %p")
    created_on = report["created_at"].strftime("%d %b %Y, %I:%M %p")

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{ font-family: Helvetica, Arial, sans-serif; color: #23302D; font-size: 11pt; margin: 24px; }}
    .header {{ text-align: center; border-bottom: 2px solid #0E3B36; padding-bottom: 10px; margin-bottom: 16px; }}
    .header h1 {{ color: #0E3B36; margin: 0 0 4px 0; font-size: 20pt; }}
    .header .badge {{ display: inline-block; width: 13px; height: 13px; background-color: #E1614A; margin-right: 8px; }}
    .header .subtitle {{ color: #5B6864; font-size: 10pt; margin: 0; }}
    .meta-table {{ width: 100%; margin-bottom: 14px; }}
    .meta-table td {{ padding: 2px 0; font-size: 10pt; vertical-align: top; }}
    .meta-table .label {{ color: #5B6864; width: 110px; }}
    .section {{ margin-bottom: 14px; }}
    .section h3 {{ color: #0E3B36; font-size: 12pt; margin: 0 0 6px 0; border-bottom: 1px solid #E3E0D6; padding-bottom: 3px; }}
    table.meds {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
    table.meds th {{ background: #EAF2EF; color: #0E3B36; text-align: left; padding: 6px 8px; font-size: 9.5pt; border: 1px solid #E3E0D6; }}
    table.meds td {{ padding: 6px 8px; font-size: 9.5pt; border: 1px solid #E3E0D6; }}
    table.vitals {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
    table.vitals td {{ padding: 5px 8px; font-size: 9.5pt; border: 1px solid #E3E0D6; }}
    table.vitals td.label {{ color: #5B6864; width: 45%; }}
    .footer {{ margin-top: 28px; border-top: 1px solid #E3E0D6; padding-top: 10px; font-size: 9.5pt; color: #5B6864; }}
    .signature {{ margin-top: 22px; }}
    .signature .name {{ font-weight: bold; color: #0E3B36; font-size: 11pt; }}
</style>
</head>
<body>
    <div class="header">
        <h1><span class="badge"></span>Patient Health Report</h1>
        <p class="subtitle">{_esc(settings.APP_NAME)} &middot; Generated {generated_on}</p>
    </div>

    <table class="meta-table">
        <tr><td class="label">Patient</td><td>{_esc(report['patient_name'])}</td>
            <td class="label">Report date</td><td>{created_on}</td></tr>
        <tr><td class="label">Attending doctor</td><td>{_esc(_doctor_label(report['doctor_name']))}</td>
            <td class="label">Specialty</td><td>{_esc(report['doctor_specialty'])}</td></tr>
    </table>

    <div class="section">
        <h3>Diagnosis</h3>
        {diagnosis_line or '<p>&mdash;</p>'}
    </div>

    {vitals_section}

    <div class="section">
        <h3>Prescribed medicines</h3>
        <table class="meds">
            <tr>{header_cells}</tr>
            {items_rows or '<tr><td colspan="6">No medicines on this report.</td></tr>'}
        </table>
    </div>

    {advice_section}

    <div class="footer">
        <div class="signature">
            Sincerely,<br>
            <span class="name">{_esc(_doctor_label(report['doctor_name']))}</span><br>
            {_esc(report['doctor_specialty'])}<br>
            {_esc(settings.APP_NAME)}
        </div>
        <p style="margin-top:14px;">This is a system-generated report from {_esc(settings.APP_NAME)}. For questions about this report, contact your doctor through the portal.</p>
    </div>
</body>
</html>"""


def _vitals_html(vitals: dict) -> str:
    if not vitals:
        return ""
    rows = [
        ("Heart rate", f"{vitals['heart_rate']} bpm" if vitals["heart_rate"] else None),
        ("Blood pressure", vitals["blood_pressure"]),
        ("SpO<sub>2</sub>", f"{vitals['pulse_oximetry']}%" if vitals["pulse_oximetry"] else None),
        ("Ejection fraction", f"{vitals['ejection_fraction']}%" if vitals["ejection_fraction"] else None),
        ("ECG note", vitals["ecg_note"]),
    ]
    vitals_rows = "".join(
        f'<tr><td class="label">{label}</td><td>{_esc(value) if value else "&mdash;"}</td></tr>'
        for label, value in rows
    )
    notes_html = (
        f'<p style="margin-top:8px;"><strong>Clinical notes:</strong> {_esc(vitals["notes"])}</p>'
        if vitals["notes"] else ""
    )
    return f"""<div class="section">
        <h3>Clinical notes &amp; vitals</h3>
        <table class="vitals">{vitals_rows}</table>
        {notes_html}
    </div>"""


def _esc(value) -> str:
    """Minimal HTML escaping for user-entered text dropped into the
    template above (medicine notes, diagnosis, advice, etc.)."""
    if value is None:
        return ""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def render_report_pdf(report: dict) -> bytes:
    """Converts render_report_html(report) to PDF bytes via xhtml2pdf.
    Raises RuntimeError (not the raw xhtml2pdf exception) on failure, so
    callers can show a friendly message instead of a traceback."""
    try:
        from xhtml2pdf import pisa
    except ImportError as e:
        raise RuntimeError(
            "PDF export needs the 'xhtml2pdf' package — run `pip install xhtml2pdf` and restart the app."
        ) from e

    import io

    html = render_report_html(report)
    buffer = io.BytesIO()
    result = pisa.CreatePDF(html, dest=buffer)
    if result.err:
        raise RuntimeError("Couldn't generate the PDF for this report.")
    return buffer.getvalue()