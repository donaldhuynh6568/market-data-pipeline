import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
TICKERS_PATH = ROOT_DIR / "tickers.yaml"


# Above this, a per-trade risk figure is far more likely to be a mistake than an intention.
IMPLAUSIBLE_RISK_PCT = 5.0


def _env_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw.replace(",", "").replace("$", "").strip())
    except ValueError:
        print(f"  [config] {name}={raw!r} is not a number, ignoring it")
        return None


def account_settings(fallback: dict | None = None) -> dict:
    """Account sizing from the environment, falling back to the rules file then to built-in defaults.

    Deliberately never exits when unset, unlike the API-key helpers: the report must still build without an
    account configured, it just says which numbers it assumed.
    """
    fallback = fallback or {}
    defaults = {"account_size": 100_000.0, "risk_per_trade_pct": 1.0, "max_position_pct": 25.0}
    env_names = {
        "account_size": "ACCOUNT_SIZE",
        "risk_per_trade_pct": "RISK_PER_TRADE_PCT",
        "max_position_pct": "MAX_POSITION_PCT",
    }
    resolved = {}
    for key, default in defaults.items():
        value = _env_float(env_names[key])
        if value is None:
            value = fallback.get(key, default)
        resolved[key] = float(value)
    resolved["from_env"] = any(os.getenv(name) for name in env_names.values())
    if resolved["risk_per_trade_pct"] > IMPLAUSIBLE_RISK_PCT:
        # Easy to set by copying max_position_pct into the wrong field, and the consequence is severe:
        # at 25% per trade, four losses in a row is a 68% drawdown.
        risk = resolved["account_size"] * resolved["risk_per_trade_pct"] / 100
        print(
            f"  [config] WARNING: risk_per_trade_pct={resolved['risk_per_trade_pct']:g}% means risking "
            f"{risk:,.0f} of {resolved['account_size']:,.0f} on a single trade. Most sizing rules use 0.5-2%."
        )
    return resolved


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
