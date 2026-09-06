// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";
import { h } from "preact";
import { useState } from "preact/hooks";
import { TimeFields } from "../../src/sm64_events/ui/components/timefields.js";

afterEach(cleanup);

function editor(initial = null) {
  const commits = vi.fn();
  function Parent() {
    const [seconds, setSeconds] = useState(initial);
    return h(TimeFields, { seconds, label: "Time", onCommit(value) {
      commits(value);
      setSeconds(value);
    } });
  }
  const view = render(h(Parent));
  return { ...view, commits, box: (name) => view.getByRole("spinbutton", { name: `Time ${name}` }) };
}

test("committing seconds leaves untouched centiseconds blank after the parent echoes the value", () => {
  const { box, commits } = editor();
  fireEvent.input(box("seconds"), { target: { value: "11" } });
  expect(commits).not.toHaveBeenCalled();
  fireEvent.blur(box("seconds"));
  expect(commits).toHaveBeenLastCalledWith(11);
  expect(box("centis").value).toBe("");
  fireEvent.input(box("centis"), { target: { value: "5" } });
  fireEvent.blur(box("centis"));
  expect(commits).toHaveBeenLastCalledWith(11.05);
  expect(box("centis").value).toBe("05");
});

test("successive field edits retain the previously entered values", () => {
  const { box, commits } = editor();
  for (const [name, value] of [["minutes", "1"], ["seconds", "21"], ["centis", "32"]]) {
    fireEvent.input(box(name), { target: { value } });
    fireEvent.blur(box(name));
  }
  expect(commits.mock.calls.map(([value]) => value)).toEqual([60, 81, 81.32]);
});

test("an external value replaces the displayed fields, and clearing it empties them", () => {
  const onCommit = vi.fn();
  const view = render(h(TimeFields, { seconds: 81.32, label: "Time", onCommit }));
  const values = () => view.getAllByRole("spinbutton").map((box) => box.value);
  expect(values()).toEqual(["1", "21", "32"]);
  view.rerender(h(TimeFields, { seconds: 23.05, label: "Time", onCommit }));
  expect(values()).toEqual(["", "23", "05"]);
  view.rerender(h(TimeFields, { seconds: null, label: "Time", onCommit }));
  expect(values()).toEqual(["", "", ""]);
  expect(onCommit).not.toHaveBeenCalled();
});

test("clearing all fields commits no time, while an explicit zero stays zero", () => {
  const { box, commits } = editor(23.05);
  for (const name of ["seconds", "centis"]) {
    fireEvent.input(box(name), { target: { value: "" } });
    fireEvent.blur(box(name));
  }
  expect(commits).toHaveBeenLastCalledWith(null);
  fireEvent.input(box("seconds"), { target: { value: "0" } });
  fireEvent.blur(box("seconds"));
  expect(commits).toHaveBeenLastCalledWith(0);
});
