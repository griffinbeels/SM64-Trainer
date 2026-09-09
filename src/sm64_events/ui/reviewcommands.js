// The transport owns loop markers. Video clicks and keyboard commands share it.
import { prepareReviewPlayback, seekReviewSource } from "./reviewsource.js";
const reviews = new WeakMap();
export function watchReviewCommands(video, read) {
  reviews.set(video, read);
  return () => { if (reviews.get(video) === read) reviews.delete(video); };
}
export function reviewCommand(video, name) {
  reviews.get(video)?.()?.[name]?.();
}
export function playReview(video) {
  if (!video) return;
  const range = reviews.get(video)?.()?.range;
  if (range?.enabled && (video.currentTime < range.start || video.currentTime >= range.end))
    seekReviewSource(video, range.start);
  prepareReviewPlayback(video);
  return video.play().catch(() => {});
}
