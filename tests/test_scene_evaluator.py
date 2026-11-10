"""The free-camera evaluator's state assembly (tools/scene_lab/evaluator.py SceneImages) on synthetic data.

A recorded state arrives as tap.SceneState's arrays: Project64's word-swapped RDRAM with the 0x5A
pattern where nothing was captured, and a known mask. The evaluator runs the game's code on the
CPU's big-endian RDRAM with the ROM's code images filling what was not captured and the known mask
widened by those images. The drivers before SceneImages built that as
np.where(fill & ~known, image, big-endian RDRAM); that formula is the expected value. The evaluator
itself needs Unicorn and the real ROM: e2_georender.py from-package proves it on a live recording.
No private RAM image or real ROM is involved here.
"""
import importlib.util
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scene_evaluator", ROOT / "tools/scene_lab/evaluator.py")
evaluator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = evaluator
spec.loader.exec_module(evaluator)

RDRAM = 8 << 20


def _mask(rng: random.Random, spans: int, unit: int) -> np.ndarray:
    mask = np.zeros(RDRAM, bool)
    for _ in range(spans):
        begin = rng.randrange(0, RDRAM - 0x8000, unit)
        mask[begin:begin + rng.randrange(unit, 0x8000, unit)] = True
    return mask


def _state(seed: int):
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    known = _mask(rng, 400, 4)                       # captured spans are whole words
    ram = np.where(known, nrng.integers(0, 256, RDRAM, np.uint8), np.uint8(0x5A))
    image_known = _mask(rng, 3, 4)                   # the code segments
    image = np.where(image_known, nrng.integers(0, 256, RDRAM, np.uint8), np.uint8(0))
    fill = image_known | _mask(rng, 1, 1)            # plus a code window that is not data
    image = np.where(fill, nrng.integers(0, 256, RDRAM, np.uint8), image)
    return ram, known, image, image_known, fill


def test_a_state_assembles_as_the_drivers_built_it():
    for seed in (1, 2):
        ram, known, image, image_known, fill = _state(seed)
        ram_be = np.frombuffer(np.frombuffer(ram.tobytes(), "<u4").astype(">u4").tobytes(), np.uint8)
        expected = np.where(fill & ~known, image, ram_be).tobytes()
        inputs = ram.copy(), known.copy()
        assembled, widened = evaluator.SceneImages(image, image_known, fill).assemble(ram, known)
        assert assembled == expected
        assert np.array_equal(widened, known | image_known)
        assert np.array_equal(ram, inputs[0]) and np.array_equal(known, inputs[1])   # inputs untouched


def test_nothing_captured_leaves_the_pattern_and_the_code_images():
    ram, known, image, image_known, fill = _state(3)
    nothing = np.zeros(RDRAM, bool)
    assembled, widened = evaluator.SceneImages(image, image_known, fill).assemble(np.full(RDRAM, 0x5A, np.uint8),
                                                                              nothing)
    assembled = np.frombuffer(assembled, np.uint8)
    assert np.array_equal(assembled[fill], image[fill]) and (assembled[~fill] == 0x5A).all()
    assert np.array_equal(widened, image_known)
