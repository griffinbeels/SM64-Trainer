// The browser half of the agent-maintainability gate. WHY these rules and not
// a style preset: docs/agent-maintainability.md. Runs with no package.json and
// no node_modules — `npx --yes eslint@9 --no-config-lookup -c eslint.config.mjs`.
export default [
  {
    // Vendored, minified Preact. Not ours to fix, and linting it produced 334
    // phantom findings on the first run (2026-08-28) — which is exactly how a
    // gate teaches you to ignore it on day one.
    ignores: [
      "src/sm64_events/ui/preact.module.js",
      "src/sm64_events/ui/hooks.module.js",
      "src/sm64_events/ui/htm.module.js",
      "**/vendor/**",
      "ds-bundle/**",
      ".design-sync/**",
      "build/**",
      "dist/**",
    ],
  },
  {
    files: ["**/*.js", "**/*.mjs"],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "module",
      globals: {
        window: "readonly", document: "readonly", location: "readonly",
        fetch: "readonly", console: "readonly", navigator: "readonly",
        setTimeout: "readonly", clearTimeout: "readonly",
        setInterval: "readonly", clearInterval: "readonly",
        requestAnimationFrame: "readonly", cancelAnimationFrame: "readonly",
        localStorage: "readonly", sessionStorage: "readonly",
        WebSocket: "readonly", URL: "readonly", URLSearchParams: "readonly",
        Image: "readonly", Audio: "readonly", Blob: "readonly",
        performance: "readonly", history: "readonly", getComputedStyle: "readonly",
        ResizeObserver: "readonly", IntersectionObserver: "readonly",
        MutationObserver: "readonly", AbortController: "readonly",
        HTMLElement: "readonly", Element: "readonly", Node: "readonly",
        CustomEvent: "readonly", Event: "readonly", FormData: "readonly",
        FileReader: "readonly", DOMParser: "readonly", structuredClone: "readonly",
        matchMedia: "readonly", alert: "readonly", confirm: "readonly",
        process: "readonly", globalThis: "readonly",
      },
    },
    linterOptions: { reportUnusedDisableDirectives: true },
    rules: {
      // ---- silent wrongness: the value is not what the code says it is ----
      // `== null` stays legal: it is the deliberate "null or undefined" idiom
      // and 334 of the first run's 405 findings were exactly that.
      eqeqeq: ["error", "always", { null: "ignore" }],
      "no-self-compare": "error",
      "no-constant-binary-expression": "error",
      "no-unsafe-optional-chaining": "error",
      "no-dupe-else-if": "error",
      "no-duplicate-case": "error",
      // `'${x}'` inside a single-quoted string in a codebase written almost
      // entirely in template literals renders the braces to the user verbatim.
      "no-template-curly-in-string": "error",
      "no-unmodified-loop-condition": "error",
      "no-unreachable-loop": "error",

      // ---- silent failure: the thing did not happen and nothing said so ----
      "no-fallthrough": "error",
      "array-callback-return": "error",
      "no-promise-executor-return": "error",
      "no-async-promise-executor": "error",
      "require-atomic-updates": "error",
      "no-empty": ["error", { allowEmptyCatch: false }],

      // ---- dead weight the next agent has to read past ----
      // `args: "none"` on purpose: an unused parameter is usually a signature
      // the caller still passes, not a mistake.
      "no-unused-vars": ["error", { args: "none", varsIgnorePattern: "^_" }],
      "no-unused-private-class-members": "error",
      "no-useless-catch": "error",
      "no-useless-rename": "error",

      // ---- size and shape: see docs/agent-maintainability.md for the numbers ----
      complexity: ["error", 15],
      "max-lines-per-function": [
        "error",
        { max: 100, skipComments: true, skipBlankLines: true, IIFEs: true },
      ],
      "max-depth": ["error", 4],
    },
  },
];
