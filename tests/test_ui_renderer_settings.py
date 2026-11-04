"""Frame buffer emulation the trainer cannot turn off by itself must reach a
working Install in the shipping wizard (his rule, 2026-10-30: "MAKE SURE THAT
FRAME BUFFER EMULATION IS DISABLED"). Disposable files and fakes only."""
from test_ui_setup_modal import (
    CURRENT, MISSING_ROM, body, click_text, get_driver, serve_ui, wait_page, wait_step,
)


def protected_settings_layer(tmp_path, monkeypatch):
    """A packaged install whose GLideN64.ini came back on and whose automatic
    refresh was refused (a protected Plugin folder): it never asks for UAC."""
    from test_capturelayer import build, make_layer, renderer_for
    from test_capturelayer_refresh_recovery import TwoTicks
    from test_renderer_settings import LINK_ON, deny_settings_write
    from sm64_events.core import plugin_elevation
    from sm64_events.core.capturelayer import WRAPPER_DLL
    from sm64_events.core.renderer_settings import RENDERER_INI

    folder = tmp_path / "PJ64"
    (folder / "Plugin").mkdir(parents=True)
    bundled = tmp_path / WRAPPER_DLL
    bundled.write_bytes(build(b"wrapper"))
    renderer_for(bundled).write_bytes(build(b"renderer"))
    with monkeypatch.context() as env:
        # The layer reads its VirtualStore root once; the browser still needs the real one.
        env.setenv("LOCALAPPDATA", str(tmp_path / "original-user"))
        layer, _, _ = make_layer(folder, bundled, tmp_path / "capture.json")
    layer.install(True)
    ini = folder / "Plugin" / RENDERER_INI
    ini.write_bytes(LINK_ON)       # GLideN64's defaults came back
    attempts, real_write = deny_settings_write(monkeypatch, ini)
    monkeypatch.setattr(plugin_elevation, "elevated_install",
                        lambda *_: (_ for _ in ()).throw(AssertionError("background UAC")))
    layer.refresh_loop(TwoTicks())
    assert len(attempts) == 1 and ini.read_bytes() == LINK_ON
    return layer, ini, real_write


def test_a_protected_settings_file_opens_setup_on_a_working_install(tmp_path, monkeypatch):
    import ui_fixture
    from test_renderer_settings import LINK_OFF
    from sm64_events.core import renderer_settings

    layer, ini, real_write = protected_settings_layer(tmp_path, monkeypatch)
    monkeypatch.setattr(ui_fixture, "_FixtureCaptureLayer", lambda *a, **kw: layer)
    observed = dict(target=dict(state="ready", pid=None), rom=dict(MISSING_ROM), checks={})
    with serve_ui(setup_observer=lambda _: observed) as url, get_driver().launch(viewport=(850, 600)) as page:
        page.goto(url + "/ui/index.html")
        # The wizard opens by itself: the trainer cannot fix this alone.
        wait_page(page, "install")
        wait_step(page, "install")
        state = page.evaluate("fetch('/api/setup').then(r=>r.json())")["emu"]
        assert state["frame_buffer_emulation_off"] is False and not state["installation_verified"]
        assert state["problems"][-1].endswith("Choose Install to turn it off.")
        button = f"{CURRENT} .setup-install-action button"
        page.wait_for(button)
        assert page.evaluate(f"!document.querySelector({button!r}).disabled")
        assert "will update it automatically" not in body(page)
        (tmp_path / "protected-settings-install-action.png").write_bytes(page.screenshot())
        click_text(page, "What does this install change?")
        page.wait_ms(400)
        assert "Turns off frame buffer emulation in GLideN64.ini" in body(page)
        assert "puts your old setting back" in body(page)
        page.evaluate("""Array.from(document.querySelectorAll('.setup-modal p'))
          .find(p => p.textContent.includes('frame buffer emulation'))
          .scrollIntoView({block: 'center'})""")
        page.wait_ms(200)
        (tmp_path / "protected-settings-install-details.png").write_bytes(page.screenshot())
        click_text(page, "What does this install change?")
        monkeypatch.setattr(renderer_settings, "write_atomic", real_write)
        page.click(button)
        wait_step(page, "reopen")
        state = page.evaluate("fetch('/api/setup').then(r=>r.json())")["emu"]
        assert state["frame_buffer_emulation_off"] and state["installation_verified"]
        assert ini.read_bytes() == LINK_OFF
        assert page.problems() == []
