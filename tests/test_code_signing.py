"""Our release files are signed when this machine is set up for it, and say so plainly when not.

tools/code_signing.py with a fake signtool: no certificate is created,
installed or used, and nothing is sent anywhere.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import code_signing  # noqa: E402


@pytest.fixture
def files(tmp_path):
    made = [tmp_path / "SM64Trainer.exe", tmp_path / "sm64_trainer_gfx.dll"]
    for path in made:
        path.write_bytes(b"MZ fixture")
    return made


@pytest.fixture
def configured(tmp_path, monkeypatch):
    tools = tmp_path / "signing"
    tools.mkdir()
    signtool, dlib, metadata = tools / "signtool.exe", tools / "Azure.CodeSigning.Dlib.dll", tools / "metadata.json"
    signtool.write_bytes(b"fake")
    dlib.write_bytes(b"fake")
    metadata.write_text(json.dumps({"Endpoint": "https://wus2.codesigning.azure.net",
                                    "CodeSigningAccountName": "account", "CertificateProfileName": "profile"}))
    config = tools / "signing.json"
    config.write_text(json.dumps({"signtool": str(signtool), "dlib": str(dlib), "metadata": str(metadata)}))
    monkeypatch.setenv("SM64_SIGNING_CONFIG", str(config))
    return signtool, dlib, metadata


def test_without_a_configuration_the_files_stay_unsigned_and_it_says_so(files, tmp_path, monkeypatch):
    monkeypatch.setenv("SM64_SIGNING_CONFIG", str(tmp_path / "absent.json"))
    said = []
    result = code_signing.sign(files, runner=lambda command: pytest.fail("ran signtool"), log=said.append)
    assert result.signed is False and "unsigned" in result.note
    assert any("unsigned" in line and "docs/code-signing.md" in line for line in said)


def test_a_configured_machine_signs_with_artifact_signing_then_verifies_each_file(files, configured):
    signtool, dlib, metadata = configured
    commands = []
    result = code_signing.sign(files, runner=lambda command: commands.append(command) or (0, ""), log=lambda _: None)
    assert result.signed
    sign, *verify = commands
    # Microsoft's documented SignTool command for Artifact Signing (how-to-signing-integrations).
    assert sign[:2] == [str(signtool), "sign"]
    pairs = {sign[i]: sign[i + 1] for i in range(2, len(sign) - 1) if sign[i].startswith("/")}
    assert pairs["/fd"] == "SHA256" and pairs["/td"] == "SHA256"
    assert pairs["/tr"] == "http://timestamp.acs.microsoft.com"
    assert pairs["/dlib"] == str(dlib) and pairs["/dmdf"] == str(metadata)
    assert sign[-2:] == [str(path) for path in files]
    assert verify == [[str(signtool), "verify", "/pa", str(path)] for path in files]


def test_a_failed_signing_stops_the_release(files, configured):
    with pytest.raises(RuntimeError, match="could not sign"):
        code_signing.sign(files, runner=lambda command: (1, "SignerSign() failed"), log=lambda _: None)


def test_a_signature_that_does_not_verify_stops_the_release(files, configured):
    def runner(command):
        return (0, "") if command[1] == "sign" else (1, "No signature found.")
    with pytest.raises(RuntimeError, match="does not verify"):
        code_signing.sign(files, runner=runner, log=lambda _: None)


def test_an_incomplete_configuration_is_an_error_not_unsigned(files, configured):
    _, _, metadata = configured
    metadata.write_text(json.dumps({"Endpoint": "https://wus2.codesigning.azure.net"}))
    with pytest.raises(RuntimeError, match="CodeSigningAccountName"):
        code_signing.sign(files, runner=lambda command: (0, ""), log=lambda _: None)


def test_the_configuration_lives_outside_the_repository(monkeypatch):
    monkeypatch.delenv("SM64_SIGNING_CONFIG", raising=False)
    assert not code_signing.config_path().resolve().is_relative_to(ROOT)
