"""An observation before a pause/detach must not become a later occurrence."""
import asyncio
import ast
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events.inputs.sampler import InputSampler
from sm64_events.inputs.store import ChunkWriter
from sm64_events.memory.base import MemoryReadError
from sm64_events.memory.layout import US
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from test_inputs_sampler import ScriptedMemory
from test_inputs_track import AT, LATER
from test_poller import RecordingBroadcaster, ScriptedReader, StubMemory


@pytest.mark.parametrize("boundary", ["pause", "detach", "read_error"])
def test_equal_counter_after_capture_break_keeps_both_inputs(tmp_path, boundary):
    with closing(Database(tmp_path / "boundary.db")) as db:
        session = db.insert_session(AT)
        writer = ChunkWriter(db.inputs, session)
        clock = iter([AT, LATER])
        sampler = InputSampler(ScriptedMemory([(100, 0x8000, 0, 80, 0),
                                               (100, 0x4000, 0, -80, 0)]),
                               US, writer.add, clock=lambda: next(clock))
        poller = Poller(StubMemory(), [], RecordingBroadcaster(), input_sampler=sampler,
                        reader=ScriptedReader([MemoryReadError("detached")]))
        sampler.sample()
        if boundary == "pause":
            poller.set_paused(True)
            poller.set_paused(False)
        elif boundary == "detach":
            # The source is already pending; force the snapshot stage that
            # detects loss without consuming the scripted post-reattach input.
            poller._due_for_a_snapshot = lambda: True
            asyncio.run(poller.tick())
        else:
            class Unreadable:
                def read_u32(self, address):
                    raise MemoryReadError("temporary read outage")
            memory = sampler._memory
            sampler._memory = Unreadable()
            assert sampler.sample() is None
            sampler._memory = memory
        sampler.sample()
        sampler.flush()
        writer.close()
        chunks = db.inputs.chunks_between(AT, LATER)
        assert [(raw, pad.buttons) for c in chunks for raw, pad in c.frames] == [
            (100, 0x8000), (100, 0x4000)]
        assert chunks[0].observations[0].source_id != chunks[1].observations[0].source_id


def test_composed_shutdown_emits_pending_input_before_closing_its_writer():
    # Execute the real wiring block without running build(), which also
    # probes the live emulator and opens its machine-wide frame mapping.
    source = Path(__file__).resolve().parents[1] / "src/sm64_events/main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    block, = [node for node in ast.walk(tree) if isinstance(node, ast.If)
              and ast.unparse(node.test) == "input_writer is not None"]
    calls = []
    poller = SimpleNamespace()
    namespace = {"input_sampler": SimpleNamespace(flush=lambda: calls.append("sampler")),
                 "input_writer": SimpleNamespace(close=lambda: calls.append("writer")),
                 "poller": poller, "service": SimpleNamespace()}
    exec(compile(ast.Module(body=[block], type_ignores=[]), str(source), "exec"), namespace)
    poller.on_stop()
    assert calls == ["sampler", "writer"]


@pytest.mark.parametrize("after, breaks", [(50, True), (102, False)])
def test_only_a_backward_straddle_is_evidence_of_a_reset(after, breaks):
    class StraddlingMemory(ScriptedMemory):
        def read_u32(self, address):
            second = self._second_read
            value = super().read_u32(address)
            return after if second and self.index == 1 else value

    memory = StraddlingMemory([(100, 0x8000, 0, 80, 0),
                               (101, 0, 0, 0, 0), (101, 0x4000, 0, -80, 0)])
    got = []
    sampler = InputSampler(memory, US,
                           lambda number, frame, **metadata: got.append(metadata["observation"]))
    assert sampler.sample() == 100
    assert sampler.sample() is None
    assert sampler.sample() == 101
    sampler.flush()
    first, second = got
    assert (first.source_id != second.source_id) is breaks
