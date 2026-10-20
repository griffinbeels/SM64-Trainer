"""Installed reporting identity cannot be replaced by an environment variable."""
import json
import sys

import pytest

from sm64_events.core import reporting_setup as setup
from pathlib import Path

DSN = "https://public@o123.ingest.us.sentry.io/42"


@pytest.mark.parametrize("value", ["", DSN, "https://key@o1.ingest.de.sentry.io/1"])
def test_public_dsn_is_valid(value):
    assert setup.validate_dsn(value) == value


@pytest.mark.parametrize("value", [None, 12, DSN.replace("https:", "http:"),
    DSN.replace("@", ":sample-password@"), "https://public@example.com/1",
    DSN + "?unexpected=value", "https://o1.ingest.sentry.io/1", DSN + "wrong"])
def test_invalid_configuration_has_a_generic_explanation(value):
    with pytest.raises(ValueError) as failure:
        setup.validate_dsn(value)
    assert "sample-password" not in str(failure.value)
    assert "unexpected=value" not in str(failure.value)


def test_frozen_reads_its_bundle_and_ignores_source_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("SM64_SENTRY_DSN", "https://wrong@o1.ingest.sentry.io/99")
    (tmp_path / setup.CONFIG_FILE).write_text(json.dumps({"schema": 1, "dsn": DSN}))
    assert setup.configured_dsn() == DSN
    (tmp_path / setup.CONFIG_FILE).unlink()
    assert setup.configured_dsn() == ""


def test_source_reporting_is_explicit(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("SM64_SENTRY_DSN", raising=False)
    assert setup.configured_dsn() == ""
    monkeypatch.setenv("SM64_SENTRY_DSN", DSN)
    assert setup.configured_dsn() == DSN


def test_builder_bundles_the_validated_public_configuration(monkeypatch, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from build_exe import app_args, stage_reporting, SEP
    monkeypatch.setenv("SM64_SENTRY_DSN", DSN)
    config = stage_reporting(tmp_path)
    assert json.loads(config.read_text())["dsn"] == DSN
    assert f"{config}{SEP}." in app_args(None, reporting_path=config)
    assert "sentry_sdk" in app_args(None)


def test_builder_uses_the_checked_in_public_destination_when_unset(monkeypatch, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from build_exe import DATA_DIR, stage_reporting
    monkeypatch.delenv("SM64_SENTRY_DSN", raising=False)
    expected = setup.read_config_dsn(DATA_DIR / setup.CONFIG_FILE)
    assert json.loads(stage_reporting(tmp_path).read_text())["dsn"] == expected


def test_build_can_explicitly_disable_reporting(monkeypatch, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from build_exe import stage_reporting
    monkeypatch.setenv("SM64_SENTRY_DSN", "")
    assert json.loads(stage_reporting(tmp_path).read_text())["dsn"] == ""
