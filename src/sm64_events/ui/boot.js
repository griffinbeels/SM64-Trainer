// Install listeners before importing the UI so failed module loads also report.
import { installErrorReporting, reportBrowserError } from "./errorreporting.js";

installErrorReporting();
import("./app.js").catch(error => reportBrowserError(error, "startup"));
