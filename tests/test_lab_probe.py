"""The Ghost Lab's self-checks (sm64_events/scenelab/probe.py), the parts that run without a frozen build or a ROM.

The frozen receipts themselves (SM64Trainer.exe --beta-self-check and --lab-self-check, SM64TrainerSim.exe
--sim-self-check) are the release's proof; these hold the receipt's shape and the checks' own logic."""
import json

from sm64_events.scenelab import probe


def test_unicorn_runs_one_mips_instruction():
    assert probe._unicorn_runs_mips() is True


def test_a_failed_step_names_its_error_and_fails_the_receipt():
    receipt = probe.Receipt("test")
    assert receipt.check("fine", lambda: True) is True
    assert receipt.check("broken", lambda: 1 / 0) is False
    data = receipt.finish()
    assert data["ok"] is False and data["checks"][1]["error"].startswith("ZeroDivisionError")
    assert probe.Receipt("empty").finish()["ok"] is False                   # nothing checked proves nothing


def test_the_lab_self_check_refuses_a_file_that_is_no_usamune_rom(tmp_path):
    not_a_rom = tmp_path / "game.z64"
    not_a_rom.write_bytes(b"\x80\x37\x12\x40" + bytes(60))
    receipt = tmp_path / "lab.json"
    assert probe.lab_self_check([str(receipt), "--rom", str(not_a_rom), "--work", str(tmp_path / "work")]) == 2
    data = json.loads(receipt.read_text(encoding="utf-8"))
    assert data["ok"] is False and [item["name"] for item in data["checks"]] == ["inputs"]
    assert "neither Usamune" in data["checks"][0]["error"]


def test_a_receipt_path_must_be_absolute_json(tmp_path):
    import pytest
    assert probe.lab_self_check(["relative.json", "--rom", str(tmp_path / "x.z64")]) == 2
    for bad in ("relative.json", str(tmp_path / "receipt.txt"), str(tmp_path / "missing" / "receipt.json")):
        with pytest.raises(ValueError):
            probe._receipt_path(bad)
