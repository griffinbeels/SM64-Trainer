// The transport owns loop markers. Video clicks and keyboard commands share it.
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
    video.currentTime = range.start;
  return video.play().catch(() => {});
}
