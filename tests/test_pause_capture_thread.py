"""Manual pause must mutate the sampler on its owning event-loop thread."""
import asyncio
import threading

from httpx import ASGITransport, AsyncClient

from sm64_events.server.app import create_app
from sm64_events.server.poller import Poller
from test_poller import RecordingBroadcaster, ScriptedReader, StubMemory


def test_pause_route_runs_on_the_polling_event_loop(tmp_path):
    broadcaster = RecordingBroadcaster()
    poller = Poller(StubMemory(), [], broadcaster, reader=ScriptedReader([]))
    changed_on = []
    original = poller.set_paused

    def record(paused):
        changed_on.append(threading.get_ident())
        original(paused)

    poller.set_paused = record
    app = create_app(poller, broadcaster, library_path=tmp_path / "library.json",
                     library_bundled_path=tmp_path / "absent.json")

    async def request_pause():
        owner = threading.get_ident()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            response = await client.post("/api/pause", json={"paused": True})
            assert response.status_code == 200
        assert poller.paused
        assert changed_on == [owner]

    asyncio.run(request_pause())
