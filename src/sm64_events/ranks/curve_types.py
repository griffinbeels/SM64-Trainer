"""Wire contract for a resolved, frame-aware Overall scoring curve.

Statistical fitting belongs to overall.py; Python and browser evaluators consume
this value without seeing observations. Legacy curves retain exact custom-ladder
behavior. Version 1 PCHIP nodes are [displayed centiseconds, score], ordered from
fastest to slowest, with strictly increasing times and decreasing scores.
"""
from typing import Any, Literal, TypedDict


class CompiledCurve(TypedDict):
    schema_version: Literal[1]
    interpolation: Literal["pchip", "legacy"]
    nodes: list[list[float]]
    ladder_cs: dict[str, int]
    metadata: dict[str, Any]
