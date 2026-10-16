// @vitest-environment jsdom
import {h} from "preact";
import {useState} from "preact/hooks";
import {cleanup, fireEvent, render} from "@testing-library/preact";
import {afterEach, beforeEach, expect, test, vi} from "vitest";
import {Modal} from "../../src/sm64_events/ui/components/modal.js";

beforeEach(() => {
  // jsdom has no layout. These fixture controls are visible; actual paint
  // and inert visibility are verified by the real-browser startup cases.
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{width: 1, height: 1}]);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function Parent() {
  const [open, show] = useState(false);
  return h("div", null,
    h("button", {onClick: () => show(true)}, "Open dialog"),
    open && h(Modal, {title: "Focus fixture", onClose: () => show(false)},
      h("input", {"aria-label": "Name"}), h("button", null, "Last control")));
}

test("dialog traps boundary Tab keys and Escape restores its surviving trigger", () => {
  const view = render(h(Parent));
  const trigger = view.getByRole("button", {name: "Open dialog"});
  trigger.focus();
  fireEvent.click(trigger);
  const first = view.getByRole("button", {name: "Close dialog"});
  const last = view.getByRole("button", {name: "Last control"});
  expect(document.activeElement).toBe(first);
  fireEvent.keyDown(document, {key: "Tab", shiftKey: true});
  expect(document.activeElement).toBe(last);
  fireEvent.keyDown(document, {key: "Tab"});
  expect(document.activeElement).toBe(first);
  fireEvent.keyDown(document, {key: "Escape"});
  expect(view.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});
