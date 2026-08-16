#!/usr/bin/env bash
# Activates the virtual environment (if present) and launches the app.
set -e

if [ -d "patient_care" ]; then
  source patient_care/bin/activate
fi

streamlit run app.py
