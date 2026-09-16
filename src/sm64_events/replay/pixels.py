"""Owned plugin pixels; expand only pictures the recorder will submit.

The native delivery worker owns the picture bytes. Keep that lifetime boundary:
borrowing a live shared-memory slot would allow the producer to overwrite a
queued picture. The ledger still sees exactly the former top-down BGRA sample,
including alpha and odd edges; its selection policy does not change.
"""
from dataclasses import dataclass

import numpy as np

from sm64_events.core.profiling import measured


def _expand_bgr(top_down: np.ndarray) -> np.ndarray:
    height, width = top_down.shape[:2]
    out = np.empty((height, width, 4), dtype=np.uint8)
    # Channel strides use NumPy's native bulk-copy loop; a three-byte inner
    # slice takes its tiny-copy path for every pixel.
    for channel in range(3):
        out[:, :, channel] = top_down[:, :, channel]
    out[:, :, 3] = 255
    return out


@measured("capture.bgr_to_bgra")
def to_bgra_top_down(pixels_bgr_bottom_up: np.ndarray) -> np.ndarray:
    """A fresh contiguous BGRA picture, safe for retained encoder heartbeats."""
    return _expand_bgr(pixels_bgr_bottom_up[::-1])


class BgrPicture:
    """An already-owned bottom-up slot copy, with lazy encoder preparation.

    This is an explicit capture value, not an ndarray. Its BGRA materialization
    is cached so the ledger's pre-commit preparation and submission share one
    allocation. Neither this value nor a shared slot reaches the encoder queue.
    """
    __slots__ = ("_pixels", "_bgra")

    def __init__(self, owned_bgr_bottom_up: np.ndarray):
        self._pixels = owned_bgr_bottom_up
        self._bgra: np.ndarray | None = None

    @property
    def shape(self) -> tuple[int, int, int]:
        return (*self._pixels.shape[:2], 4)

    def sample_bytes(self, stride: int) -> bytes:
        # Reverse the FULL height before striding, or non-aligned heights
        # sample different pixels. Expand only this small selection.
        return _expand_bgr(self._pixels[::-1][::stride, ::stride]).tobytes()

    def as_bgra(self) -> np.ndarray:
        if self._bgra is None:
            self._bgra = to_bgra_top_down(self._pixels)
        return self._bgra


@dataclass(frozen=True, slots=True)
class SampledPicture:
    """Owned tiny BGRA selection sample; never a full CPU image or GPU lease.

    The native worker must copy its borrowed output into immutable bytes before
    handing this value off. Original dimensions include the encoder's odd edges.
    A separate owner retains the corresponding GPU texture and source identity.
    """
    width: int
    height: int
    sample: bytes
    stride: int

    def __post_init__(self) -> None:
        if (type(self.width) is not int or type(self.height) is not int
                or not 0 < self.width <= 8192 or not 0 < self.height <= 8192):
            raise ValueError("invalid original sample dimensions")
        if type(self.stride) is not int or self.stride != 8:
            raise ValueError("native GPU sample requires stride 8")
        expected = ((self.width + self.stride - 1) // self.stride
                    * ((self.height + self.stride - 1) // self.stride) * 4)
        if type(self.sample) is not bytes or len(self.sample) != expected:
            raise ValueError("exact immutable BGRA sample bytes required")

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.height, self.width, 4

    def sample_bytes(self, stride: int) -> bytes:
        if stride != self.stride:
            raise ValueError("sample stride does not match original capture")
        return self.sample


def sample_bytes(pixels, stride: int) -> bytes:
    if isinstance(pixels, (BgrPicture, SampledPicture)):
        return pixels.sample_bytes(stride)
    return pixels[::stride, ::stride].tobytes()


def as_bgra(pixels: np.ndarray | BgrPicture) -> np.ndarray:
    if isinstance(pixels, SampledPicture):
        raise TypeError("GPU selection sample has no full CPU image")
    return pixels.as_bgra() if isinstance(pixels, BgrPicture) else pixels
