import { expect, test } from "vitest";
import { lastPictureTime, pictureInterval, loopSeekTime } from "../../src/sm64_events/ui/reviewmedia.js";
import { timeOfSlot } from "../../src/sm64_events/ui/frame.js";

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

test("the seek bar ends on the last picture, exactly where a step to the final frame lands", () => {
  const clock = { times: [0, .1, .2, .3], duration: .35 };
  // A step to the final frame lands on timeOfSlot of the last slot, bounded by the media.
  expect(lastPictureTime(clock, null, .4)).toBeCloseTo(timeOfSlot(3, { ...clock, duration: .4 }), 12);
  expect(lastPictureTime(clock, null, .4)).toBeCloseTo(.35, 12);
  // Measured encoded steps: the middle of the last (possibly short) interval.
  expect(lastPictureTime(null, .1, .35)).toBeCloseTo(.325, 12);
  // No picture timing, or no media yet: the duration, or nothing.
  expect(lastPictureTime(null, null, 4)).toBe(4);
  expect(lastPictureTime({ times: null }, null, 4)).toBe(4);
  expect(lastPictureTime(clock, null, 0)).toBeNull();
});
