import { expect, test } from "vitest";
import { newestReview, reviewRows } from "../../src/sm64_events/ui/latestreview.js";

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
