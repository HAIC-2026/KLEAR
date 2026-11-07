import os

import pytest

from klear import config


def test_load_secret_reads_env_file(tmp_path, monkeypatch):
    f = tmp_path / "env"
    f.write_text("CENSUS_API_KEY=abc123\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", f)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    assert config.load_secret("CENSUS_API_KEY") == "abc123"


def test_env_var_wins_over_file(tmp_path, monkeypatch):
    f = tmp_path / "env"
    f.write_text("CENSUS_API_KEY=abc123\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", f)
    monkeypatch.setenv("CENSUS_API_KEY", "zzz")
    assert config.load_secret("CENSUS_API_KEY") == "zzz"


def test_missing_secret_message_has_no_value(tmp_path, monkeypatch):
    f = tmp_path / "env"
    f.write_text("OTHER_KEY=secretvalue\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", f)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    with pytest.raises(config.MissingSecretError) as exc:
        config.load_secret("CENSUS_API_KEY")
    msg = str(exc.value)
    assert "CENSUS_API_KEY" in msg
    assert ".env" in msg
    assert "~/.config/klear/env" in msg
    assert "secretvalue" not in msg


def test_project_env_wins_over_user_file_and_works_outside_repo(tmp_path, monkeypatch):
    project_env = tmp_path / ".env"
    project_env.write_text("export CENSUS_API_KEY='project-key'\n", encoding="utf-8-sig")
    user_env = tmp_path / "user-env"
    user_env.write_text("CENSUS_API_KEY=user-key\n", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ENV_FILE", project_env)
    monkeypatch.setattr(config, "ENV_FILE", user_env)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert config.load_secret("CENSUS_API_KEY") == "project-key"
    assert "CENSUS_API_KEY" not in os.environ


def test_process_env_wins_over_project_env(tmp_path, monkeypatch):
    project_env = tmp_path / ".env"
    project_env.write_text("CENSUS_API_KEY=project-key\n", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ENV_FILE", project_env)
    monkeypatch.setenv("CENSUS_API_KEY", "process-key")
    assert config.load_secret("CENSUS_API_KEY") == "process-key"


def test_blank_project_key_falls_back_to_user_file(tmp_path, monkeypatch):
    project_env = tmp_path / ".env"
    project_env.write_text("CENSUS_API_KEY=\n", encoding="utf-8")
    user_env = tmp_path / "user-env"
    user_env.write_text("CENSUS_API_KEY=user-key\n", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ENV_FILE", project_env)
    monkeypatch.setattr(config, "ENV_FILE", user_env)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    assert config.load_secret("CENSUS_API_KEY") == "user-key"


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
