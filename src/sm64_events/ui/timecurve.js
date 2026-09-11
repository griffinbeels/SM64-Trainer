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

// Compiled Overall curves: browser twin of ranks/curves.py. Only interpolation
// happens here; the server publishes every fitted node and its provenance.
/** @type {Record<string, number>} */
export const SCORE_ANCHORS = { Mario: 95, Grandmaster: 90, Master: 80,
  Diamond: 70, Platinum: 60, Gold: 45, Silver: 25, Bronze: 10 };
export const DIVISION_NUMERALS = ['V', 'IV', 'III', 'II', 'I'];
export const DIVISIONS_PER_TIER = DIVISION_NUMERALS.length;
const CURVE_TIERS = Object.keys(SCORE_ANCHORS);
const MAX_CURVE_FRAME = Math.floor(Number.MAX_SAFE_INTEGER / 100) * 30;
/** @typedef {{defined: string[], points: Point[], slopes?: number[]}} PreparedCurve */

/** @param {unknown} value @param {string} name */
function finiteNumber(value, name) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`${name} must be a finite number`);
  }
  return value;
}

/** @param {any} value @returns {boolean} */
function record(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** @param {any} curve @returns {PreparedCurve} */
function checkedCurve(curve) {
  if (!record(curve) || curve.schema_version !== 1) {
    throw new Error('Unsupported curve schema_version; expected 1');
  }
  if (!['legacy', 'pchip'].includes(curve.interpolation)) {
    throw new Error('Unsupported curve interpolation');
  }
  if (!record(curve.metadata)) throw new Error('Curve metadata must be an object');
  const ladder = curve.ladder_cs;
  if (!record(ladder) || Object.keys(ladder).some((rank) => !CURVE_TIERS.includes(rank))) {
    throw new Error('Curve ladder must contain only supported rank keys');
  }
  const defined = CURVE_TIERS.filter((rank) => Object.hasOwn(ladder, rank));
  checkCutoffs(ladder, defined);
  if (curve.interpolation === 'legacy') {
    if (!Array.isArray(curve.nodes) || curve.nodes.length) {
      throw new Error('Legacy curves must have an empty node list');
    }
    return { defined, points: defined.map((rank) => [framePosition(ladder[rank]), SCORE_ANCHORS[rank]]) };
  }
  if (!Array.isArray(curve.nodes) || curve.nodes.length < 2) {
    throw new Error('PCHIP requires at least two nodes');
  }
  let lastTime = 0, lastScore = Infinity;
  const points = curve.nodes.map((/** @type {any} */ node) => {
    if (!Array.isArray(node) || node.length !== 2) throw new Error('Each node must be [display_cs, score]');
    const time = finiteNumber(node[0], 'Node time'), score = finiteNumber(node[1], 'Node score');
    if (!(time > 0 && time <= Number.MAX_SAFE_INTEGER && score > 0 && score <= 100)) {
      throw new Error('Node times must be positive safe numbers and scores in (0, 100]');
    }
    if (time <= lastTime || score >= lastScore) {
      throw new Error('Node times must strictly increase and scores strictly decrease');
    }
    lastTime = time; lastScore = score;
    return [framePosition(time), score];
  });
  if (defined.length !== CURVE_TIERS.length) throw new Error('PCHIP curves require the complete derived tier ladder');
  return { defined, points, slopes: pchipSlopes(points) };
}

/** @param {Record<string, number>} ladder @param {string[]} defined */
function checkCutoffs(ladder, defined) {
  let previous = 0;
  for (const rank of defined) {
    const value = finiteNumber(ladder[rank], 'Ladder cutoff');
    if (!Number.isSafeInteger(value) || value < previous) {
      throw new Error('Ladder cutoffs must be nonnegative, ordered safe integers');
    }
    previous = value;
  }
}

/** @param {number} h0 @param {number} h1 @param {number} d0 @param {number} d1 */
function endpoint(h0, h1, d0, d1) {
  return Math.min(0, ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1));
}

/** @param {readonly Point[]} points @returns {number[]} */
function pchipSlopes(points) {
  const h = [], d = [];
  for (let i = 0; i + 1 < points.length; i++) {
    const gap = points[i + 1][0] - points[i][0];
    if (gap <= 0) throw new Error('Node times must remain distinct on the frame coordinate');
    h.push(gap);
    d.push((points[i + 1][1] - points[i][1]) / gap);
  }
  if (d.some((slope) => !Number.isFinite(slope) || slope >= 0)) {
    throw new Error('Node slopes are not representable; separate the supplied nodes');
  }
  if (points.length === 2) return [d[0], d[0]];
  const slopes = [endpoint(h[0], h[1], d[0], d[1])];
  for (let i = 1; i + 1 < points.length; i++) {
    const w1 = 2 * h[i] + h[i - 1], w2 = h[i] + 2 * h[i - 1];
    slopes.push((w1 + w2) / (w1 / d[i - 1] + w2 / d[i]));
  }
  slopes.push(endpoint(h[h.length - 1], h[h.length - 2], d[d.length - 1], d[d.length - 2]));
  if (slopes.some((slope) => !Number.isFinite(slope))) {
    throw new Error('Node slopes are not representable; separate the supplied nodes');
  }
  return slopes;
}

/** @param {PreparedCurve} prepared @param {number} position */
function pchipScore(prepared, position) {
  const { points } = prepared, slopes = prepared.slopes || [];
  const [fast, high] = points[0], [slow, low] = points[points.length - 1];
  if (position <= fast) {
    const distance = fast - position;
    const gain = slopes[0] ? -slopes[0] * distance : (distance / (points[1][0] - fast)) ** 2;
    return Math.min(100, high + gain);
  }
  if (position >= slow) {
    const distance = position - slow;
    const ratio = distance / (slow - points[points.length - 2][0]);
    const slope = slopes[slopes.length - 1];
    const denominator = slope ? 1 + (-slope / low) * distance : 1 + ratio * ratio;
    return Math.max(Number.MIN_VALUE, low / denominator);
  }
  let right = 1;
  while (points[right][0] < position) right++;
  if (points[right][0] === position) return points[right][1];
  const i = right - 1, width = points[right][0] - points[i][0];
  const t = (position - points[i][0]) / width;
  let score = ((2 * t - 3) * t * t + 1) * points[i][1] + ((t - 2) * t + 1) * t * width * slopes[i];
  score += (-2 * t + 3) * t * t * points[right][1] + (t - 1) * t * t * width * slopes[right];
  return Math.max(points[right][1], Math.min(points[i][1], score));
}

/** @param {PreparedCurve} prepared @param {number} target @returns {number | null} */
function curveInverse(prepared, target) {
  if (!prepared.slopes) {
    if (!prepared.points.length) return null;
    const position = positionAt(prepared.points, target);
    return position == null ? null : displayPosition(position);
  }
  if (target <= 0 || target > 100 || pchipScore(prepared, 0) < target) return null;
  let low = 0, high = Math.min(MAX_CURVE_FRAME, Math.max(1, Math.ceil(prepared.points[prepared.points.length - 1][0])));
  while (pchipScore(prepared, high) >= target) {
    if (high === MAX_CURVE_FRAME) return null;
    low = high; high = Math.min(MAX_CURVE_FRAME, high * 2);
  }
  while (high - low > 1) {
    const middle = Math.floor((low + high) / 2);
    if (pchipScore(prepared, middle) >= target) low = middle;
    else high = middle;
  }
  return Math.floor(low / 30) * 100 + Math.floor((low % 30) * 100 / 30);
}

/** Score a serialized curve; null means an empty legacy ladder.
 * @param {any} curve @param {number} timeCs */
export function curveScore(curve, timeCs) {
  const prepared = checkedCurve(curve);
  finiteNumber(timeCs, 'Time');
  if (prepared.slopes && timeCs < 0) throw new Error('Curve time must be nonnegative');
  if (!prepared.points.length) return null;
  return prepared.slopes ? pchipScore(prepared, curveTimePosition(timeCs)) : scoreAt(prepared.points, framePosition(timeCs));
}

/** @param {number} time */
function curveTimePosition(time) {
  return time <= Number.MAX_SAFE_INTEGER ? framePosition(time) : time * .3;
}

/** Slowest whole frame earning targetScore; legacy keeps its old inverse.
 * @param {any} curve @param {number} targetScore */
export function curveTimeForScore(curve, targetScore) {
  const prepared = checkedCurve(curve);
  finiteNumber(targetScore, 'Target score');
  return curveInverse(prepared, targetScore);
}

/** @param {number} score @param {string[]} defined */
function divisionState(score, defined) {
  const tier = defined.find((rank) => score >= SCORE_ANCHORS[rank]) || 'Iron';
  const index = defined.indexOf(tier);
  const low = tier === 'Iron' ? 0 : SCORE_ANCHORS[tier];
  const high = tier === 'Iron' ? (defined.length ? SCORE_ANCHORS[defined[defined.length - 1]] : 100) : index > 0 ? SCORE_ANCHORS[defined[index - 1]] : 100;
  const span = high - low;
  const slice = span <= 0 ? DIVISIONS_PER_TIER - 1 : Math.max(0, Math.min(DIVISIONS_PER_TIER - 1, Math.trunc((score - low) / span * DIVISIONS_PER_TIER)));
  return { tier, division: DIVISION_NUMERALS[slice], low, high, slice };
}

/** @param {number} score @param {string[]} defined */
function curveDivisionProgress(score, defined) {
  const { tier, division, low, high, slice } = divisionState(score, defined);
  const width = (high - low) / DIVISIONS_PER_TIER;
  const floor = low + slice * width;
  const fill = width <= 0 ? 1 : Math.max(0, Math.min(1, (score - floor) / width));
  const nextAt = width <= 0 ? 100 : Math.min(100, floor + width);
  const next = divisionState(nextAt, defined);
  const maxed = next.tier === tier && next.division === division;
  return { tier, division, fill: maxed ? 1 : fill, next_tier: maxed ? null : next.tier,
    next_division: maxed ? null : next.division, next_at: maxed ? null : nextAt };
}

/** Exact fields and legacy boundary walk of ranks/scoring.progress_for_time.
 * @param {any} curve @param {number} timeCs */
export function curveProgress(curve, timeCs) {
  const prepared = checkedCurve(curve);
  finiteNumber(timeCs, 'Time');
  if (prepared.slopes && timeCs < 0) throw new Error('Curve time must be nonnegative');
  if (!prepared.points.length) return null;
  let score = prepared.slopes ? pchipScore(prepared, curveTimePosition(timeCs)) : scoreAt(prepared.points, framePosition(timeCs));
  let progress = curveDivisionProgress(score, prepared.defined), nextGap = null;
  while (progress.next_at !== null) {
    const target = curveInverse(prepared, progress.next_at);
    if (target === null) break;
    if (timeCs > target) { nextGap = timeCs - target; break; }
    score = progress.next_at;
    progress = curveDivisionProgress(score, prepared.defined);
  }
  return { score, ...progress, next_gap_cs: nextGap };
}
