// tools/responsive_probe.js
//
// Injected into the running app by tools/responsive_sweep.py. Defines one
// global, __sweep(navTabs), which measures the CURRENT layout and returns the
// defects it can PROVE.
//
// Semantic assertions, not pixel diffing. This app has a live clock, a
// recording timer and (soon) motion on every button, so a screenshot baseline
// would go red for reasons unrelated to any change -- the visual-regression
// literature is mostly about fighting exactly that flake. These measurements
// need no baseline, survive dynamic content, and each result NAMES the defect
// instead of reporting that 37 pixels differ.
//
// Everything returned must survive JSON.stringify through CDP's
// returnByValue: plain objects, numbers and strings only.
(() => {
  const EPS = 1.5;                        // sub-pixel layout noise

  const path = (el) => {
    if (el.id) return "#" + el.id;
    const cls = (el.className || "").toString().trim().split(/\s+/)
      .filter(Boolean).slice(0, 3).join(".");
    return el.tagName.toLowerCase() + (cls ? "." + cls : "");
  };

  const visible = (el) => {
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (parseFloat(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  // Elements that must never truncate. Opting IN is deliberate: a blanket
  // "nothing may ellipsise" fires on every intentional text-overflow in the
  // app, and a probe that cries wolf gets exemption-listed into silence.
  // These four are the ones the design says carry irreducible information.
  const NEVER_TRUNCATE = [".rank-banner-kicker", ".context-label",
                          ".nav-item span", ".field-label"];

  window.__sweep = (navTabs) => {
    const out = {overflow: [], clipped: [], truncated: [], overlap: [],
                 unreachable: []};
    const all = Array.from(document.querySelectorAll("body *")).filter(visible);

    // 1. Content escaping the viewport sideways.
    //
    //    Measured by GEOMETRY, not by document.scrollWidth, and that is the
    //    whole point: index.html:531 sets `html, body, #app { overflow-x:
    //    hidden }`, so this app can never grow a horizontal scrollbar and
    //    documentElement.scrollWidth can never exceed innerWidth. Sideways
    //    overflow here is HIDDEN, not prevented -- the user just sees content
    //    cut off at the right edge with nothing to hint at it. A scrollWidth
    //    check would therefore have returned clean forever (measured
    //    2026-07-28: a deliberately injected 5000px-wide child did not move it
    //    by a pixel).
    //
    //    getBoundingClientRect reports real geometry regardless of clipping,
    //    so it still sees the escape. An element hanging past the edge is only
    //    legitimate when some ancestor can SCROLL to reveal it.
    const scrollableAncestor = (el) => {
      for (let node = el.parentElement; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (["auto", "scroll"].includes(style.overflowX)) return true;
      }
      return false;
    };
    const offscreenByDesign = (el) => {
      const style = getComputedStyle(el);
      // .sr-only and friends park themselves outside the viewport on purpose.
      return style.clip !== "auto" || style.clipPath !== "none"
             || style.position === "fixed";
    };
    const doc = document.documentElement;
    if (doc.scrollWidth - window.innerWidth > EPS)
      out.overflow.push({selector: ":root", detail:
        "scrollWidth " + doc.scrollWidth + " > innerWidth " + window.innerWidth});
    for (const el of all) {
      if (el.children.length > 0) continue;      // leaves only: one report per escape
      if (offscreenByDesign(el) || scrollableAncestor(el)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0) continue;
      if (rect.right - window.innerWidth > EPS)
        out.overflow.push({selector: path(el), detail:
          "right edge " + Math.round(rect.right) + " past viewport "
          + window.innerWidth});
      else if (rect.left < -EPS)
        out.overflow.push({selector: path(el), detail:
          "left edge " + Math.round(rect.left) + " before viewport origin"});
    }

    // 2. Content clipped inside an overflow:hidden box. THIS is the reported
    //    bug. .objective-card is a hard fixed height with hidden overflow, so
    //    content that outgrows it is silently guillotined -- and both end
    //    states look fine in a screenshot, which is why it survived review.
    for (const el of all) {
      const style = getComputedStyle(el);
      const hiddenY = style.overflowY === "hidden" || style.overflow === "hidden";
      const hiddenX = style.overflowX === "hidden" || style.overflow === "hidden";
      if (hiddenY && el.scrollHeight - el.clientHeight > EPS)
        out.clipped.push({selector: path(el), detail:
          "scrollHeight " + el.scrollHeight + " > clientHeight " + el.clientHeight});
      // Horizontal clipping is only a defect when nothing is ellipsising --
      // a truncated label is a deliberate, legible outcome; a hard cut is not.
      if (hiddenX && el.scrollWidth - el.clientWidth > EPS
          && style.textOverflow !== "ellipsis")
        out.clipped.push({selector: path(el), detail:
          "scrollWidth " + el.scrollWidth + " > clientWidth " + el.clientWidth});
    }

    // 3. Opted-in elements that are truncating.
    for (const selector of NEVER_TRUNCATE)
      for (const el of document.querySelectorAll(selector)) {
        if (!visible(el)) continue;
        if (el.scrollWidth - el.clientWidth > EPS)
          out.truncated.push({selector: selector, detail:
            '"' + el.textContent.trim().slice(0, 40) + '" truncated'});
      }

    // 4. Overlap between NORMAL-FLOW siblings. Narrow on purpose: absolutely
    //    positioned things, negative margins and the rank washes overlap by
    //    design, and a broad test drowns the real signal in them.
    for (const parent of all) {
      const kids = Array.from(parent.children).filter((kid) => {
        if (!visible(kid)) return false;
        const style = getComputedStyle(kid);
        if (!["static", "relative"].includes(style.position)) return false;
        if (kid.hasAttribute("data-allow-overlap")) return false;
        return true;
      });
      for (let i = 0; i < kids.length; i++)
        for (let j = i + 1; j < kids.length; j++) {
          const a = kids[i].getBoundingClientRect();
          const b = kids[j].getBoundingClientRect();
          const dx = Math.min(a.right, b.right) - Math.max(a.left, b.left);
          const dy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
          if (dx > EPS && dy > EPS)
            out.overlap.push({selector: path(kids[i]) + " x " + path(kids[j]),
              detail: "overlap " + Math.round(dx) + "x" + Math.round(dy)
                      + "px inside " + path(parent)});
        }
    }

    // 5. A tab the shell claims to have, with no way to reach it at this size.
    //    This is what the Rank dead end below 760px needed: Rank is in app.js's
    //    NAV_GROUPS registry and in neither hardcoded mobile list, so it was
    //    unreachable and no unit test could ever have noticed.
    const labels = new Set(
      Array.from(document.querySelectorAll("button, a, [role=button], [role=tab]"))
        .filter(visible).map((el) => (el.textContent || "").trim()));
    for (const tab of (navTabs || []))
      if (!labels.has(tab))
        out.unreachable.push({selector: tab,
                              detail: "no visible control opens it"});

    return out;
  };
})();
