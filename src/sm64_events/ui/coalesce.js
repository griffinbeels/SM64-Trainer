// src/sm64_events/ui/coalesce.js — collapse a burst of triggers into one run
//
// One star grab publishes TWO events in the same server tick (`star_collected`
// and the `attempt_completed` it produced), and both are in store.js's
// REFRESH_ON — so the page used to refetch the whole session view AND the
// MARELO figure twice, back to back, per grab. Two identical fetches in flight
// together can also land out of order, in which case the older payload
// overwrites the newer one.
//
// Import-free on purpose: tests/test_ui_coalesce.py drives it in node.

// The default burst window is the next macrotask. Two WebSocket messages
// delivered in one network read are ALREADY queued as tasks when the first one
// runs, so a task hop is enough for both to join the same run — there is no
// delay to tune and nothing waits for a timer to expire.
//
// A hidden document runs the trigger straight away instead: Chrome clamps
// background timers to one per second, and to one per minute after five
// minutes hidden, so a minimised window would sit up to a minute stale on its
// next event. Nothing worth coalescing happens where nobody is looking, and
// the in-flight rule below still holds there.
function nextTask(fn) {
  if (typeof document !== "undefined" && document.hidden) fn();
  else setTimeout(fn, 0);
}

// coalesce(run, schedule?) -> request()
//
// Two mechanisms, and neither one alone is enough:
//   * the burst window — triggers landing before the scheduled run starts join
//     it, which is what turns a grab's two events into one refetch;
//   * one in flight at a time — a trigger arriving mid-run queues EXACTLY one
//     follow-up however many arrive, so a long fetch under a fast event stream
//     can never stack requests or let two race each other home.
export function coalesce(run, schedule = nextTask) {
  let scheduled = false;
  let inFlight = false;
  let againWhenDone = false;

  function start() {
    scheduled = false;
    inFlight = true;
    Promise.resolve()
      .then(run)
      .catch(() => {})        // a failed run must never wedge the coalescer
      .then(() => {
        inFlight = false;
        if (againWhenDone) { againWhenDone = false; request(); }
      });
  }

  function request() {
    if (inFlight) { againWhenDone = true; return; }
    if (scheduled) return;
    scheduled = true;
    schedule(start);
  }

  return request;
}
