import pytest

from klear import config


def test_load_secret_reads_env_file(tmp_path, monkeypatch):
    f = tmp_path / "env"
    f.write_text("CENSUS_API_KEY=abc123\n")
    monkeypatch.setattr(config, "ENV_FILE", f)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    assert config.load_secret("CENSUS_API_KEY") == "abc123"


def test_env_var_wins_over_file(tmp_path, monkeypatch):
    f = tmp_path / "env"
    f.write_text("CENSUS_API_KEY=abc123\n")
    monkeypatch.setattr(config, "ENV_FILE", f)
    monkeypatch.setenv("CENSUS_API_KEY", "zzz")
    assert config.load_secret("CENSUS_API_KEY") == "zzz"


def test_missing_secret_message_has_no_value(tmp_path, monkeypatch):
    f = tmp_path / "env"
    f.write_text("OTHER_KEY=secretvalue\n")
    monkeypatch.setattr(config, "ENV_FILE", f)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    with pytest.raises(config.MissingSecretError) as exc:
        config.load_secret("CENSUS_API_KEY")
    msg = str(exc.value)
    assert "CENSUS_API_KEY" in msg
    assert "~/.config/klear/env" in msg
    assert "secretvalue" not in msg


def test_httpx_info_log_masks_key(caplog):
    import logging

    import httpx

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    with caplog.at_level(logging.INFO):
        client.get("https://api.census.gov/data/2024/acs/acs5?get=NAME&for=state:15&key=SECRET")
    client.close()
    assert any("HTTP Request" in r.getMessage() for r in caplog.records)
    for r in caplog.records:
        assert "SECRET" not in r.getMessage()
        assert "SECRET" not in caplog.text
