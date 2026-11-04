"""The setup check across several Project64 test folders on one Windows account.

Griffin keeps copies side by side (stock 1.6, stock under Program Files, Wermi
v7 without Fixups, ...) and switches between them (2026-10-30). Each check must
describe one folder, say which, and mark the HKCU choices every copy shares.
"""
import hashlib

from sm64_events.core.diagnostic_privacy import safe_snapshot
from sm64_events.core.diagnostics import setup_check_markdown
from sm64_events.core.setup_profile import folder_name, load_baseline
from tests.test_setup_profile import US, by_id, fake_folder, fake_registry, layer, profile, stock_check


def test_each_check_describes_one_folder_and_labels_account_wide_choices(tmp_path):
    wermi = fake_folder(tmp_path / "a")
    wermi = wermi.rename(wermi.with_name("01-wermi-v7"))
    stock = tmp_path / "b" / "02-stock-vanilla"
    (stock / "Plugin").mkdir(parents=True)
    (stock / "Project64.exe").write_bytes(b"stock 1.6")
    (stock / "Plugin" / "RSP.dll").write_bytes(b"stock rsp")
    made = profile(fake_registry(), tmp_path)
    first = made.compare(layer(wermi) | {"pj64_running": True}, US)
    second = made.compare(layer(stock) | {"pj64_running": True}, US)
    again = made.compare(layer(wermi), US)
    assert first["folder"] == {"name": "01-wermi-v7", "location": "other", "running": True}
    assert second["folder"]["name"] == "02-stock-vanilla" and again["folder"]["running"] is False
    # Switching away and back changes nothing about the first folder: no
    # cached hash or rdb from the second leaks into it.
    assert {item["id"]: item["status"] for item in again["items"]} == {
        item["id"]: item["status"] for item in first["items"]}
    assert all(item["status"] == "match" for item in again["items"])
    stock_items = by_id(second)
    assert stock_items["emulator_build"]["yours_sha256"] == hashlib.sha256(b"stock 1.6").hexdigest()
    assert stock_items["rsp_plugin"]["status"] == "differs"
    # No rdb in this folder: the value is the registry default every copy shares.
    assert (stock_items["memory_size"]["status"], stock_items["memory_size"]["yours"]) == (
        "match", "8 MB (Project64's default)")
    assert {item["id"] for item in second["items"] if item["shared"]} == {
        "graphics_plugin", "audio_plugin", "controller_plugin", "memory_size", "cpu_core"}
    assert {item["id"] for item in first["items"] if item["shared"]} == {
        "graphics_plugin", "audio_plugin", "controller_plugin"}
    markdown = setup_check_markdown(second)
    assert "- Project64 folder: 02-stock-vanilla (other), running" in markdown
    assert ("| memory_size | required | match | 8 MB (Project64's default) | 8 MB "
            "| every Project64 on this Windows account |") in markdown
    assert "| rsp_plugin | recommended | differs |" in markdown and str(tmp_path) not in markdown


def test_program_files_and_virtualstore_name_the_location(tmp_path):
    assert stock_check(tmp_path)["folder"] == {"name": "Project64 1.6", "location": "VirtualStore", "running": False}
    plain = tmp_path / "Program Files" / "Other copy"
    (plain / "Plugin").mkdir(parents=True)
    assert profile(fake_registry(), tmp_path).compare(layer(plain), US)["folder"]["location"] == "Program Files"


def test_a_folder_name_is_shown_only_when_short_and_plain():
    assert folder_name("01-stock-vanilla") == "01-stock-vanilla"
    assert folder_name("Project64 1.6 (wermi's build v7)") == "Project64 1.6 (wermi's build v7)"
    for private in ("C:\\Users\\Someone", "a/b", "x" * 49, "", "   ", "name\nnext", "ユーザー", None):
        assert folder_name(private) == "unnamed"
    sent = safe_snapshot({"setup": {"check": {"folder": {"name": "C:\\Users\\Someone\\pj64", "location": "D:\\x",
                                                         "running": "yes"}, "items": []}}})["setup"]["check"]
    assert sent["folder"] == {"name": "unnamed", "location": "unknown", "running": False}
    kept = safe_snapshot({"setup": {"check": {"folder": {"name": "01-stock-vanilla", "location": "VirtualStore",
                                                         "running": True}, "items": []}}})["setup"]["check"]
    assert kept["folder"] == {"name": "01-stock-vanilla", "location": "VirtualStore", "running": True}


def test_every_link_is_a_wermi_guide_page_and_downloads_are_the_guides_own():
    """His direction (2026-10-30): link the guide's page for each step, or the
    download that page itself links; never fetch or bundle a third-party file."""
    guide_downloads = {
        "emulator_build": "https://www.dropbox.com/s/cgkbhsarjthgnz8/"
                          "Project64%201.6%20%28wermi%27s%20build%20v7%29.exe?dl=1",
        "rsp_plugin": "https://github.com/aglab2/PJ64Fixups/releases",
        "audio_plugin": "https://www.mediafire.com/file/s1fil36hivy7qbj/AziAudio.dll/file"}
    for item in load_baseline()["items"]:
        assert item["link"].startswith("https://wermi.neocities.org/emuguide/"), item["id"]
        assert item.get("download") == guide_downloads.get(item["id"])
