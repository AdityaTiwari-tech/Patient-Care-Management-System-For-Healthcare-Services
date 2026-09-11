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
import os

from core.config import settings

# --- Script fonts for non-Latin report languages --------------------
# xhtml2pdf's built-in "PDF-safe" fonts (Helvetica/Times) are Latin-only
# — this is what caused every non-English report to render its labels
# as tofu boxes (□□□) instead of real glyphs; Helvetica has no Devanagari,
# Tamil, Bengali, etc. glyphs to fall back to. Fix: embed a real Unicode
# font per script via @font-face, pointing at a bundled Noto Sans TTF
# (assets/fonts/) rather than relying on whatever fonts happen to be
# installed on the machine running the app.
#
# These are Google's Noto Sans *variable* fonts (single file covers the
# whole weight range) — reportlab (xhtml2pdf's PDF engine) loads them
# fine at their default instance; there's no need for separate static
# weight files here since the report template only ever uses one weight
# per run.
#
# Noto's script-specific fonts also always include full Latin coverage,
# so English medicine names / ₹ prices embedded in a Hindi (etc.) report
# render correctly from the SAME font — no separate fallback font needed
# for the mixed-script case that's the norm here (drug names are almost
# always in Latin script regardless of report language).
_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")

LANGUAGE_FONTS = {
    "Hindi": "NotoSans_devanagari.ttf",
    "Marathi": "NotoSans_devanagari.ttf",
    "Tamil": "NotoSans_tamil.ttf",
    "Telugu": "NotoSans_telugu.ttf",
    "Bengali": "NotoSans_bengali.ttf",
    "Gujarati": "NotoSans_gujarati.ttf",
    "Kannada": "NotoSans_kannada.ttf",
    "Malayalam": "NotoSans_malayalam.ttf",
    "Punjabi": "NotoSans_gurmukhi.ttf",
    "Urdu": "NotoSans_arabic.ttf",
}

# Arabic-script text (Urdu) needs contextual letter-joining and
# right-to-left reordering before it reaches reportlab — reportlab draws
# glyphs in the order/form it's given and does NOT do OpenType shaping,
# so raw Urdu text renders as disconnected, wrongly-ordered letterforms
# (confirmed while building this: each letter shows in its isolated
# form, reading left-to-right instead of joined and right-to-left).
# arabic_reshaper + python-bidi fix both problems before rendering.
_SHAPING_REQUIRED_LANGUAGES = {"Urdu"}


def _font_family_name(language: str) -> str:
    """Unique CSS font-family name PER SCRIPT, derived from the font
    filename (e.g. "ReportScript_devanagari"). This must NOT be a single
    shared name across all languages: xhtml2pdf/reportlab registers fonts
    in a GLOBAL, process-lifetime registry keyed by font-family name —
    reusing one name for every script meant that whichever language's
    font got registered FIRST in the running app stuck around, and every
    OTHER language silently rendered with that first font instead of its
    own (confirmed by reproducing it directly: render a Hindi report,
    then an Urdu report, in the same process — the Urdu one came out in
    the Devanagari font, which has no Arabic glyphs, hence tofu boxes).
    A per-script name makes each one its own registry entry, so they
    can't collide regardless of render order within the same process."""
    fname = LANGUAGE_FONTS.get(language)
    if not fname:
        return ""
    return "ReportScript_" + fname.rsplit(".", 1)[0]


def _font_face_css(language: str) -> str:
    """@font-face rule embedding the right script font for this language,
    or "" for English/unlisted languages (falls through to the template's
    normal Helvetica/Times stack — no change in behavior there)."""
    fname = LANGUAGE_FONTS.get(language)
    if not fname:
        return ""
    path = os.path.join(_FONT_DIR, fname).replace("\\", "/")
    family = _font_family_name(language)
    return f'@font-face {{ font-family: "{family}"; src: url("{path}"); }}\n'


def _body_font_family(language: str) -> str:
    family = _font_family_name(language)
    if family:
        return f'"{family}", Helvetica, Arial, sans-serif'
    return "Helvetica, Arial, sans-serif"


def _shape(text: str, language: str) -> str:
    """Applies Arabic contextual shaping + bidi reordering for Urdu; a
    no-op passthrough for every other language. Call this on each
    user-visible text FRAGMENT individually (a label, a name, a note) —
    not on the assembled HTML string — since running the bidi algorithm
    over raw HTML would reorder the markup itself, not just the text.
    Degrades to unshaped text (still using the Arabic font, just with
    disconnected letterforms) rather than raising, if the optional
    arabic_reshaper/python-bidi packages aren't installed."""
    if not text or language not in _SHAPING_REQUIRED_LANGUAGES:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except ImportError:
        return text

# UI CHROME translations only — section headers, table column names,
# field labels. CLINICAL DATA (medicine_name, dosage, frequency,
# duration, instructions, diagnosis text, advice_note text, numeric
# vitals) is never translated and always rendered exactly as stored,
# regardless of `language` — see FUTURE_ROADMAP.md item 3 for why: a
# mistranslated dosage or drug name is a patient-safety issue, not a UX
# bug, so the boundary here is deliberate and should not be loosened.
#
# Covers the original 11 languages from ai/languages.py. The 12
# languages added alongside this feature (Nepali, Assamese, Sanskrit,
# Sindhi, Odia, Konkani, Maithili, Dogri, Kashmiri, Manipuri, Bodo,
# Santali) aren't included yet — extend REPORT_LABELS with the same key
# set per language. Have a native/clinical speaker review any new
# language's labels before shipping it on a real patient-facing report;
# these are a solid draft, not a substitute for that review.
REPORT_LABELS = {
    "English": {
        "title": "Patient Health Report", "generated": "Generated",
        "patient": "Patient", "report_date": "Report date",
        "attending_doctor": "Attending doctor", "specialty": "Specialty",
        "diagnosis": "Diagnosis", "prescribed_medicines": "Prescribed medicines",
        "no_medicines": "No medicines on this report.",
        "table_headers": ["Medicine", "Dosage", "Frequency", "Duration", "Quantity", "Instructions"],
        "advice_for_patient": "Advice for the patient",
        "clinical_vitals": "Clinical notes & vitals",
        "heart_rate": "Heart rate", "blood_pressure": "Blood pressure",
        "spo2": "SpO<sub>2</sub>", "ejection_fraction": "Ejection fraction",
        "ecg_note": "ECG note", "clinical_notes_label": "Clinical notes:",
        "sincerely": "Sincerely,",
        "footer_note": "This is a system-generated report from {app}. For questions about this report, contact your doctor through the portal.",
    },
    "Hindi": {
        "title": "रोगी स्वास्थ्य रिपोर्ट", "generated": "बनाया गया",
        "patient": "रोगी", "report_date": "रिपोर्ट तिथि",
        "attending_doctor": "उपस्थित चिकित्सक", "specialty": "विशेषज्ञता",
        "diagnosis": "निदान", "prescribed_medicines": "निर्धारित दवाइयाँ",
        "no_medicines": "इस रिपोर्ट में कोई दवा नहीं है।",
        "table_headers": ["दवा", "खुराक", "आवृत्ति", "अवधि", "मात्रा", "निर्देश"],
        "advice_for_patient": "रोगी के लिए सलाह",
        "clinical_vitals": "क्लिनिकल नोट्स और वाइटल्स",
        "heart_rate": "हृदय गति", "blood_pressure": "रक्तचाप",
        "spo2": "SpO<sub>2</sub>", "ejection_fraction": "इजेक्शन फ्रैक्शन",
        "ecg_note": "ईसीजी नोट", "clinical_notes_label": "क्लिनिकल नोट्स:",
        "sincerely": "सादर,",
        "footer_note": "यह {app} की एक सिस्टम-जनित रिपोर्ट है। इस रिपोर्ट के बारे में प्रश्नों के लिए, पोर्टल के माध्यम से अपने डॉक्टर से संपर्क करें।",
    },
    "Tamil": {
        "title": "நோயாளி சுகாதார அறிக்கை", "generated": "உருவாக்கப்பட்டது",
        "patient": "நோயாளி", "report_date": "அறிக்கை தேதி",
        "attending_doctor": "கலந்துகொள்ளும் மருத்துவர்", "specialty": "சிறப்பு",
        "diagnosis": "நோய் கண்டறிதல்", "prescribed_medicines": "பரிந்துரைக்கப்பட்ட மருந்துகள்",
        "no_medicines": "இந்த அறிக்கையில் மருந்துகள் இல்லை.",
        "table_headers": ["மருந்து", "அளவு", "அதிர்வெண்", "காலம்", "எண்ணிக்கை", "வழிமுறைகள்"],
        "advice_for_patient": "நோயாளிக்கான ஆலோசனை",
        "clinical_vitals": "மருத்துவக் குறிப்புகள் & உயிர்ச்சான்றுகள்",
        "heart_rate": "இதய துடிப்பு", "blood_pressure": "இரத்த அழுத்தம்",
        "spo2": "SpO<sub>2</sub>", "ejection_fraction": "எஜெக்ஷன் பிராக்ஷன்",
        "ecg_note": "ஈசிஜி குறிப்பு", "clinical_notes_label": "மருத்துவக் குறிப்புகள்:",
        "sincerely": "நன்றியுடன்,",
        "footer_note": "இது {app} இலிருந்து தானாக உருவாக்கப்பட்ட அறிக்கை. இது குறித்த கேள்விகளுக்கு, போர்ட்டல் வழியாக உங்கள் மருத்துவரைத் தொடர்பு கொள்ளவும்.",
    },
    "Telugu": {
        "title": "రోగి ఆరోగ్య నివేదిక", "generated": "రూపొందించబడింది",
        "patient": "రోగి", "report_date": "నివేదిక తేదీ",
        "attending_doctor": "సంరక్షణ వైద్యుడు", "specialty": "ప్రత్యేకత",
        "diagnosis": "నిర్ధారణ", "prescribed_medicines": "సూచించిన మందులు",
        "no_medicines": "ఈ నివేదికలో మందులు లేవు.",
        "table_headers": ["మందు", "మోతాదు", "ఫ్రీక్వెన్సీ", "వ్యవధి", "పరిమాణం", "సూచనలు"],
        "advice_for_patient": "రోగికి సలహా",
        "clinical_vitals": "క్లినికల్ నోట్స్ & వైటల్స్",
        "heart_rate": "హృదయ స్పందన", "blood_pressure": "రక్తపోటు",
        "spo2": "SpO<sub>2</sub>", "ejection_fraction": "ఎజెక్షన్ ఫ్రాక్షన్",
        "ecg_note": "ఈసీజీ నోట్", "clinical_notes_label": "క్లినికల్ నోట్స్:",
        "sincerely": "మీ విధేయుడు,",
        "footer_note": "ఇది {app} నుండి వ్యవస్థ ద్వారా రూపొందించిన నివేదిక. దీనికి సంబంధించిన ప్రశ్నలకు, పోర్టల్ ద్వారా మీ వైద్యుడిని సంప్రదించండి.",
    },
    "Bengali": {
        "title": "রোগীর স্বাস্থ্য প্রতিবেদন", "generated": "তৈরি হয়েছে",
        "patient": "রোগী", "report_date": "প্রতিবেদনের তারিখ",
        "attending_doctor": "উপস্থিত চিকিৎসক", "specialty": "বিশেষত্ব",
        "diagnosis": "রোগ নির্ণয়", "prescribed_medicines": "নির্ধারিত ওষুধ",
        "no_medicines": "এই প্রতিবেদনে কোনো ওষুধ নেই।",
        "table_headers": ["ওষুধ", "মাত্রা", "ফ্রিকোয়েন্সি", "মেয়াদ", "পরিমাণ", "নির্দেশনা"],
        "advice_for_patient": "রোগীর জন্য পরামর্শ",
        "clinical_vitals": "ক্লিনিক্যাল নোট ও ভাইটালস",
        "heart_rate": "হৃদস্পন্দন", "blood_pressure": "রক্তচাপ",
        "spo2": "SpO<sub>2</sub>", "ejection_fraction": "ইজেকশন ফ্র্যাকশন",
        "ecg_note": "ইসিজি নোট", "clinical_notes_label": "ক্লিনিক্যাল নোট:",
        "sincerely": "বিনীত,",
        "footer_note": "এটি {app} থেকে একটি সিস্টেম-জেনারেটেড প্রতিবেদন। এই প্রতিবেদন সম্পর্কে প্রশ্নের জন্য, পোর্টালের মাধ্যমে আপনার ডাক্তারের সাথে যোগাযোগ করুন।",
    },
    "Marathi": {
        "title": "रुग्ण आरोग्य अहवाल", "generated": "तयार केले",
        "patient": "रुग्ण", "report_date": "अहवाल तारीख",
        "attending_doctor": "उपस्थित डॉक्टर", "specialty": "विशेषज्ञता",
        "diagnosis": "निदान", "prescribed_medicines": "सुचवलेली औषधे",
        "no_medicines": "या अहवालात कोणतीही औषधे नाहीत.",
        "table_headers": ["औषध", "डोस", "वारंवारता", "कालावधी", "प्रमाण", "सूचना"],
        "advice_for_patient": "रुग्णासाठी सल्ला",
        "clinical_vitals": "क्लिनिकल नोट्स आणि व्हायटल्स",
        "heart_rate": "हृदय गती", "blood_pressure": "रक्तदाब",
        "spo2": "SpO<sub>2</sub>", "ejection_fraction": "इजेक्शन फ्रॅक्शन",
        "ecg_note": "ईसीजी नोंद", "clinical_notes_label": "क्लिनिकल नोट्स:",
        "sincerely": "आपला विश्वासू,",
        "footer_note": "हा {app} कडून स्वयंचलितपणे तयार केलेला अहवाल आहे. या अहवालाबद्दल प्रश्नांसाठी, पोर्टलद्वारे आपल्या डॉक्टरांशी संपर्क साधा.",
    },
    "Gujarati": {
        "title": "દર્દી આરોગ્ય અહેવાલ", "generated": "બનાવવામાં આવ્યું",
        "patient": "દર્દી", "report_date": "અહેવાલ તારીખ",
        "attending_doctor": "ઉપસ્થિત ડોક્ટર", "specialty": "વિશેષતા",
        "diagnosis": "નિદાન", "prescribed_medicines": "સૂચવેલી દવાઓ",
        "no_medicines": "આ અહેવાલમાં કોઈ દવા નથી.",
        "table_headers": ["દવા", "માત્રા", "આવર્તન", "સમયગાળો", "જથ્થો", "સૂચનાઓ"],
        "advice_for_patient": "દર્દી માટે સલાહ",
        "clinical_vitals": "ક્લિનિકલ નોંધો અને વાઇટલ્સ",
        "heart_rate": "હૃદય ગતિ", "blood_pressure": "બ્લડ પ્રેશર",
        "spo2": "SpO<sub>2</sub>", "ejection_fraction": "ઇજેક્શન ફ્રેક્શન",
        "ecg_note": "ઈસીજી નોંધ", "clinical_notes_label": "ક્લિનિકલ નોંધો:",
        "sincerely": "આપનો વિશ્વાસુ,",
        "footer_note": "આ {app} તરફથી સિસ્ટમ-જનરેટેડ અહેવાલ છે. આ અહેવાલ વિશેના પ્રશ્નો માટે, પોર્ટલ દ્વારા તમારા ડોક્ટરનો સંપર્ક કરો.",
    },
    "Kannada": {
        "title": "ರೋಗಿಯ ಆರೋಗ್ಯ ವರದಿ", "generated": "ರಚಿಸಲಾಗಿದೆ",
        "patient": "ರೋಗಿ", "report_date": "ವರದಿ ದಿನಾಂಕ",
        "attending_doctor": "ಹಾಜರಿದ್ದ ವೈದ್ಯರು", "specialty": "ವಿಶೇಷತೆ",
        "diagnosis": "ರೋಗನಿರ್ಣಯ", "prescribed_medicines": "ಸೂಚಿಸಿದ ಔಷಧಿಗಳು",
        "no_medicines": "ಈ ವರದಿಯಲ್ಲಿ ಯಾವುದೇ ಔಷಧಿಗಳಿಲ್ಲ.",
        "table_headers": ["ಔಷಧಿ", "ಪ್ರಮಾಣ", "ಆವರ್ತನ", "ಅವಧಿ", "ಸಂಖ್ಯೆ", "ಸೂಚನೆಗಳು"],
        "advice_for_patient": "ರೋಗಿಗೆ ಸಲಹೆ",
        "clinical_vitals": "ಕ್ಲಿನಿಕಲ್ ಟಿಪ್ಪಣಿಗಳು ಮತ್ತು ವೈಟಲ್ಸ್",
        "heart_rate": "ಹೃದಯ ಬಡಿತ", "blood_pressure": "ರಕ್ತದೊತ್ತಡ",
        "spo2": "SpO<sub>2</sub>", "ejection_fraction": "ಎಜೆಕ್ಷನ್ ಫ್ರಾಕ್ಷನ್",
        "ecg_note": "ಇಸಿಜಿ ಟಿಪ್ಪಣಿ", "clinical_notes_label": "ಕ್ಲಿನಿಕಲ್ ಟಿಪ್ಪಣಿಗಳು:",
        "sincerely": "ವಂದನೆಗಳೊಂದಿಗೆ,",
        "footer_note": "ಇದು {app} ನಿಂದ ಸಿಸ್ಟಮ್-ಜನರೇಟೆಡ್ ವರದಿಯಾಗಿದೆ. ಈ ವರದಿಯ ಬಗ್ಗೆ ಪ್ರಶ್ನೆಗಳಿಗಾಗಿ, ಪೋರ್ಟಲ್ ಮೂಲಕ ನಿಮ್ಮ ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸಿ.",
    },
    "Malayalam": {
        "title": "രോഗിയുടെ ആരോഗ്യ റിപ്പോർട്ട്", "generated": "സൃഷ്ടിച്ചത്",
        "patient": "രോഗി", "report_date": "റിപ്പോർട്ട് തീയതി",
        "attending_doctor": "ചികിത്സിക്കുന്ന ഡോക്ടർ", "specialty": "വിശേഷത",
        "diagnosis": "രോഗനിർണയം", "prescribed_medicines": "നിർദ്ദേശിച്ച മരുന്നുകൾ",
        "no_medicines": "ഈ റിപ്പോർട്ടിൽ മരുന്നുകൾ ഇല്ല.",
        "table_headers": ["മരുന്ന്", "അളവ്", "ആവൃത്തി", "കാലാവധി", "എണ്ണം", "നിർദ്ദേശങ്ങൾ"],
        "advice_for_patient": "രോഗിക്കുള്ള ഉപദേശം",
        "clinical_vitals": "ക്ലിനിക്കൽ കുറിപ്പുകളും വൈറ്റലുകളും",
        "heart_rate": "ഹൃദയമിടിപ്പ്", "blood_pressure": "രക്തസമ്മർദ്ദം",
        "spo2": "SpO<sub>2</sub>", "ejection_fraction": "ഇജക്ഷൻ ഫ്രാക്ഷൻ",
        "ecg_note": "ഇസിജി കുറിപ്പ്", "clinical_notes_label": "ക്ലിനിക്കൽ കുറിപ്പുകൾ:",
        "sincerely": "വിശ്വസ്തതയോടെ,",
        "footer_note": "ഇത് {app} ൽ നിന്നുള്ള സിസ്റ്റം-ജനറേറ്റഡ് റിപ്പോർട്ടാണ്. ഈ റിപ്പോർട്ടിനെക്കുറിച്ചുള്ള ചോദ്യങ്ങൾക്ക്, പോർട്ടൽ വഴി നിങ്ങളുടെ ഡോക്ടറെ ബന്ധപ്പെടുക.",
    },
    "Punjabi": {
        "title": "ਮਰੀਜ਼ ਸਿਹਤ ਰਿਪੋਰਟ", "generated": "ਤਿਆਰ ਕੀਤੀ ਗਈ",
        "patient": "ਮਰੀਜ਼", "report_date": "ਰਿਪੋਰਟ ਮਿਤੀ",
        "attending_doctor": "ਹਾਜ਼ਰ ਡਾਕਟਰ", "specialty": "ਵਿਸ਼ੇਸ਼ਤਾ",
        "diagnosis": "ਨਿਦਾਨ", "prescribed_medicines": "ਸਿਫਾਰਸ਼ ਕੀਤੀਆਂ ਦਵਾਈਆਂ",
        "no_medicines": "ਇਸ ਰਿਪੋਰਟ ਵਿੱਚ ਕੋਈ ਦਵਾਈ ਨਹੀਂ ਹੈ।",
        "table_headers": ["ਦਵਾਈ", "ਖੁਰਾਕ", "ਬਾਰੰਬਾਰਤਾ", "ਮਿਆਦ", "ਮਾਤਰਾ", "ਹਦਾਇਤਾਂ"],
        "advice_for_patient": "ਮਰੀਜ਼ ਲਈ ਸਲਾਹ",
        "clinical_vitals": "ਕਲੀਨਿਕਲ ਨੋਟਸ ਅਤੇ ਵਾਈਟਲਸ",
        "heart_rate": "ਦਿਲ ਦੀ ਧੜਕਣ", "blood_pressure": "ਬਲੱਡ ਪ੍ਰੈਸ਼ਰ",
        "spo2": "SpO<sub>2</sub>", "ejection_fraction": "ਇਜੈਕਸ਼ਨ ਫਰੈਕਸ਼ਨ",
        "ecg_note": "ਈਸੀਜੀ ਨੋਟ", "clinical_notes_label": "ਕਲੀਨਿਕਲ ਨੋਟਸ:",
        "sincerely": "ਸਤਿਕਾਰ ਸਹਿਤ,",
        "footer_note": "ਇਹ {app} ਤੋਂ ਇੱਕ ਸਿਸਟਮ-ਤਿਆਰ ਰਿਪੋਰਟ ਹੈ। ਇਸ ਰਿਪੋਰਟ ਬਾਰੇ ਸਵਾਲਾਂ ਲਈ, ਪੋਰਟਲ ਰਾਹੀਂ ਆਪਣੇ ਡਾਕਟਰ ਨਾਲ ਸੰਪਰਕ ਕਰੋ।",
    },
    "Urdu": {
        "title": "مریض کی صحت کی رپورٹ", "generated": "تیار کردہ",
        "patient": "مریض", "report_date": "رپورٹ کی تاریخ",
        "attending_doctor": "حاضر ڈاکٹر", "specialty": "خصوصیت",
        "diagnosis": "تشخیص", "prescribed_medicines": "تجویز کردہ ادویات",
        "no_medicines": "اس رپورٹ میں کوئی دوا نہیں ہے۔",
        "table_headers": ["دوا", "خوراک", "تعدد", "مدت", "مقدار", "ہدایات"],
        "advice_for_patient": "مریض کے لیے مشورہ",
        "clinical_vitals": "کلینیکل نوٹس اور وائٹلز",
        "heart_rate": "دل کی دھڑکن", "blood_pressure": "بلڈ پریشر",
        "spo2": "SpO<sub>2</sub>", "ejection_fraction": "ایجیکشن فریکشن",
        "ecg_note": "ای سی جی نوٹ", "clinical_notes_label": "کلینیکل نوٹس:",
        "sincerely": "مخلص,",
        "footer_note": "یہ {app} کی جانب سے خودکار طور پر تیار کردہ رپورٹ ہے۔ اس رپورٹ کے بارے میں سوالات کے لیے، پورٹل کے ذریعے اپنے ڈاکٹر سے رابطہ کریں۔",
    },
}


def get_report_labels(language: str = "English") -> dict:
    return REPORT_LABELS.get(language, REPORT_LABELS["English"])


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


def render_report_html(report: dict, language: str = "English") -> str:
    """Renders a report dict as a standalone HTML document. `language`
    controls only the UI chrome (section headers, table column names,
    field labels) via REPORT_LABELS above — medicine names, dosages,
    frequencies, durations, instructions, diagnosis/advice free text, and
    all numeric vitals are always rendered exactly as stored, in whatever
    language they were originally entered in. See the REPORT_LABELS
    docstring for why that boundary is deliberate.

    Non-Latin languages get a real embedded script font (see
    LANGUAGE_FONTS above) instead of silently falling back to Helvetica,
    which has no glyphs for those scripts at all."""
    L = {
        k: ([_shape(x, language) for x in v] if isinstance(v, list) else _shape(v, language))
        for k, v in get_report_labels(language).items()
    }

    def esc(value) -> str:
        """Escape + shape (for Urdu) in one step — use this instead of
        the bare module-level _esc() for anything rendered in this
        function, so dynamic report data (patient/doctor names, notes)
        gets the same RTL handling the static labels above already got."""
        return _esc(_shape(str(value) if value is not None else "", language))

    items_rows = "".join(
        f"""<tr>
            <td>{esc(it['medicine_name'])}</td>
            <td>{esc(it['dosage']) or '&mdash;'}</td>
            <td>{esc(it['frequency']) or '&mdash;'}</td>
            <td>{esc(it['duration']) or '&mdash;'}</td>
            <td>{it['quantity']}</td>
            <td>{esc(it['instructions']) or '&mdash;'}</td>
        </tr>"""
        for it in report["items"]
    )
    header_cells = "".join(f"<th>{h}</th>" for h in L["table_headers"])

    vitals_section = _vitals_html(report.get("vitals"), L, language)
    advice_section = (
        f'<div class="section"><h3>{L["advice_for_patient"]}</h3><p>{esc(report["advice_note"])}</p></div>'
        if report.get("advice_note") else ""
    )
    diagnosis_line = (
        f'<p><strong>{L["diagnosis"]}:</strong> {esc(report["diagnosis"])}</p>'
        if report.get("diagnosis") else ""
    )

    generated_on = datetime.now().strftime("%d %b %Y, %I:%M %p")
    created_on = report["created_at"].strftime("%d %b %Y, %I:%M %p")

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    {_font_face_css(language)}
    body {{ font-family: {_body_font_family(language)}; color: #23302D; font-size: 11pt; margin: 24px; }}
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
        <h1><span class="badge"></span>{L["title"]}</h1>
        <p class="subtitle">{esc(settings.APP_NAME)} &middot; {L["generated"]} {generated_on}</p>
    </div>

    <table class="meta-table">
        <tr><td class="label">{L["patient"]}</td><td>{esc(report['patient_name'])}</td>
            <td class="label">{L["report_date"]}</td><td>{created_on}</td></tr>
        <tr><td class="label">{L["attending_doctor"]}</td><td>{esc(_doctor_label(report['doctor_name']))}</td>
            <td class="label">{L["specialty"]}</td><td>{esc(report['doctor_specialty'])}</td></tr>
    </table>

    <div class="section">
        <h3>{L["diagnosis"]}</h3>
        {diagnosis_line or '<p>&mdash;</p>'}
    </div>

    {vitals_section}

    <div class="section">
        <h3>{L["prescribed_medicines"]}</h3>
        <table class="meds">
            <tr>{header_cells}</tr>
            {items_rows or f'<tr><td colspan="6">{L["no_medicines"]}</td></tr>'}
        </table>
    </div>

    {advice_section}

    <div class="footer">
        <div class="signature">
            {L["sincerely"]}<br>
            <span class="name">{esc(_doctor_label(report['doctor_name']))}</span><br>
            {esc(report['doctor_specialty'])}<br>
            {esc(settings.APP_NAME)}
        </div>
        <p style="margin-top:14px;">{L["footer_note"].format(app=esc(settings.APP_NAME))}</p>
    </div>
</body>
</html>"""


def _vitals_html(vitals: dict, L: dict, language: str = "English") -> str:
    if not vitals:
        return ""
    rows = [
        (L["heart_rate"], f"{vitals['heart_rate']} bpm" if vitals["heart_rate"] else None),
        (L["blood_pressure"], vitals["blood_pressure"]),
        (L["spo2"], f"{vitals['pulse_oximetry']}%" if vitals["pulse_oximetry"] else None),
        (L["ejection_fraction"], f"{vitals['ejection_fraction']}%" if vitals["ejection_fraction"] else None),
        (L["ecg_note"], vitals["ecg_note"]),
    ]
    vitals_rows = "".join(
        f'<tr><td class="label">{label}</td><td>{_esc(_shape(str(value), language)) if value else "&mdash;"}</td></tr>'
        for label, value in rows
    )
    notes_html = (
        f'<p style="margin-top:8px;"><strong>{L["clinical_notes_label"]}</strong> {_esc(_shape(str(vitals["notes"]), language))}</p>'
        if vitals["notes"] else ""
    )
    return f"""<div class="section">
        <h3>{L["clinical_vitals"]}</h3>
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


def render_report_pdf(report: dict, language: str = "English") -> bytes:
    """Converts render_report_html(report, language) to PDF bytes via
    xhtml2pdf. Raises RuntimeError (not the raw xhtml2pdf exception) on
    failure, so callers can show a friendly message instead of a
    traceback."""
    try:
        from xhtml2pdf import pisa
    except ImportError as e:
        raise RuntimeError(
            "PDF export needs the 'xhtml2pdf' package — run `pip install xhtml2pdf` and restart the app."
        ) from e

    import io

    html = render_report_html(report, language)
    buffer = io.BytesIO()
    result = pisa.CreatePDF(html, dest=buffer)
    if result.err:
        raise RuntimeError("Couldn't generate the PDF for this report.")
    return buffer.getvalue()


_IMAGE_FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG"}

# Common install locations for poppler's binaries on Windows, checked
# when the binary isn't already on PATH — same fallback pattern
# ai/report_reader.py already uses for Tesseract, for the same reason:
# a Windows PATH edit doesn't apply to a terminal/IDE that was already
# open when poppler was installed, which is a common cause of "is
# poppler installed and in PATH?" even when it genuinely is. Covers the
# two most common ways people end up with poppler on Windows: the
# oschwartz10612/poppler-windows release (unzipped anywhere, commonly
# straight under C:\poppler) and installing it via conda.
_WINDOWS_POPPLER_CANDIDATES = [
    r"C:\poppler\Library\bin",
    r"C:\poppler\bin",
    r"C:\Program Files\poppler\Library\bin",
    r"C:\Program Files\poppler\bin",
    r"C:\Program Files (x86)\poppler\bin",
]


def _find_windows_poppler_path():
    """Returns a poppler bin/ folder to pass to pdf2image explicitly, or
    None to fall back to relying on PATH as normal. Only searches on
    Windows and only when pdftoppm.exe isn't already reachable via PATH
    — this never overrides a working PATH-based install."""
    if os.name != "nt":
        return None
    import shutil
    if shutil.which("pdftoppm"):
        return None  # already on PATH — nothing to do
    for candidate in _WINDOWS_POPPLER_CANDIDATES:
        if os.path.isfile(os.path.join(candidate, "pdftoppm.exe")):
            return candidate
    return None


def render_report_image(report: dict, fmt: str = "png", language: str = "English") -> bytes:
    """Renders the report as a single raster image (PNG/JPG) instead of a
    PDF — same template, same language handling, just rasterized. Reuses
    render_report_pdf() and pdf2image (already a project dependency via
    ai/ocr_extraction.py) rather than a second HTML-to-image pipeline, so
    the PDF and image exports can never visually drift apart.

    Only the first page is returned — patient reports in this app are
    short enough to fit one page in practice; a very long multi-medicine
    report would need pagination handling this doesn't attempt.

    Raises RuntimeError (never the raw pdf2image/PIL exception) so
    callers can show a friendly message, matching render_report_pdf()'s
    contract.
    """
    fmt = fmt.lower()
    if fmt not in _IMAGE_FORMATS:
        raise RuntimeError(f"Unsupported image format '{fmt}' — use png, jpg, or jpeg.")

    try:
        from pdf2image import convert_from_bytes
    except ImportError as e:
        raise RuntimeError(
            "Image export needs the 'pdf2image' package plus the poppler "
            "binary — run `pip install pdf2image` (`apt install "
            "poppler-utils` / `brew install poppler`) and restart the app."
        ) from e

    import io

    pdf_bytes = render_report_pdf(report, language)
    poppler_path = _find_windows_poppler_path()
    try:
        convert_kwargs = {"dpi": 200}
        if poppler_path:
            convert_kwargs["poppler_path"] = poppler_path
        pages = convert_from_bytes(pdf_bytes, **convert_kwargs)
    except Exception as e:
        msg = str(e)
        if "poppler" in msg.lower() or "page count" in msg.lower():
            raise RuntimeError(
                "Image export needs the poppler binary installed and on "
                "your PATH (the 'pdf2image' Python package alone isn't "
                "enough — it just calls poppler's command-line tools). "
                "Install it with `apt install poppler-utils` on Linux, "
                "`brew install poppler` on Mac, or download the Windows "
                "binaries and add their bin/ folder to PATH, then restart "
                "the app. PDF download doesn't need this and will keep "
                "working either way."
            ) from e
        raise RuntimeError(f"Couldn't rasterize this report to an image. ({e})") from e
    if not pages:
        raise RuntimeError("Couldn't generate an image for this report.")

    image = pages[0]
    if fmt == "jpg" or fmt == "jpeg":
        image = image.convert("RGB")  # JPEG has no alpha channel

    buffer = io.BytesIO()
    image.save(buffer, format=_IMAGE_FORMATS[fmt])
    return buffer.getvalue()