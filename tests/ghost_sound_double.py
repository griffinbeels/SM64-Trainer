"""Stand-ins for ghost_sound.EngineHost, importable by the track maker child too (--engine ghost_sound_double:...).

FakeEngine: ticks of 1056 or 1088 frames (as SM64's), an impulse of 10000 x gain at each cued tick's
first sample. PositionEngine: every tick's whole length carries its camera-relative x and z and its
gain, so a track shows where each tick sounded from. FailingEngine refuses every ghost.
"""
import numpy as np


class FakeEngine:
    def __init__(self, _rom=None, fail=False):
        self.calls, self.fail = [], fail

    def lengths(self, ticks):
        return np.array([1088 if k % 3 else 1056 for k in range(len(ticks))], np.int64)

    def render(self, ticks):
        self.calls.append(ticks)
        if self.fail:
            raise RuntimeError("the ghost sound engine stopped (exit 3)")
        lengths = self.lengths(ticks)
        pcm = np.zeros((int(lengths.sum()), 2), np.int16)
        starts = np.concatenate([[0], np.cumsum(lengths)])
        for k, (_position, gain, cues) in enumerate(ticks):
            if cues:
                pcm[starts[k]:starts[k] + 8] = int(10000 * gain)
        return lengths, pcm

    def close(self):
        return 0


class PositionEngine(FakeEngine):
    def render(self, ticks):
        lengths = self.lengths(ticks)
        pcm = np.zeros((int(lengths.sum()), 2), np.int16)
        starts = np.concatenate([[0], np.cumsum(lengths)])
        for k, ((x, _y, z), gain, _cues) in enumerate(ticks):
            pcm[starts[k]:starts[k + 1]] = (int(np.clip(x + 1000 * gain, -32768, 32767)), int(np.clip(z, -32768, 32767)))
        return lengths, pcm


class FailingEngine(FakeEngine):
    def __init__(self, _rom=None):
        super().__init__(_rom, fail=True)
