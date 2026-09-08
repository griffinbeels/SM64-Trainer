// Browser twin of ranks/timecurve.py. Real implementation parity is tested.
/** @typedef {readonly [number, number]} Point */
/** @param {number} frame */
function csOfFrame(frame) { return Math.floor(frame * 100 / 30); }

/** @param {number} cs */
export function framePosition(cs) {
  if (cs <= 0) return cs * .3;
  const whole = Math.floor(Math.ceil(cs) / 100);
  const upper = whole * 30 + Math.ceil((Math.ceil(cs) % 100) * 30 / 100);
  const upperCs = csOfFrame(upper);
  if (upperCs === cs) return upper;
  const lowerCs = csOfFrame(upper - 1);
  return upper - 1 + (cs - lowerCs) / (upperCs - lowerCs);
}

/** @param {number} value */
function pyRound(value) {
  const floor = Math.floor(value), diff = value - floor;
  return diff > .5 ? floor + 1 : diff < .5 ? floor : floor % 2 === 0 ? floor : floor + 1;
}

/** @param {number} frame */
export function displayPosition(frame) {
  if (frame <= 0) return pyRound(frame / .3);
  const lower = Math.floor(frame);
  return pyRound(csOfFrame(lower) + (frame - lower) * (csOfFrame(lower + 1) - csOfFrame(lower)));
}

/** @param {readonly Point[]} points */
function topNeighbor(points) { return points.slice(1).find((p) => p[0] > points[0][0]); }

/** @param {readonly Point[]} points @returns {Point} */
function tailEdge(points) {
  const [easiest, score] = points[points.length - 1];
  const previous = points.slice(0, -1).reverse().find((p) => p[0] < easiest);
  const step = previous ? Math.max(1, (easiest - previous[0]) / 5) : 1;
  return [easiest + 4 * step, score / 5];
}

/** @param {readonly Point[]} points @param {number} position */
export function scoreAt(points, position) {
  const [hardest, score] = points[0];
  if (position <= hardest) {
    const neighbor = topNeighbor(points);
    if (!neighbor) return score;
    const slope = (neighbor[1] - score) / (neighbor[0] - hardest);
    return Math.min(100, score + slope * (position - hardest));
  }
  for (let i = 0; i + 1 < points.length; i++) {
    const [fast, high] = points[i], [slow, low] = points[i + 1];
    if (position <= slow) return high + (low - high) * (position - fast) / (slow - fast);
  }
  const [easiest, high] = points[points.length - 1], [end, low] = tailEdge(points);
  if (position <= end) return high + (low - high) * (position - easiest) / (end - easiest);
  return low * end / position;
}

/** @param {readonly Point[]} points @param {number} target */
export function positionAt(points, target) {
  const [hardest, score] = points[0];
  if (target >= score) {
    const neighbor = topNeighbor(points);
    if (!neighbor) return hardest;
    return hardest + (target - score) * (neighbor[0] - hardest) / (neighbor[1] - score);
  }
  for (let i = 0; i + 1 < points.length; i++) {
    const [fast, high] = points[i], [slow, low] = points[i + 1];
    if (target >= low) return fast + (target - high) * (slow - fast) / (low - high);
  }
  if (target <= 0) return null;
  const [easiest, high] = points[points.length - 1], [end, low] = tailEdge(points);
  if (target >= low) return easiest + (target - high) * (end - easiest) / (low - high);
  return low * end / target;
}
