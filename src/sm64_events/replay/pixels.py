"""The picture ledger's selection sample: a desktop grab's BGRA array, or
the tiny BGRA sample the GPU route's native worker copies out for a picture
whose full image stays on the GPU.
"""
from dataclasses import dataclass


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
    if isinstance(pixels, SampledPicture):
        return pixels.sample_bytes(stride)
    return pixels[::stride, ::stride].tobytes()
