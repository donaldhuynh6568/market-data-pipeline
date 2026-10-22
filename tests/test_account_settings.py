import importlib


def _reload_config(monkeypatch, **env):
    """config reads env at call time, but reload keeps each test isolated from .env side effects."""
    for name in ("ACCOUNT_SIZE", "RISK_PER_TRADE_PCT", "MAX_POSITION_PCT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    import market_data_pipeline.config as config

    return importlib.reload(config)


def test_env_wins_over_the_rules_file(monkeypatch):
    config = _reload_config(monkeypatch, ACCOUNT_SIZE="250000", RISK_PER_TRADE_PCT="0.5")

    resolved = config.account_settings({"account_size": 100_000, "risk_per_trade_pct": 1.0, "max_position_pct": 25})

    assert resolved["account_size"] == 250_000.0
    assert resolved["risk_per_trade_pct"] == 0.5
    assert resolved["max_position_pct"] == 25.0  # unset in env, taken from the rules file
    assert resolved["from_env"] is True


def test_rules_file_wins_over_the_built_in_default(monkeypatch):
    config = _reload_config(monkeypatch)

    resolved = config.account_settings({"account_size": 50_000})

    assert resolved["account_size"] == 50_000.0
    assert resolved["risk_per_trade_pct"] == 1.0  # built-in default
    assert resolved["from_env"] is False


def test_a_missing_account_does_not_raise(monkeypatch):
    config = _reload_config(monkeypatch)

    resolved = config.account_settings()

    assert resolved["account_size"] == 100_000.0  # the report must still build


def test_formatted_and_unparseable_values(monkeypatch):
    config = _reload_config(monkeypatch, ACCOUNT_SIZE="$250,000")
    assert config.account_settings()["account_size"] == 250_000.0

    config = _reload_config(monkeypatch, ACCOUNT_SIZE="not a number")
    assert config.account_settings({"account_size": 75_000})["account_size"] == 75_000.0


def test_an_implausible_risk_percentage_is_warned_about(monkeypatch, capsys):
    config = _reload_config(monkeypatch)

    config.account_settings({"account_size": 25_000, "risk_per_trade_pct": 25.0})

    warning = capsys.readouterr().out
    assert "WARNING" in warning and "6,250" in warning  # the dollar consequence, not just the percentage


def test_a_normal_risk_percentage_is_silent(monkeypatch, capsys):
    config = _reload_config(monkeypatch)

    config.account_settings({"account_size": 25_000, "risk_per_trade_pct": 1.0})

    assert capsys.readouterr().out == ""
