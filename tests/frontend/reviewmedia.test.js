import { expect, test } from "vitest";
import { pictureInterval, loopSeekTime } from "../../src/sm64_events/ui/reviewmedia.js";

test("markers use actual held-picture intervals, including adjacent 90 kHz ticks", () => {
  const clock = { times: [0,.1,.2,.2+1/90000,.3] };
  expect(pictureInterval(null,clock,null,.8)).toBeNull();
  expect(pictureInterval(.2,clock,null,.8)).toEqual({start:.2,end:.2+1/90000});
  expect(pictureInterval(.3,clock,null,.8)).toEqual({start:.3,end:.8});
  expect(loopSeekTime({start:.2,end:.3},clock,null,.8)).toBe(.2);
  expect(loopSeekTime({start:1,end:3},{times:[0,1,2,3]},null,4)).toBe(1);
});

test("downloaded markers require measured encoded timing and clamp their last interval", () => {
  expect(pictureInterval(.2,null,null,1)).toBeNull();
  expect(pictureInterval(.3,null,.1,.35)).toEqual({start:.30000000000000004,end:.35});
});
