import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
TICKERS_PATH = ROOT_DIR / "tickers.yaml"


def require_alpaca_credentials() -> tuple[str, str]:
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise SystemExit(
            "Missing ALPACA_API_KEY / ALPACA_SECRET_KEY. Add them to your .env file before running the pipeline."
        )
    return api_key, secret_key
