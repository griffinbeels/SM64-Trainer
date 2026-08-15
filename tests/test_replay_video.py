# tests/test_replay_video.py — pure grab-pacing math for the capture path
from sm64_events.replay.video import grab_period


def test_grab_period_full_rate_when_active():
    # 60 fps * 2x oversample -> 120 grabs/s
    assert grab_period(120.0, idle=False) == 1.0 / 120.0


def test_grab_period_trickles_when_idle():
    # idle: discarded footage -> a few grabs/s regardless of active rate
    assert grab_period(120.0, idle=True) == 1.0 / 8.0
    assert grab_period(90.0, idle=True, idle_fps=2.0) == 0.5


def test_grab_period_never_divides_by_zero():
    assert grab_period(0.0, idle=False) == 1.0
