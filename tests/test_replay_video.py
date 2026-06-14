# tests/test_replay_video.py — pure crop math for the monitor-capture path
from sm64_events.replay.video import crop_bounds, grab_period


def test_grab_period_full_rate_when_active():
    # 60 fps * 2x oversample -> 120 grabs/s
    assert grab_period(120.0, idle=False) == 1.0 / 120.0


def test_grab_period_trickles_when_idle():
    # idle: discarded footage -> a few grabs/s regardless of active rate
    assert grab_period(120.0, idle=True) == 1.0 / 8.0
    assert grab_period(90.0, idle=True, idle_fps=2.0) == 0.5


def test_grab_period_never_divides_by_zero():
    assert grab_period(0.0, idle=False) == 1.0


def test_window_inside_monitor_maps_directly():
    # 4K monitor at origin, window at (263,99)-(2666,2006)
    assert crop_bounds(3840, 2160, (263, 99, 2666, 2006), (0, 0, 3840, 2160)) \
        == (263, 99, 2666, 2006)


def test_secondary_monitor_origin_translates():
    # monitor spans (2560,0)-(5120,1440); window at (2600,100)-(3240,580)
    assert crop_bounds(2560, 1440, (2600, 100, 3240, 580),
                       (2560, 0, 5120, 1440)) == (40, 100, 680, 580)


def test_clamps_to_frame_when_window_hangs_off_edge():
    assert crop_bounds(1920, 1080, (-50, -20, 600, 400), (0, 0, 1920, 1080)) \
        == (0, 0, 600, 400)
    assert crop_bounds(1920, 1080, (1500, 800, 2400, 1300), (0, 0, 1920, 1080)) \
        == (1500, 800, 1920, 1080)


def test_degenerate_visible_region_returns_none():
    # fully off-monitor
    assert crop_bounds(1920, 1080, (3000, 50, 3600, 500), (0, 0, 1920, 1080)) is None
    # sliver thinner than 16 px
    assert crop_bounds(1920, 1080, (1910, 100, 1925, 500), (0, 0, 1920, 1080)) is None
