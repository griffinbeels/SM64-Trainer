"""The ghost copy lane over a fake twin channel and a fake twin helper.

The helper answers each command the way process_worker does: Submit holds the
slot's key until a Poll returns it, Repeat re-encodes the retained picture,
and a busy slot answers PENDING. The channel stands in for the producer's twin
block. No process, GPU or file is touched.
"""

from collections import deque
from dataclasses import replace
import logging
from types import SimpleNamespace as NS

import pytest

from sm64_events.replay import gpucapture_session, gpuchannel as G
from sm64_events.replay.ghostcopy import RELEASE_S, GhostCopyLane
from sm64_events.replay.gpucapture_session import CaptureSession
from sm64_events.replay.gpuencoder import Result
from sm64_events.replay.gpuprocess.process_controller import Refused, Reply
from sm64_events.replay.gpupublication import PublicationWriteError
from sm64_events.replay.gpusettings import GpuSettings
from sm64_events.replay.media import MediaRun

from test_gpumedia import FakeMux, complete, media as clean_media, sample, stamp

OPEN = dict(op="Open", dll_path="C:/staged/SM64GpuEncoderV1.dll", adapter_luid=[0, 7],
            names=["Local\\clean-0", "Local\\clean-1"],
            options=dict(codec=1, width=640, height=480))
TWINS = ("Local\\SM64GpuImage-copy-0", "Local\\SM64GpuImage-copy-1")


class Clock:
    def __init__(self, now=100.0, step=0.0):
        self.now, self.step = now, step

    def __call__(self):
        self.now += self.step
        return self.now


class Helper:
    """The twin encoder helper behind its controller, answering at once."""

    def __init__(self, *, keyframes=True, open_result=0):
        self.commands = []
        self.ready = deque()
        self.next = 1
        self.held = {}
        self.generation = self.selected = 0
        self.fault = None
        self.done = False
        self.stops = []
        self.keyframes, self.open_result = keyframes, open_result

    def enqueue(self, command):
        if self.fault or self.done:
            return Refused("closed")
        request, self.next = self.next, self.next + 1
        self.commands.append(dict(command))
        self.ready.append(self._answer(request, dict(command)))
        return request

    def _answer(self, request, command):
        op, result, packet, payload, returns = command["op"], 0, None, b"", []
        if op == "Open":
            result = self.open_result
        elif op == "Submit":
            slot = command["slot"]
            if slot in self.held:
                result = Result.PENDING
            else:
                self.held[slot] = dict(slot=slot, bridge_token=command["bridge_token"],
                                       serial=command["serial"])
                self.generation += 1
                self.selected = command["serial"]
                key = command["force_idr"] and self.keyframes
                packet, payload = self._packet(command, key)
        elif op == "Repeat":
            assert command["generation"] == self.generation
            packet, payload = self._packet(command, False)
        elif op == "Poll":
            returns = list(self.held.values())
            self.held.clear()
        elif op == "Close":
            self.done = True
        retained = dict(struct_size=56, version=1, valid=int(self.generation > 0),
                        reserved=0, generation=self.generation,
                        selected_serial=self.selected, source_copies=0, repeats=0,
                        last_serial=0)
        return Reply(request, dict(result=int(result), error=None,
                                   worker_disposal_required=False, retained=retained,
                                   key_returns=returns, packet=packet), payload)

    @staticmethod
    def _packet(command, key):
        payload = f"{command['op']}-{command['serial']}".encode()
        return dict(serial=command["serial"], pts=command["pts"],
                    encoder_duration=command["encoder_duration"], keyframe=key,
                    bytes=len(payload)), payload

    def take_result(self):
        return self.ready.popleft() if self.ready else None

    def status(self):
        proved = dict(empty=True, handle_closed=True, active_processes=0)
        return dict(fault=self.fault, done=self.done, pending_count=len(self.ready),
                    disposal=proved if self.done else None)

    def stop(self, reason):
        self.stops.append(reason)
        self.done = True

    def ops(self):
        return [c["op"] for c in self.commands]


class Channel:
    """The producer's twin block: status, bridges with credits, receipts."""

    twin_texture_names = TWINS

    def __init__(self, log=None):
        self.log = [] if log is None else log
        self.value = G.TwinStatus(G.TWIN_OFF, 0, 0, 0, 0, 0)
        self.published = deque()
        self.credits = {}
        self.token = 0
        self.ended = None
        self.header = NS(even_width=640, even_height=480)

    def twin_request(self, lane):
        self.log.append(("request", lane))

    def twin_release(self):
        self.log.append(("release",))

    def twin_status(self):
        return self.value

    def twin_bridges(self):
        out = list(self.published)
        self.published.clear()
        return out

    def acknowledge_twin_metadata(self, bridge):
        assert self.credits.pop(bridge.texture_index) == bridge
        self.log.append(("receipt", bridge.token))

    def status(self):
        if self.ended:
            raise self.ended
        return NS()

    # producer side
    def set(self, state, lane=1, resolved=0, reason=0):
        self.value = G.TwinStatus(state, reason, lane, resolved, 0, 0)

    def bridge(self, pts, duration=3000):
        slot = next(s for s in (0, 1) if s not in self.credits)
        self.token += 1
        b = G.Bridge(slot, self.token, self.token, self.token, pts, duration, False)
        self.credits[slot] = b
        self.published.append(b)
        return b


class Sink:
    def __init__(self, codec, width, height, log):
        self.shape = (codec, width, height)
        self.calls, self.log = [], log
        self.ready, self.error = True, None

    def check(self):
        if self.error:
            raise PublicationWriteError(self.error)

    def bind(self, run):
        self.calls.append(("bind", run))

    def publish_picture(self, picture, row_ts, wrote_at, *, media_run, pts, source_id,
                        repeat):
        assert picture.pts == pts and media_run is self.calls[0][1]
        self.calls.append(("video", picture, source_id, repeat, wrote_at))

    def write_pcm(self, data, first_us):
        self.calls.append(("pcm", data, first_us))

    def close(self):
        self.calls.append(("close",))

    def abort(self):
        self.calls.append(("abort",))

    def finish(self, error, *, timeout):
        self.calls.append(("finish", error))
        self.log.append(("sink-finish",))

    def status(self):
        return {"error": self.error}

    def videos(self):
        return [c[1] for c in self.calls if c[0] == "video"]


class Owner:
    def __init__(self, log):
        self.wanted, self.timeline, self.sinks, self.log = True, None, [], log

    def ghost_copy_wanted(self):
        return self.wanted

    def ghost_copy_sink(self, codec, width, height):
        self.sinks.append(Sink(codec, width, height, self.log))
        return self.sinks[-1]

    def ghost_timeline(self):
        return self.timeline


def build(*, helper=None, settings=None, clock=None, mix=None, lane=1):
    log = []
    helper = helper or Helper()
    channel, owner = Channel(log), Owner(log)
    media = NS(follower=None, run=MediaRun("clean-run", 1000.25))
    launched = []

    def factory(**kwargs):
        launched.append(kwargs)
        return helper

    extra = {} if mix is None else dict(mix=mix)
    lane = GhostCopyLane(
        owner, channel, media, OPEN, codec="h264", width=640, height=480,
        settings=settings or GpuSettings(), lane=lane, controller_factory=factory,
        launch=lambda: (["C:/python.exe", "-m", "worker"], {"entry": "w"}, {"E": "1"}),
        clock=clock or Clock(), **extra,
    )
    return NS(lane=lane, helper=helper, channel=channel, owner=owner, media=media,
              launched=launched, log=log)


def started(**kwargs):
    t = build(**kwargs)
    t.lane.start(t.lane.clock())
    for _ in range(3):
        t.lane.tick(t.lane.clock())
    assert t.lane.phase == "requested" and t.media.follower is t.lane
    t.channel.set(G.TWIN_ACTIVE)
    t.sink = t.owner.sinks[0]
    return t


def picture(t, pts, duration=3000, *, bridged=True, repeat=False, ticks=1):
    """The clean order: the producer publishes the twin bridge, the clean
    packet lands, GpuMedia follows it, then the lane ticks."""
    if bridged:
        t.channel.bridge(pts)
    t.lane.tick(t.lane.clock())
    t.lane.video(pts, duration, repeat)
    for _ in range(ticks):
        t.lane.tick(t.lane.clock())


def drive(t, ticks=4):
    for _ in range(ticks):
        t.lane.tick(t.lane.clock())


def test_twin_helper_repeats_the_clean_open_with_the_twin_textures():
    t = started()
    (launch,) = t.launched
    assert launch["command"] == ["C:/python.exe", "-m", "worker"]
    assert launch["limits"] == GpuSettings().helper()
    assert t.helper.commands[0] == dict(OPEN, names=list(TWINS))
    assert t.sink.shape == ("h264", 640, 480)
    assert t.log == [("request", 1)]


def test_every_followed_picture_gets_one_twin_packet_with_clean_pts_and_duration():
    t = started()
    clean = [(90000, 3000), (93000, 3003), (96003, 2997), (99000, 6000), (105000, 3000)]
    for pts, duration in clean:
        picture(t, pts, duration, ticks=3)
    drive(t)
    videos = t.sink.videos()
    assert [(v.pts, v.duration) for v in videos] == clean
    assert [v.occurrence for v in videos] == [1, 2, 3, 4, 5]
    assert [v.key for v in videos] == [True, False, False, False, False]
    submits = [c for c in t.helper.commands if c["op"] == "Submit"]
    assert [(c["pts"], c["encoder_duration"], c["force_idr"]) for c in submits] == [
        (p, d, p == 90000) for p, d in clean]
    (bind,) = [c for c in t.sink.calls if c[0] == "bind"]
    run = bind[1]
    assert run.origin_ts == t.media.run.origin_ts and run.id != t.media.run.id
    assert [c[2] for c in t.sink.calls if c[0] == "video"] == [
        f"ghostcopy:{p}" for p, _ in clean]
    # Every twin key came back and was receipted, in bridge order.
    assert [e[1] for e in t.log if e[0] == "receipt"] == [1, 2, 3, 4, 5]
    assert not t.channel.credits


def test_a_missed_picture_repeats_once_the_producer_resolves_it():
    t = started()
    picture(t, 90000, ticks=3)
    picture(t, 93000, bridged=False, ticks=3)
    assert t.helper.ops().count("Repeat") == 0  # unresolved: it waits
    t.channel.set(G.TWIN_ACTIVE, resolved=93000)
    drive(t)
    repeat = [c for c in t.helper.commands if c["op"] == "Repeat"]
    assert repeat == [dict(op="Repeat", generation=1, serial=2, pts=93000,
                           encoder_duration=3000, force_idr=False)]
    second = [c for c in t.sink.calls if c[0] == "video"][1]
    assert second[1].pts == 93000 and second[3] is True
    assert t.lane.counts["missed"] == 1


def test_a_clean_repeat_is_mirrored_without_a_bridge():
    t = started()
    picture(t, 90000, ticks=3)
    picture(t, 120000, 30000, bridged=False, repeat=True, ticks=2)
    picture(t, 150000, ticks=3)
    assert [(v.pts, v.duration) for v in t.sink.videos()] == [
        (90000, 3000), (120000, 30000), (150000, 3000)]
    assert [c["op"] for c in t.helper.commands if c["op"] in ("Submit", "Repeat")] == [
        "Submit", "Repeat", "Submit"]
    assert t.lane.counts == dict(submitted=2, repeated=1, missed=0, published=3, dropped=0)


def test_coverage_starts_at_the_first_bridge_and_pcm_follows_its_picture():
    t = started()
    t.lane.video(87000, 3000, False)   # before any twin bridge: dropped
    t.lane.pcm(b"\x01\x00" * 8, 1_000_000)
    t.channel.bridge(90000)
    drive(t, 1)
    t.lane.video(88000, 2000, False)  # followed after the bridge, but earlier
    t.lane.pcm(b"\x02\x00" * 8, 1_001_000)  # still before the first picture
    t.lane.video(90000, 3000, False)
    t.lane.pcm(b"\x03\x00" * 8, 1_002_000)
    t.lane.pcm(b"\x04\x00" * 8, 1_003_000)
    picture(t, 93000, ticks=3)
    kinds = [(c[0], c[1].pts if c[0] == "video" else c[2]) for c in t.sink.calls
             if c[0] in ("video", "pcm")]
    assert kinds == [("video", 90000), ("pcm", 1_002_000), ("pcm", 1_003_000),
                     ("video", 93000)]
    assert t.lane.counts["dropped"] == 4


def test_a_first_bridge_whose_picture_never_comes_stops_the_lane():
    t = started()
    t.channel.bridge(90000)
    drive(t, 1)
    t.lane.video(93000, 3000, False)  # 90000 was never followed
    assert t.media.follower is t.lane  # stopped by the tick, not detached
    with pytest.raises(RuntimeError, match="never followed"):
        drive(t, 1)


def test_pcm_carries_the_ghost_sound_the_owner_timeline_heard():
    taken, mixed = [], []

    def mix(game, ghost):
        mixed.append((game, ghost))
        return b"M" * len(game)

    t = started(mix=mix)
    t.owner.timeline = NS(take=lambda first_us, frames: taken.append((first_us, frames))
                          or "ghost")
    t.channel.bridge(90000)
    drive(t, 1)
    t.lane.video(90000, 3000, False)
    t.lane.pcm(b"\x05\x00" * 8, 2_000_000)
    drive(t, 3)
    assert taken == [(2_000_000, 4)] and mixed == [(b"\x05\x00" * 8, "ghost")]
    assert ("pcm", b"M" * 16, 2_000_000) in t.sink.calls


def test_the_first_twin_packet_must_be_a_keyframe():
    t = started(helper=Helper(keyframes=False))
    t.channel.bridge(90000)
    t.lane.video(90000, 3000, False)
    with pytest.raises(RuntimeError, match="keyframe"):
        drive(t)
    t.lane.stop("keyframe")
    t.channel.set(G.TWIN_STOPPED)
    drive(t, 8)
    assert t.lane.stopped and not t.sink.videos()
    assert ("abort",) in t.sink.calls  # nothing bound: nothing kept


@pytest.mark.parametrize("bound", ["age", "bytes"])
def test_falling_behind_either_bound_stops_the_lane(bound):
    settings = (replace(GpuSettings(), max_age=0.5) if bound == "age"
                else replace(GpuSettings(), pending_bytes=4096))
    clock = Clock()
    t = started(settings=settings, clock=clock)
    picture(t, 90000, ticks=3)
    t.lane.video(93000, 3000, False)  # its bridge never comes, nor a resolution
    if bound == "bytes":
        t.lane.pcm(b"\x00" * 4096, 3_000_000)
    else:
        clock.now += 0.6
    with pytest.raises(RuntimeError, match="behind|budget"):
        drive(t, 1)


def test_a_codec_refusal_ends_the_lane_without_retry():
    t = build(helper=Helper(open_result=Result.CODEC))
    t.lane.start(t.lane.clock())
    with pytest.raises(RuntimeError, match="refused a second h264 encoder"):
        drive(t, 1)
    t.lane.stop("codec")
    drive(t, 8)
    assert t.lane.stopped and t.helper.ops() == ["Open", "Close"]
    assert not t.log  # never requested, so never released


def test_a_producer_stop_names_its_reason():
    t = started()
    t.channel.set(G.TWIN_STOPPED, reason=10025)
    with pytest.raises(RuntimeError, match="ghost copy bridge failed"):
        drive(t, 1)


def test_stop_releases_drains_keys_waits_for_stopped_then_closes_helper_and_sink():
    t = started()
    picture(t, 90000, ticks=1)          # Submit answered; its key is still held
    t.channel.bridge(93000)             # published, never followed
    drive(t, 1)
    assert t.channel.credits and "Close" not in t.helper.ops()
    t.lane.stop("Ghosts in recording was switched off")
    assert t.media.follower is None
    drive(t, 6)
    # Keys came home (the unfollowed bridge was submitted only to return its
    # key), but the helper stays open until the producer says STOPPED.
    assert not t.channel.credits and "Close" not in t.helper.ops()
    assert t.lane.phase == "releasing"
    t.channel.set(G.TWIN_STOPPED)
    drive(t, 6)
    assert t.lane.stopped and t.helper.ops()[-1] == "Close"
    order = [e[0] for e in t.log]
    assert order.index("release") < order.index("receipt") < order.index("sink-finish")
    assert t.sink.calls[-2:] == [("close",), ("finish", None)]
    assert [v.pts for v in t.sink.videos()] == [90000]  # the discarded one is not kept


def test_stop_stops_waiting_after_its_bound_or_when_the_clean_channel_ends():
    clock = Clock()
    t = started(clock=clock)
    t.lane.stop("off")
    drive(t, 3)
    assert t.lane.phase == "releasing"
    clock.now += RELEASE_S + 0.01
    drive(t, 6)
    assert t.lane.stopped and "Close" in t.helper.ops()

    t = started()
    t.lane.stop("off")
    t.channel.ended = G.ChannelStopped(G.CLOSED, 0)
    drive(t, 6)
    assert t.lane.stopped


def session_over(t, monkeypatch, actions=None):
    monkeypatch.setattr(gpucapture_session, "helper_launch",
                        lambda: (["C:/python.exe"], {}, {}))
    owner = t.owner
    owner.settings = GpuSettings()
    session = CaptureSession(owner, NS(request_stop=lambda reason: None),
                             controller_factory=lambda **kwargs: t.helper)
    session.channel, session.media = t.channel, t.media
    session.open_command, session.codec = OPEN, "h264"
    return session


def test_session_starts_a_lane_on_each_switch_edge_and_never_retries_a_fault(monkeypatch):
    t = build()
    session = session_over(t, monkeypatch)
    session._tick_copy_lane(1.0)
    first = session.copy_lane
    assert first.lane == 1 and first.phase == "opening"
    for _ in range(3):
        session._tick_copy_lane(1.0)
    assert t.media.follower is first
    t.owner.wanted = False
    for _ in range(8):
        session._tick_copy_lane(1.0)
    assert first.reason == "Ghosts in recording was switched off"
    t.channel.set(G.TWIN_STOPPED)
    for _ in range(8):
        session._tick_copy_lane(1.0)
    assert first.stopped and session.copy_lane is first
    t.helper.done = False
    t.owner.wanted = True
    session._tick_copy_lane(1.0)
    second = session.copy_lane
    assert second is not first and second.lane == 2
    # A fault while the switch stays on is never retried in this session.
    second.stop("fault")
    for _ in range(12):
        session._tick_copy_lane(1.0)
    assert second.stopped and session.copy_lane is second


def test_a_lane_fault_leaves_the_clean_tick_running(monkeypatch, caplog):
    t = build()
    session = session_over(t, monkeypatch)
    calls = []
    session.output = NS(check=lambda: calls.append("check"))
    session.audio = NS(drain=lambda: calls.append("audio"))
    session.adapter = NS(pump=lambda now: calls.append("pump"), frontier=None)
    session.media = t.media
    t.media.order = NS(last_selected_pts=None)
    t.media.drain = lambda: calls.append("drain")
    t.media.check_age = lambda now: calls.append("age")
    t.owner.report = lambda s: calls.append("report")
    session.tick()  # the edge launches the lane
    lane = session.copy_lane

    def broken(now):
        raise ValueError("twin bridge fields")

    lane.tick = broken
    with caplog.at_level(logging.INFO, "sm64.replay"):
        session.tick()
        session.tick()
    assert calls.count("report") == 3 and session.error is None
    assert lane.reason == "twin bridge fields"


def test_a_follower_exception_detaches_only_the_follower(caplog):
    mux = FakeMux()
    q = clean_media(mux=mux)
    seen = []

    class Follower:
        def video(self, pts, duration, repeat):
            seen.append(("video", pts))
            raise RuntimeError("follower bug")

        def pcm(self, data, first_us):
            seen.append(("pcm", first_us))

    q.follower = Follower()
    with caplog.at_level(logging.ERROR, "sm64.replay"):
        a = q.offer(sample(1), stamp(10), occurrence=1, capture_ts=1000, now=0)
        complete(q, a)
        b = q.offer(sample(2), stamp(11), occurrence=2, capture_ts=1000.033, now=0.033)
        complete(q, b)
        c = q.offer(sample(3), stamp(12), occurrence=3, capture_ts=1000.066, now=0.066)
        complete(q, c)
    assert q.follower is None and q.fault is None and not q.closed
    assert seen == [("video", 0)]
    assert [p.pts for p in mux.video] == [0, 2970]
    assert len([r for r in caplog.records if "follower failed" in r.message]) == 1


def test_the_follower_sees_the_clean_order_exactly():
    order = []

    class Mux(FakeMux):
        def write_video(self, picture):
            super().write_video(picture)
            order.append(("video", picture.pts, picture.duration))

        def write_pcm(self, data, first_us):
            super().write_pcm(data, first_us)
            order.append(("pcm", first_us))

    followed = []
    follower = NS(video=lambda p, d, r: followed.append(("video", p, d)),
                  pcm=lambda data, first_us: followed.append(("pcm", first_us)))
    q = clean_media(mux=Mux())
    q.follower = follower
    for i in range(4):
        d = q.offer(sample(i + 1), stamp(10 + i), occurrence=i + 1,
                    capture_ts=1000 + i / 30, now=i / 30)
        complete(q, d)
        q.audio(b"\x00\x01" * 1470, 1000_000_000 + round(i * 1_000_000 / 30), now=i / 30)
    q.frontier(1000.2)
    assert q.seal(1000.2) and q.finish(audio_drained=True)
    assert followed == order and len(order) > 6


def test_session_close_stops_the_lane_before_the_clean_helper_retires(monkeypatch):
    actions = []
    t = build()
    session = session_over(t, monkeypatch)
    session.demand = NS(request_stop=lambda reason: actions.append("revoke"))
    t.owner.end_audio = lambda handoff: actions.append("audio")
    session._tick_copy_lane(1.0)
    lane = session.copy_lane
    lane.close = lambda: actions.append("lane")
    session._close_channel = lambda: actions.append("channel")
    session._finish_helper = lambda: actions.append("helper")
    session._finish_media = lambda: None
    session._finish_output = lambda: None
    session.close()
    assert actions == ["revoke", "audio", "lane", "channel", "helper"]


def test_session_close_drives_a_running_lane_to_stopped(monkeypatch):
    clock = Clock(step=0.01)
    t = started(clock=clock)
    picture(t, 90000, ticks=3)
    t.channel.set(G.TWIN_STOPPED)
    t.lane.close()
    assert t.lane.stopped and t.helper.ops()[-1] == "Close"
    assert t.sink.calls[-2:] == [("close",), ("finish", None)]
