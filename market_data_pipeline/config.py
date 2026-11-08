import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
CONFIG_DIR = ROOT_DIR / "config"
TICKERS_PATH = CONFIG_DIR / "tickers.yaml"
STRATEGY_PATH = CONFIG_DIR / "strategy.yaml"


def require_alpaca_credentials() -> tuple[str, str]:
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise SystemExit(
            "Missing ALPACA_API_KEY / ALPACA_SECRET_KEY. Add them to your .env file before running the pipeline."
        )
    return api_key, secret_key


def require_fred_api_key() -> str:
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        raise SystemExit(
            "Missing FRED_API_KEY. Add it to your .env file before running the FRED collector. "
            "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html"
        )
    return api_key


def require_sec_user_agent() -> str:
    user_agent = os.getenv("SEC_USER_AGENT")
    if not user_agent:
        raise SystemExit(
            "Missing SEC_USER_AGENT. SEC EDGAR's fair-use policy requires a descriptive "
            "User-Agent with contact info (e.g. 'YourApp your-email@example.com'). "
            "Add it to your .env file before running SEC collectors."
        )
    return user_agent
