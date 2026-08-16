"""
core/config.py
Reads environment variables (.env) and exposes app-wide settings.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv
from sqlalchemy.engine import URL

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # --- Database ---
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: str = os.getenv("DB_PORT", "3306")
    DB_USER: str = os.getenv("DB_USER", "root")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_NAME: str = os.getenv("DB_NAME", "patient_care_db")

    # --- App ---
    APP_NAME: str = os.getenv("APP_NAME", "Patient Care Management System for Healthcare Service")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-.env")
    SESSION_TIMEOUT_MIN: int = int(os.getenv("SESSION_TIMEOUT_MIN", "120"))
    # Where the app is actually reachable — used to build the "Sign in"
    # button in account-created/notification emails. Defaults to the
    # standard local Streamlit address; set this in .env to your real
    # deployed URL once this app is hosted somewhere, or the sign-in
    # button in emails will point at localhost for anyone but you.
    APP_URL: str = os.getenv("APP_URL", "http://localhost:8501")

    # --- Email / notifications ---
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    EMAIL_FROM: str = os.getenv("EMAIL_FROM", "")

    # --- AI / chatbot (optional) ---
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
    # Multimodal (image-input) model — used only by ai/medicine_reader.py
    # for bare-pill photo identification, where OCR has no text to read.
    # Kept separate from LLM_MODEL since that one is text-only.
    VISION_LLM_MODEL: str = os.getenv("VISION_LLM_MODEL", "qwen/qwen3.6-27b")

    @property
    def database_url(self):
        """
        SQLAlchemy connection URL for MySQL via the mysqlconnector driver.
        Built with URL.create() (not an f-string) so that special
        characters in DB_USER / DB_PASSWORD — @, :, /, # etc. — are
        percent-encoded correctly instead of being misread as part of
        the host. A raw f-string here is what causes errors like
        "Unknown MySQL server host '<password>@localhost'".
        """
        return URL.create(
            drivername="mysql+mysqlconnector",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=int(self.DB_PORT),
            database=self.DB_NAME,
        )


settings = Settings()