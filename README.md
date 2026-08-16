# Patient-Care-Management-System-For-Healthcare-Services

## SmartCare — Integrated Patient Care Management (Cardiac Portal)

Streamlit + MySQL app with three roles — **Patient**, **Doctor**, **Admin** —
built on the `patient_care_db` schema you created in MySQL Workbench.

## 1. Set up the database
Your tables (`users`, `specialties`, `doctors`, `doctor_slots`,
`appointments`, `health_records`, `chat_messages`) are already created.
No schema changes are required.

> **Note on prescriptions:** your schema doesn't have a dedicated
> `prescriptions` table, so a doctor's "prescribe medicine" action is
> stored as a new row in `health_records` — `diagnosis` holds the
> condition and `notes` holds the medicines/instructions. If you'd
> rather have a separate `prescriptions` table, that's a small schema
> addition we can make later.

## 2. Configure environment
```bash
cp .env.example .env
# then edit .env with your real MySQL password
```

## 3. Install dependencies
```bash
python -m venv patient_care
source patient_care/bin/activate        # Windows: patient_care\Scripts\activate
pip install -r requirements.txt
```
The AI chatbot dependencies (`groq`, `sentence-transformers`, `chromadb`)
are optional — the app runs fine without them, the chatbot just tells the
patient it's offline until you add a `GROQ_API_KEY`.

## 4. Seed specialties + an admin login
```bash
python seed_data.py
```
This prints the bootstrap admin email/password (from `.env`, or the
defaults `admin@smartcare.local` / `Admin@123`). Log in as admin, then
register doctor and patient test accounts from the app's own sign-up
card — no separate seeding needed for those.

## 5. Run the app
```bash
./run.sh
# or directly:
streamlit run app.py
```

## What's included
- **Card-style login / register** with role selection (patient / doctor
  / admin); doctor sign-up also captures specialty, experience and fee.
- **Patient dashboard** — vitals snapshot, health-record history with a
  heart-rate trend chart, appointment booking (with live slot
  availability) and a doctor directory filterable by specialty.
- **Doctor portal** — today's schedule with status updates, a
  prescribing form that writes diagnosis/medicines to the patient's
  health record, and weekly availability management.
- **Admin portal** — hospital-wide KPIs, appointment-volume and status
  charts, and appointment / patient / doctor directories (with each
  patient's health records viewable inline).
- **SmartCare Assistant** — a lightweight RAG chatbot (Groq + local
  embeddings) that answers questions using your own doctor/specialty
  data; degrades gracefully to an "offline" message if no `GROQ_API_KEY`
  is set.

## Design
The visual language leans into the cardiac focus of the schema
(troponin, ejection fraction, ECG notes): an ink-teal / parchment
palette, Fraunces for display type, Inter for UI text, JetBrains Mono
for vitals readouts, and a small animated ECG line used as a section
divider throughout — see `assets/styles.css`.
