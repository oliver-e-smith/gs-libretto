"""Vercel Python entrypoint — exposes the FastAPI app."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webapp.app import app  # noqa: E402,F401
