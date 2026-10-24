"""The shadow-config mismatch must reach a usable repair in the shipping wizard."""
from test_ui_setup_modal import (
    INSTALLED, click_text, connect, get_driver, scenario, serve_ui, wait_page, wait_step,
)


def test_shadow_mismatch_repair_is_reachable_with_current_dlls(tmp_path):
    layer, observed = scenario()
    connect(layer, observed)
    layer.update(INSTALLED, renderer_present=True, renderer_current=True,
                 pj64_running=False, configuration_consistent=False, state="regressed",
                 problems=["Windows has a different per-user capture configuration."])
    repaired = {**INSTALLED, "configuration_consistent": True, "problems": []}
    with serve_ui(capture_layer_status=layer, capture_layer_install=repaired,
                  setup_observer=lambda _: observed) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        wait_page(page, "install")
        wait_step(page, "install")
        before = page.evaluate("fetch('/api/setup').then(r => r.json())")
        assert before["emu"]["wrapper_current"] and before["emu"]["renderer_current"]
        assert not before["emu"]["configuration_consistent"] and not before["emu"]["installation_verified"]
        (tmp_path / "shadow-repair.png").write_bytes(page.screenshot())
        click_text(page, "Install Practice Replay")
        wait_step(page, "reopen")
        after = page.evaluate("fetch('/api/setup').then(r => r.json())")
        assert after["emu"]["configuration_consistent"] and after["emu"]["installation_verified"]
        assert page.count(".setup-install-action") == 0
        assert page.problems() == []
