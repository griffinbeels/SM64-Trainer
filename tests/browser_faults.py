"""Observe real page faults while retiring exact requests on test-owned navigation."""


def watch(page):
    """Return fault lists and attach the test's explicit request-retirement callback."""
    seen = {"errors": [], "failed": []}
    pending, retiring = set(), set()

    def finished(request):
        pending.discard(request)
        retiring.discard(request)

    def failed(request):
        cancelled = (request in retiring and request.failure == "net::ERR_ABORTED"
                     and (request.method == "GET" or
                          request.method == "POST" and request.url.split("?")[0].endswith("/api/uilog")))
        if not cancelled:
            seen["failed"].append(("failed", request.url, request.failure))
        finished(request)

    # Only the test can retire a document. A same-document abort, a real
    # network failure, or an interrupted business write must still fail.
    page._retire_observed_requests = lambda: retiring.update(pending)
    page.on("request", lambda request: pending.add(request))
    page.on("requestfinished", finished)
    page.on("pageerror", lambda error: seen["errors"].append(str(error)))
    page.on("console", lambda message: seen["errors"].append(message.text) if message.type == "error" else None)
    page.on("response", lambda response: seen["failed"].append((response.status, response.url))
            if response.status >= 400 else None)
    page.on("requestfailed", failed)
    return seen


def no_faults(seen):
    assert seen["errors"] == [], seen["errors"]
    assert seen["failed"] == [], seen["failed"]
