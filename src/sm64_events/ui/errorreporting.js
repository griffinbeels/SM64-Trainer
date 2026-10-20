// Browser failures use the same local collector and privacy setting as Python.
// Transport errors are swallowed here to avoid recursively reporting reporting.
import { send, setApiFailureReporter } from "./api.js";

let installed = false;
const recent = new Map();

export function reportBrowserError(error, source = "browser") {
  let message = "Browser error";
  let stack = "";
  try {
    message = String(error?.message || error || message).slice(0, 1000);
    stack = String(error?.stack || "").slice(0, 6000);
  } catch { return; }
  const now = Date.now();
  const signature = `${source}:${message}`;
  if (recent.size > 50) recent.clear();
  if (now - (recent.get(signature) || 0) < 60000) return;
  recent.set(signature, now);
  void send("POST", "/api/diagnostics/browser-error", { message, stack, source })
    .catch(() => {});
}

export function installErrorReporting() {
  if (installed) return;
  installed = true;
  setApiFailureReporter(reportBrowserError);
  window.addEventListener("error", event => {
    if (event.error || event.message) reportBrowserError(event.error || event.message, "uncaught");
  });
  window.addEventListener("unhandledrejection", event => {
    reportBrowserError(event.reason, "unhandled-promise");
  });
}
