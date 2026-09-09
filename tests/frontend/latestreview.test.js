// @vitest-environment jsdom
import { h } from "preact";
import { render, cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { afterEach, expect, test, vi } from "vitest";
import { newestReview, reviewRows } from "../../src/sm64_events/ui/latestreview.js";
import { ReviewLatest } from "../../src/sm64_events/ui/components/reviewlatest.js";
vi.mock("../../src/sm64_events/ui/components/attemptdrawer.js",()=>({
  AttemptDrawer:({attemptId})=>h("div",{"data-attempt":attemptId}),
}));
afterEach(cleanup);

test("completion time wins across ID namespaces and longer earlier attempts", () => {
  const rows = [
    {id:100000000099,ended_utc:"2026-09-07T00:00:01Z",entityKey:"segment:1"},
    {id:1,ended_utc:"2026-09-07T00:00:02Z",entityKey:"star:1:1"},
  ];
  expect(newestReview(rows).id).toBe(1);
  rows[0].ended_utc=rows[1].ended_utc;
  expect(newestReview(rows,"star:1:1").id).toBe(1);
  expect(newestReview(rows,"segment:1").id).toBe(100000000099);
});

test("imports do not become Review latest, real failed attempts do", () => {
  const view={stars:[{course_id:1,star_id:1,attempts:[
    {id:1,ended_utc:"2026-09-07T00:00:00Z",imported:true},
    {id:2,ended_utc:"2026-09-07T00:00:01Z",outcome:"reset"},
  ]}]};
  expect(reviewRows(view).map(row=>row.id)).toEqual([2]);
});

test("split completion messages retain the original target, and reconnect advances only the button", async () => {
  const target={kind:"star",course_id:1,star_id:1};
  const view={session:{id:1},target,stars:[],segments:[]};
  let tracker={view,feed:[]};
  const mount=()=>h(ReviewLatest,{t:tracker,openCompare:vi.fn()});
  const rendered=render(mount());
  const star={type:"attempt_completed",timestamp_utc:"2026-09-07T00:00:02Z",
    payload:{...target,session_id:1,attempt_id:2,star_name:"Chosen star"}};
  tracker={...tracker,feed:[star]};rendered.rerender(mount());
  await waitFor(()=>expect(rendered.getByText("Chosen star")).toBeTruthy());
  const segment={...star,payload:{session_id:1,attempt_id:10000001,kind:"segment",segment_id:3,segment_name:"Other segment"}};
  tracker={...tracker,view:{...view,target:{kind:"star",course_id:2,star_id:1}},feed:[segment,star]};
  rendered.rerender(mount());
  fireEvent.click(rendered.getByRole("button",{name:/Review latest/}));
  await waitFor(()=>expect(rendered.container.querySelector("[data-attempt]").dataset.attempt).toBe("2"));
  expect(document.activeElement.classList.contains("latest-review-surface")).toBe(true);
  tracker={...tracker,view:{...view,stars:[{...target,star_name:"Newer star",attempts:[
    {id:3,ended_utc:"2026-09-07T00:00:03Z"},
  ]}]}};
  rendered.rerender(mount());
  await waitFor(()=>expect(rendered.container.querySelector(".review-latest-label").textContent).toBe("Newer star"));
  expect(rendered.container.querySelector("[data-attempt]").dataset.attempt).toBe("2");
});
