import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const root = fileURLToPath(new URL(".", import.meta.url));
const ui = new URL("../../src/sm64_events/ui/", import.meta.url);
const index = readFileSync(new URL("index.html", ui), "utf8");
const { imports } = JSON.parse(index.match(/<script type="importmap">([\s\S]*?)<\/script>/)[1]);

export default defineConfig({
  // Use the app's import map and vendored runtime, including inside the test
  // helpers. Testing a second npm Preact instance would miss real hook behavior.
  resolve: {
    alias: Object.entries(imports).map(([name, path]) => ({
      find: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`),
      replacement: fileURLToPath(new URL(path.replace(/^\/ui\//, ""), ui)),
    })),
  },
  test: {
    root,
    include: ["*.test.js"],
    environment: "node",
    maxWorkers: 1,
    fileParallelism: false,
    isolate: true,
    watch: false,
    retry: 0,
    allowOnly: false,
    passWithNoTests: false,
    server: { deps: { inline: [/@testing-library\/preact/, /preact/] } },
  },
});
