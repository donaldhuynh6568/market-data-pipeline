"""Compatibility layer for the original script-based entrypoints."""

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.storage import get_supabase_client, upsert_to_supabase
from market_data_pipeline.tickers import load_tickers

__all__ = ["DATA_DIR", "load_tickers", "get_supabase_client", "upsert_to_supabase"]
