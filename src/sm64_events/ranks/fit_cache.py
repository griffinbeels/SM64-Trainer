"""Bounded process-local reuse for pure fits, with no shared mutable results."""
from collections import OrderedDict
from copy import deepcopy
import math
from threading import RLock


def frozen_inputs(value):
    """Immutable value key preserving container/numeric types and dict equality.

    Unsupported custom provenance skips reuse rather than coercing two distinct
    values into the same key. In particular, tuple/list and int/float metadata
    must retain their uncached representation and calibration fingerprint.
    """
    kind = type(value)
    if kind is dict:
        return (kind, frozenset((frozen_inputs(key), frozen_inputs(item))
                               for key, item in value.items()))
    if kind in (list, tuple):
        # The resource monitor can retain gc.get_objects() while this runs.
        # CPython cannot resize a generator-built tuple with that extra ref
        # (python/cpython#59313). Publish the tuple from a completed list.
        return (kind, tuple([frozen_inputs(item) for item in value]))
    if kind in (str, int, float, bool, type(None)):
        if kind is float:
            if not math.isfinite(value):
                raise ValueError("nonfinite provenance cannot be a fit cache key")
            return (kind, value.hex())  # Preserve even signed zero in metadata.
        return (kind, value)
    raise TypeError("custom provenance cannot be a fit cache key")


class FitCache:
    """Keep at most maxsize completed fits; concurrent misses may compute twice.

    Computation runs outside the lock, so unrelated targets do not serialize.
    Failed computations never enter the cache. Copies on insertion and retrieval
    keep both the fitting caller and later readers from mutating retained values.
    """

    def __init__(self, maxsize=1024):
        if type(maxsize) is not int or maxsize < 1:
            raise ValueError("fit cache maxsize must be a positive integer")
        self.maxsize = maxsize
        self._values = OrderedDict()
        self._lock = RLock()

    def get_or_compute(self, key, compute):
        with self._lock:
            if key in self._values:
                self._values.move_to_end(key)
                return deepcopy(self._values[key])
        result = compute()
        with self._lock:
            self._values[key] = deepcopy(result)
            self._values.move_to_end(key)
            while len(self._values) > self.maxsize:
                self._values.popitem(last=False)
        return result

    def clear(self):
        """Release retained evidence and results; useful for a cold-fit comparison."""
        with self._lock:
            self._values.clear()

    def __len__(self):
        with self._lock:
            return len(self._values)
