import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'service.db'}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://"):]
if os.getenv("VERCEL") and not os.getenv("DATABASE_URL"):
    raise RuntimeError("DATABASE_URL is required on Vercel")
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "blob" if os.getenv("VERCEL") else "local")
API_KEY = os.getenv("ONTORAG_SERVICE_API_KEY", "")
if os.getenv("VERCEL") and not API_KEY:
    raise RuntimeError("ONTORAG_SERVICE_API_KEY is required on Vercel")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(4 * 1024 * 1024)))
PUBLIC_BASE_IRI = os.getenv("ONTORAG_BASE_IRI", "https://example.org/ontorag/")
