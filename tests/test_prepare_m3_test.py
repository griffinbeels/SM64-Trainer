"""Private kit selection using inert files only; no build, SDK, app or Git."""
import json
from pathlib import Path
import stat
from types import SimpleNamespace
import zipfile

import pytest

from tools import prepare_m3_test as module


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    app = tmp_path / "built-app"
    (app / "_internal").mkdir(parents=True)
    (app / "SM64Trainer.exe").write_bytes(b"inert trainer fixture")
    (app / "_internal/base_library.zip").write_bytes(b"inert python runtime fixture")
    for name in module.CRT_FILES:
        (app / "_internal" / name).write_bytes(name.encode())
    runtime = tmp_path / "data/integrations/eos/m3-runtime"
    runtime.mkdir(parents=True)
    for name in module.RUNTIME_FILES:
        (runtime / name).write_bytes(("inert-" + name).encode())
    receipt = {"sdk": "1.19.2.1", "architecture": "x64", "core_checks_passed": True,
               "outputs": {name: {"bytes": (runtime / name).stat().st_size,
                                  "sha256": module.digest(runtime / name)} for name in module.RUNTIME_FILES[:2]}}
    (runtime / "build-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(module, "DLL_SHA256", module.digest(runtime / "EOSSDK-Win64-Shipping.dll"))
    monkeypatch.setattr(module, "ui_inputs", lambda _root: ())
    config = tmp_path / "data/integrations/eos/m3-proof.cfg"
    config.write_bytes(b"product_id=p\r\nsandbox_id=s\r\ndeployment_id=d\r\nclient_id=c\r\nclient_secret=synthetic_test_value\r\n")
    return {"workspace": tmp_path, "app": app, "runtime": runtime, "config": config, "out": tmp_path / "dist/private-kit"}


def test_portable_kit_copies_exact_app_and_only_selected_transport_inputs(inputs):
    runtime = inputs["runtime"]
    for relative in ("main.obj", "eos-core-test.exe", "identity.json", "cache/device-id.json", "SDK/Include/header.h"):
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"must never ship")
    expected_app = {path.relative_to(inputs["app"]).as_posix(): path.read_bytes()
                    for path in inputs["app"].rglob("*") if path.is_file()}
    report = module.assemble(**inputs, archive=True)
    out = inputs["out"]
    assert report["mode"] == "portable"
    assert {key.removeprefix("app/") for key in report["files"] if key.startswith("app/")} == set(expected_app)
    for name, body in expected_app.items():
        assert (out / "app" / name).read_bytes() == body
    transport = {path.name for path in (out / "live-transport").iterdir()}
    assert transport == set(module.RUNTIME_FILES) | set(module.CRT_FILES)
    for name in module.CRT_FILES:
        assert (out / "live-transport" / name).read_bytes() == (inputs["app"] / "_internal" / name).read_bytes()
    assert (out / "private/m3-player.cfg").read_bytes() == inputs["config"].read_bytes()
    assert "synthetic_test_value" not in json.dumps(report)
    for name, proof in report["files"].items():
        assert proof == {"bytes": (out / name).stat().st_size, "sha256": module.digest(out / name)}
    with zipfile.ZipFile(out.with_name(out.name + ".zip")) as archive:
        assert set(archive.namelist()) == set(report["files"]) | {"m3-private-manifest.json"}
        assert all(module.member_name(name) == name for name in archive.namelist())
        assert archive.read("private/m3-player.cfg") == inputs["config"].read_bytes()
    second = out.with_name("same-inputs-second-kit")
    module.assemble(**{**inputs, "out": second}, archive=True)
    assert module.digest(out.with_name(out.name + ".zip")) == module.digest(second.with_name(second.name + ".zip"))


def test_portable_launcher_resolves_all_paths_from_its_own_directory(inputs):
    module.assemble(**inputs)
    body = (inputs["out"] / "Run-M3-Test.bat").read_text(encoding="utf-8")
    assert 'set "SM64_EOS_HELPER=%~dp0live-transport\\eos-transport.exe"' in body
    assert 'set "SM64_EOS_CONFIG=%~dp0private\\m3-player.cfg"' in body
    assert 'set "SM64_EOS_CACHE=%~dp0live-transport\\cache"' in body
    assert 'start "" "%~dp0app\\SM64Trainer.exe"' in body
    assert body.index("Are both closed?") < body.index('start ""')
    assert str(inputs["workspace"]) not in body and "synthetic_test_value" not in body
    assert "taskkill" not in body.lower() and "git " not in body.lower()


def test_source_mode_is_a_nonportable_pointer_not_a_checkout_or_private_data_copy(inputs):
    source = inputs["workspace"] / "source-checkout"
    (source / ".venv/Scripts").mkdir(parents=True)
    (source / ".venv/Scripts/pythonw.exe").write_bytes(b"inert python")
    (source / "gui_entry.py").write_bytes(b"inert entry")
    (source / "tracker.db").write_bytes(b"must not ship")
    inputs.pop("app")
    report = module.assemble(**inputs, source_checkout=source)
    assert set(report["files"]) == {"Run-M3-Test.bat", "README-M3-TEST.txt"}
    assert report["mode"] == "source" and not (inputs["out"] / "private").exists()
    body = (inputs["out"] / "Run-M3-Test.bat").read_text(encoding="utf-8")
    assert str(source / ".venv/Scripts/pythonw.exe") in body and str(source / "gui_entry.py") in body
    assert f'set "PYTHONPATH={source / "src"}"' in body
    assert str(inputs["config"]) in body and "synthetic_test_value" not in body
    assert report["external_inputs"]["dedicated-player-configuration"]["sha256"] == module.digest(inputs["config"])
    with pytest.raises(ValueError, match="Select"):
        module.assemble(**{**inputs, "out": inputs["workspace"] / "another"}, source_checkout=source, archive=True)


@pytest.mark.parametrize("relative", ["tracker.db", "_internal/private.cfg", "_internal/sm64_events/data/local-config.json"])
def test_incidental_private_app_files_fail_before_any_copy(inputs, relative):
    path = inputs["app"] / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic private contents")
    with pytest.raises(ValueError):
        module.assemble(**inputs)
    assert not inputs["out"].exists()


def test_real_ui_policy_rejects_an_incidental_asset_present_in_source_and_app(inputs, monkeypatch):
    from tools import bundle_policy
    root = inputs["workspace"] / "source-policy"
    ui = root / "src/sm64_events/ui"
    built_ui = inputs["app"] / "_internal/sm64_events/ui"
    for directory in (ui, built_ui):
        directory.mkdir(parents=True)
        (directory / "index.html").write_bytes(b"approved UI")
        (directory / "incidental.js").write_bytes(b"untracked local script")
    # Only the Git-selected names are faked; use the real source policy and
    # bundle path policy, so merely finding a local file cannot approve it.
    monkeypatch.setattr(bundle_policy, "tracked_ui_paths", lambda _root: frozenset({"index.html"}))
    monkeypatch.setattr(module, "ui_inputs", bundle_policy.ui_inputs)
    monkeypatch.setattr(module, "ROOT", root)
    with pytest.raises(ValueError, match="first-party UI"):
        module.assemble(**inputs)
    assert not inputs["out"].exists()
    (ui / "incidental.js").unlink()
    with pytest.raises(ValueError, match="first-party UI"):
        module.assemble(**inputs)
    (built_ui / "incidental.js").unlink()
    report = module.assemble(**inputs)
    assert "app/_internal/sm64_events/ui/index.html" in report["files"]


@pytest.mark.parametrize("name", module.CRT_FILES)
def test_every_crt_dependency_is_required_for_a_portable_kit(inputs, name):
    (inputs["app"] / "_internal" / name).unlink()
    with pytest.raises(ValueError):
        module.assemble(**inputs)
    assert not inputs["out"].exists()


def test_changed_runtime_bytes_fail_the_existing_verification_receipt(inputs):
    (inputs["runtime"] / "eos-transport.exe").write_bytes(b"unverified replacement")
    with pytest.raises(ValueError, match="receipt"):
        module.assemble(**inputs)
    assert not inputs["out"].exists()


def test_other_config_names_are_rejected_before_reading_them(inputs, monkeypatch):
    other = inputs["workspace"] / "other.cfg"
    other.write_bytes(b"not a permitted input")
    read = Path.read_bytes

    def guarded(path):
        assert path != other, "arbitrary configuration was inspected"
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    with pytest.raises(ValueError, match="approved"):
        module.assemble(**{**inputs, "config": other})


def test_configuration_errors_never_print_values(inputs, capsys):
    inputs["config"].write_bytes(b"client_secret=synthetic_test_value\n")
    args = []
    for name, path in inputs.items():
        args += ["--" + name, str(path)]
    assert module.main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and json.loads(captured.err) == {"ok": False, "error": "private_kit_invalid"}
    assert "synthetic_test_value" not in captured.err


@pytest.mark.parametrize("name", ["", ".", "..", "../escape", "/absolute", "a/../b", "a//b", "a/./b",
                                  "C:/escape", "C:relative", "a\\b", "a:stream", "a.", "a ", "NUL.txt", "a?b"])
def test_archive_member_paths_reject_traversal_and_windows_aliases(name):
    with pytest.raises(ValueError):
        module.member_name(name)


def test_reparse_inputs_are_rejected_without_following_them(inputs, monkeypatch):
    target = inputs["runtime"] / "eos-transport.exe"
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == target:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="reparse"):
        module.assemble(**inputs)
    assert not inputs["out"].exists()


def test_existing_output_and_archive_are_never_overwritten(inputs):
    inputs["out"].mkdir(parents=True)
    keep = inputs["out"] / "keep.txt"
    keep.write_bytes(b"existing work")
    with pytest.raises(ValueError, match="new or empty"):
        module.assemble(**inputs)
    assert keep.read_bytes() == b"existing work"
    keep.unlink()
    archive = inputs["out"].with_name(inputs["out"].name + ".zip")
    archive.write_bytes(b"existing archive")
    with pytest.raises(ValueError, match="already exists"):
        module.assemble(**inputs, archive=True)
    assert archive.read_bytes() == b"existing archive" and not list(inputs["out"].iterdir())


def test_private_output_cannot_be_written_under_a_public_source_directory(inputs):
    with pytest.raises(ValueError, match="project-ignored"):
        module.assemble(**{**inputs, "out": inputs["workspace"] / "src/private-kit"})
    assert not (inputs["workspace"] / "src").exists()


@pytest.mark.parametrize("separator", [b"\r", b"\v", b"\f"])
def test_config_parser_does_not_accept_line_separators_the_helper_rejects(inputs, separator):
    inputs["config"].write_bytes(separator.join([
        b"product_id=p", b"sandbox_id=s", b"deployment_id=d", b"client_id=c", b"client_secret=synthetic_test_value"]))
    with pytest.raises(ValueError, match="configuration is invalid"):
        module.assemble(**inputs)
