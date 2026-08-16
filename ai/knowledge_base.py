"""
ai/knowledge_base.py
Builds a small retrieval index over (a) the hospital's own directory data
(doctors, specialties) and (b) a curated set of cardiac reference notes,
so the chatbot's search_health_info tool has real clinical-terminology
content to ground on. Deliberately narrow: it does NOT cover food, diet
or medicines — the tool's docstring tells the model to answer those from
its own knowledge instead.
"""
from functools import lru_cache

from ai.vectorstore import get_collection, index_documents, search
from services.doctor_service import list_doctors, list_specialties

# ~10 curated cardiac reference notes (blueprint §11). These explain the
# exact vitals this portal records — ejection fraction, troponin, cardiac
# output, SpO2, ECG wording — so "what is a normal EF?" gets a grounded,
# consistent answer instead of model improvisation.
CARDIAC_REFERENCE_DOCS = [
    (
        "Ejection fraction (EF) is the percentage of blood the left ventricle "
        "pumps out with each contraction. A normal EF is roughly 55-70%. "
        "An EF of 40-54% is mildly reduced, 30-39% moderately reduced, and "
        "below 30% severely reduced. A falling EF over time is a warning sign "
        "and should be discussed with a cardiologist."
    ),
    (
        "Troponin is a protein released into the blood when heart muscle is "
        "damaged. Normal high-sensitivity troponin is typically below about "
        "0.04 ng/mL. Elevated troponin can indicate a heart attack "
        "(myocardial infarction), but can also rise with myocarditis, severe "
        "infection, or kidney disease — the trend across repeat tests matters "
        "more than a single value."
    ),
    (
        "Resting heart rate for adults normally falls between 60 and 100 "
        "beats per minute (bpm). Well-trained people may sit in the 40s-50s. "
        "A persistently high resting heart rate (tachycardia, above 100 bpm) "
        "or very low rate (bradycardia, below 50 bpm with symptoms like "
        "dizziness) warrants medical review."
    ),
    (
        "Blood pressure is written as systolic/diastolic, e.g. 120/80 mmHg. "
        "Normal is below 120/80. 120-129 systolic is elevated; 130-139 or "
        "80-89 diastolic is stage 1 hypertension; 140/90 and above is stage "
        "2. Readings above 180/120 are a hypertensive crisis and need "
        "immediate care."
    ),
    (
        "Pulse oximetry (SpO2) measures the oxygen saturation of the blood. "
        "Normal is 95-100%. Values of 90-94% are low and worth medical "
        "attention; below 90% is hypoxaemia and needs urgent evaluation, "
        "especially with breathlessness or chest pain."
    ),
    (
        "Cardiac output is the volume of blood the heart pumps per minute — "
        "normally about 4 to 8 litres per minute at rest. A low cardiac "
        "output can cause fatigue, breathlessness and cold extremities, and "
        "is one of the measures doctors track in heart failure."
    ),
    (
        "An ECG (electrocardiogram) records the heart's electrical activity. "
        "'Normal sinus rhythm' means the heart's natural pacemaker is firing "
        "regularly at a normal rate. Common abnormal findings include atrial "
        "fibrillation (an irregular, often fast rhythm), ST elevation or "
        "depression (possible ischaemia or infarction), and bundle branch "
        "blocks (delayed electrical conduction)."
    ),
    (
        "Heart failure means the heart cannot pump enough blood for the "
        "body's needs — it does not mean the heart has stopped. Typical "
        "symptoms are breathlessness on exertion or lying flat, swollen "
        "ankles, and fatigue. It is tracked with ejection fraction, cardiac "
        "output and symptoms, and managed with medication and lifestyle "
        "changes."
    ),
    (
        "Warning signs of a possible heart attack include chest pain or "
        "pressure lasting more than a few minutes, pain spreading to the "
        "arm, neck, jaw or back, breathlessness, cold sweat, nausea, or "
        "sudden light-headedness. These symptoms need emergency medical "
        "attention immediately — call emergency services rather than waiting "
        "for an appointment."
    ),
    (
        "Angina is chest pain caused by reduced blood flow to the heart "
        "muscle, typically triggered by exertion or stress and relieved by "
        "rest. Stable angina follows a predictable pattern; angina that is "
        "new, worsening, or occurring at rest (unstable angina) is an "
        "emergency."
    ),
]


@lru_cache(maxsize=1)
def _build_index():
    collection = get_collection()
    docs, metas = [], []

    for i, note in enumerate(CARDIAC_REFERENCE_DOCS):
        docs.append(note)
        metas.append({"type": "reference", "id": i})

    for s in list_specialties():
        docs.append(f"Specialty: {s['name']}. {s['description'] or ''}")
        metas.append({"type": "specialty", "id": s["id"]})

    for d in list_doctors():
        docs.append(
            f"Dr. {d['name']} specializes in {d['specialty']} with "
            f"{d['experience_years']} years of experience. {d['bio'] or ''}"
        )
        metas.append({"type": "doctor", "id": d["doctor_id"]})

    if docs:
        index_documents(collection, docs, metas)
    return collection


def retrieve_context(query: str, k: int = 3) -> str:
    collection = _build_index()
    hits = search(collection, query, k)
    return "\n".join(hits)


def refresh_index():
    _build_index.cache_clear()
    _build_index()
