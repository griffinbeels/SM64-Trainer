import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";

const html = htm.bind(h);

// THE collapsible group renderer for every library in the app. Feed it a tree
// from group.js and a row renderer; it owns the chrome (headers, counts,
// chevrons, indent, open state) and nothing else. Consumers today: the route
// library and the segment library; next: the categorized picker modal.
//
// Depth is unbounded — a node's children render through the same component
// one level deeper, and the indent is a CSS custom property, so a third level
// needs no new class and no new rule.

/** Which groups are OPEN, not which are shut — so "nothing stored" means
 *  everything collapsed, which is what a 65-entry library wants. Inverting
 *  the meaning under an existing key would read a user's collapsed list as
 *  their open list, so every consumer passes its OWN, NEW key. */
export function useOpenGroups(openKey) {
  const [open, setOpen] = useState(() => {
    try { return new Set(JSON.parse(localStorage.getItem(openKey)) || []); }
    catch { return new Set(); }   // private mode: all collapsed, still usable
  });
  const toggle = (path) => setOpen((prev) => {
    const next = new Set(prev);
    if (!next.delete(path)) next.add(path);
    try { localStorage.setItem(openKey, JSON.stringify([...next])); }
    catch { /* private mode: collapse still works for this session */ }
    return next;
  });
  return [open, toggle];
}

/**
 * tree      { items, children } from group.js buildTree
 * open      Set of open node paths (from useOpenGroups)
 * toggle    (path) => void
 * renderRow (item) => vnode
 * forceOpen optional (node) => bool — a search hit opens its group regardless
 * depth     internal; callers omit it
 */
export function GroupedList({ tree, open, toggle, renderRow, forceOpen, depth = 0 }) {
  return html`
    ${tree.items.map(renderRow)}
    ${tree.children.map((node) => {
      const shut = !open.has(node.key) && !(forceOpen && forceOpen(node));
      return html`<div class="lib-cat" key=${node.key} style=${`--depth:${depth}`}>
        <button class=${`lib-cat-header ${shut ? "shut" : ""} ${depth > 0 ? "sub" : ""}`}
            aria-expanded=${!shut} onclick=${() => toggle(node.key)}>
          <${Icon} name="chevron" size=${depth > 0 ? 14 : 15} />
          <b>${node.label}</b>
          <span class="count-badge">${node.count}</span>
        </button>
        ${shut ? null : html`<div class="lib-group">
          <${GroupedList} tree=${node} open=${open} toggle=${toggle}
            renderRow=${renderRow} forceOpen=${forceOpen} depth=${depth + 1} />
        </div>`}
      </div>`;
    })}`;
}
