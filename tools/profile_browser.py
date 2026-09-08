"""Bounded Chrome trace + Trainer observations, without starting a recorder.

Use --url for an isolated headless browser, or --cdp-url and --url to attach
to exactly one existing page without navigating it. Actions are explicit
click/wait/press/mark steps in JSON; no arbitrary script is accepted. Trace
files open in Chrome's Performance panel. Headless results are not proof of
the desktop WebView's performance.
"""
import argparse
import base64
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError, sync_playwright


MODULE = Path(__file__).resolve().parents[1] / "src/sm64_events/ui/profiling.js"
TRACE_LIMIT = 64 * 1024 * 1024


def local_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password):
        raise argparse.ArgumentTypeError("use an explicit localhost HTTP(S) URL")
    return value


def read_actions(path):
    if path is None:
        return []
    if path.stat().st_size > 32768:
        raise ValueError("actions file exceeds 32 KiB")
    actions = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(actions, list) or len(actions) > 64:
        raise ValueError("actions must be a list of at most 64 steps")
    allowed = {"click", "wait", "press", "mark"}
    for action in actions:
        if (not isinstance(action, dict) or action.get("action") not in allowed
                or not isinstance(action.get("value"), str)
                or len(action["value"]) > 512 or set(action) != {"action", "value"}):
            raise ValueError("each step needs action click/wait/press/mark and a string value")
    return actions


def finish_trace(cdp, page, completion, path):
    cdp.send("Tracing.end")
    deadline = time.monotonic() + 15
    while not completion and time.monotonic() < deadline:
        cdp.send("Browser.getVersion")  # Pumps protocol events even if the observed page closed.
        time.sleep(.05)
    if not completion:
        raise RuntimeError("Chrome did not finish the trace")
    stream = completion[0].get("stream")
    if not stream:
        raise RuntimeError("Chrome did not return a trace stream")
    size = 0
    try:
        with path.open("xb") as output:
            while True:
                part = cdp.send("IO.read", {"handle": stream, "size": 65536})
                data = (base64.b64decode(part["data"]) if part.get("base64Encoded")
                        else part["data"].encode("utf-8"))
                size += len(data)
                if size > TRACE_LIMIT:
                    raise RuntimeError("Chrome trace exceeds 64 MiB; shorten capture")
                output.write(data)
                if part.get("eof"):
                    break
    finally:
        cdp.send("IO.close", {"handle": stream})
    return {"bytes": size, "data_loss": completion[0].get("dataLossOccurred", None)}


def run_actions(page, actions, deadline):
    for action in actions:
        remaining = max(0, deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("actions exceeded the capture duration")
        value, kind = action["value"], action["action"]
        page.evaluate("label => window.__trainerProfile.mark(label)", kind + ":" + value)
        if kind == "click":
            page.locator(value).click(timeout=min(10000, remaining * 1000))
        elif kind == "wait":
            page.locator(value).wait_for(state="visible", timeout=min(10000, remaining * 1000))
        elif kind == "press":
            page.keyboard.press(value)
        page.evaluate("label => window.__trainerProfile.mark(label)", "done:" + kind + ":" + value)
    while time.monotonic() < deadline:
        page.wait_for_timeout(max(0, min(100, (deadline - time.monotonic()) * 1000)))


def stop_collectors(page, cdp, report, observer_started, trace_started, completion, fullness, output):
    cleanup_error = None
    if observer_started:
        try:
            report["observations"] = page.evaluate("window.__trainerProfile.stop()")
        except PlaywrightError as exc:
            cleanup_error = exc
            report["errors"].append("Observer shutdown: " + str(exc))
    if trace_started:
        try:
            report["trace"] = finish_trace(cdp, page, completion, output / "chrome-trace.json")
            report["trace"]["peak_buffer_fraction"] = max(fullness) if fullness else None
            if report["trace"]["data_loss"] or any(value >= .99 for value in fullness):
                report["errors"].append("Chrome trace lost events or filled its buffer; shorten capture")
        except (PlaywrightError, OSError, RuntimeError) as exc:
            cleanup_error = exc
            report["errors"].append("Trace export: " + str(exc))
    return cleanup_error


def capture(page, output, duration_s, actions, trace=True):
    """Collect a real page; leave external browser/page lifecycle to the caller."""
    cdp = page.context.browser.new_browser_cdp_session()
    completion, errors, trace_started = [], [], False
    fullness = []
    def page_error(error):
        if len(errors) < 64:
            errors.append(str(error)[:512])
    page.on("pageerror", page_error)
    cdp.on("Tracing.tracingComplete", lambda data: completion.append(data))
    cdp.on("Tracing.bufferUsage", lambda data: fullness.append(data.get("percentFull", 0)) if len(fullness) < 310 else None)
    module_source = MODULE.read_text(encoding="utf-8")
    module_url = "data:text/javascript;base64," + base64.b64encode(module_source.encode()).decode()
    report = {"version": 1, "status": "incomplete", "actions": actions,
              "duration_s": duration_s, "trace_enabled": trace,
              "errors": [],
              "module_sha256": hashlib.sha256(module_source.encode()).hexdigest()}
    observer_started = False
    try:
        if trace:
            cdp.send("Tracing.start", {"transferMode": "ReturnAsStream", "bufferUsageReportingInterval": 1000, "traceConfig": {
                "recordMode": "recordUntilFull", "traceBufferSizeInKb": 16384,
                "enableSampling": True, "includedCategories": ["devtools.timeline",
                    "v8.execute", "blink.user_timing", "disabled-by-default-v8.cpu_profiler"]}})
            trace_started = True
        page.evaluate("async url => { window.__trainerProfile = await import(url); }", module_url)
        page.evaluate("seconds => window.__trainerProfile.start({duration_s: seconds})", duration_s)
        observer_started = True
        deadline = time.monotonic() + duration_s
        run_actions(page, actions, deadline)
        report["status"] = "complete"
    except BaseException as exc:
        report["errors"].append(str(exc) or type(exc).__name__)
        raise
    finally:
        cleanup_error = None
        try:
            cleanup_error = stop_collectors(page, cdp, report, observer_started,
                                            trace_started, completion, fullness, output)
        finally:
            report["page_errors"] = errors
            if report["errors"] or errors:
                report["status"] = "incomplete"
            (output / "browser.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            page.remove_listener("pageerror", page_error)
            cdp.detach()
        if cleanup_error is not None:
            raise RuntimeError("Browser capture cleanup failed; see browser.json") from cleanup_error
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", type=local_url, required=True)
    parser.add_argument("--cdp-url", type=local_url)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--actions", type=Path)
    parser.add_argument("--no-trace", action="store_true", help="observer-only overhead comparison")
    parser.add_argument("--screenshot", action="store_true", help="save the observed page after timing ends")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        parser.error("seconds must be between 1 and 300")
    actions = read_actions(args.actions)
    args.output.mkdir(parents=True, exist_ok=False)
    with sync_playwright() as play:
        browser = (play.chromium.connect_over_cdp(args.cdp_url) if args.cdp_url
                   else play.chromium.launch(headless=True))
        try:
            if args.cdp_url:
                pages = [page for context in browser.contexts for page in context.pages if page.url == args.url]
                if len(pages) != 1:
                    raise ValueError("--url must match exactly one existing page")
                page = pages[0]
            else:
                context = browser.new_context(viewport={"width": 1500, "height": 1000}, reduced_motion="no-preference")
                page = context.new_page()
                page.goto(args.url, wait_until="domcontentloaded")
            report = capture(page, args.output, args.seconds, actions, not args.no_trace)
            if args.screenshot:
                page.screenshot(path=str(args.output / "screen.png"))
            report["browser_version"] = browser.version
            report["mode"] = "attached" if args.cdp_url else "headless"
            (args.output / "browser.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({"output": str(args.output.resolve()), "status": report["status"]}))
            if report["status"] != "complete":
                raise SystemExit(1)
        finally:
            browser.close()  # CDP attachment closes the connection; owned headless browser exits.


if __name__ == "__main__":
    main()
