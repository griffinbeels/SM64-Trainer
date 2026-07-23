import hashlib
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "release", Path(__file__).resolve().parents[1] / "tools" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


def test_bump_version_py_rewrites_constant():
    src = '__version__ = "0.1.0"\n'
    out = release.bump_version_py(src, "1.2.3")
    assert '__version__ = "1.2.3"' in out
    assert "0.1.0" not in out


def test_bump_pyproject_rewrites_project_version():
    src = '[project]\nname = "x"\nversion = "0.1.0"\n'
    out = release.bump_pyproject(src, "1.2.3")
    assert 'version = "1.2.3"' in out


def test_sha256_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    assert release.sha256_file(f) == hashlib.sha256(b"hello").hexdigest()


def test_valid_version_accepts_semver():
    assert release.valid_version("1.2.3") is True
    assert release.valid_version("v1.2.3") is False
    assert release.valid_version("1.2") is False


def test_write_sha_writes_verifiable_line(tmp_path):
    p = tmp_path / "SM64Trainer-full.zip"
    p.write_bytes(b"payload")
    side = release.write_sha(p)
    digest, name = side.read_text().split()
    assert name == "SM64Trainer-full.zip"
    assert digest == hashlib.sha256(b"payload").hexdigest()


def test_release_assets_names_and_order(tmp_path):
    assets = release.release_assets(tmp_path)
    assert [a.name for a in assets] == [
        "SM64Trainer-full.zip", "SM64Trainer-full.zip.sha256",
        "manifest.json", "manifest.json.sha256",
        "SM64Trainer.exe", "SM64Trainer.exe.sha256"]
