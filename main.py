"""Run with: uvicorn main:app --host 127.0.0.1 --port 8000."""
import os
from nifty_bars import DEFAULT_CSV, create_app

app = create_app(os.environ.get("NIFTY_CSV", str(DEFAULT_CSV)))
