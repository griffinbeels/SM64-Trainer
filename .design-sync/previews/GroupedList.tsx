import * as React from "react";
import { GroupedList } from "sm64-trainer-ui";

// buildTree's node shape: { key, label, count, items, children }. The key is
// the "/"-joined path, and it is what the open-set is keyed by — a preview
// that invents its own field names renders every group shut and identical.
const TREE = {
  items: [],
  children: [
    {
      key: "wf", label: "Whomp's Fortress", count: 4,
      items: [
        { id: "wf:chip", label: "Chip Off Whomp's Block", time: `0'21"90` },
        { id: "wf:top", label: "To the Top of the Fortress", time: `0'26"13` },
      ],
      children: [
        {
          key: "wf/xcam", label: "Xcam strategies", count: 2,
          items: [
            { id: "wf:chip:xcam", label: "Chip — Xcam", time: `0'20"43` },
            { id: "wf:top:xcam", label: "Top — Xcam", time: `0'24"77` },
          ],
          children: [],
        },
      ],
    },
    {
      key: "bitdw", label: "Bowser in the Dark World", count: 1,
      items: [{ id: "bitdw:any", label: "Bowser 1", time: `1'08"20` }],
      children: [],
    },
  ],
};

const Row = (item: any) => (
  <li key={item.id} style={{ display: "flex", gap: 12, padding: "5px 2px", listStyle: "none" }}>
    <span style={{ flex: 1 }}>{item.label}</span>
    <span style={{ color: "var(--muted)" }}>{item.time}</span>
  </li>
);

const S = ({ children }: any) => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20, maxWidth: 520 }}>{children}</div>
);

/** Nested groups, each child indented one level so the parent/child
 *  relationship is visible without reading the labels. */
export const NestedAndPartlyOpen = () => (
  <S>
    <GroupedList tree={TREE} open={new Set(["wf", "wf/xcam"])} toggle={() => {}} renderRow={Row} />
  </S>
);

/** Everything shut — the index a Library page opens on. */
export const AllCollapsed = () => (
  <S>
    <GroupedList tree={TREE} open={new Set<string>()} toggle={() => {}} renderRow={Row} />
  </S>
);
