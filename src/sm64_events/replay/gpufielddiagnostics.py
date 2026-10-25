"""Bounded GPU startup evidence, independent of log tails and resource lifetime.

Only known scalar native receipts are parsed. No raw stderr, paths, pictures or
ROM data enters the retained record or automatic error collector.
"""
from __future__ import annotations

import re
import struct

STARTUP_OFFSET = 336
STARTUP = struct.Struct("<8s10I2Q80s128s96s80s")
# gpu_startup_context_v1, appended after the V1 receipt (plugin/gfxwrap/gpu_startup.h).
STARTUP_CONTEXT_OFFSET = 784
STARTUP_CONTEXT = struct.Struct("<8s10I80s128s96s")
_BINDINGS_KEYS = ("bindings_site", "bindings_gl_error", "bindings_flags", "bindings_invalidations",
                  "bindings_after_ms", "bindings_contexts")
# Receipt fields automatic reports may carry; the privacy allowlists still
# decide what leaves the machine.
STARTUP_TEXT_KEYS = ("gl_vendor", "gl_renderer", "gl_version", "missing_entry_point",
                     "context_gl_vendor", "context_gl_renderer", "context_gl_version", "context_identity",
                     "bindings_site_name", "bindings_cause")
STARTUP_NUMBER_KEYS = _BINDINGS_KEYS
_OPEN = re.compile(
    r"event=encoder_open_failure native_result=([0-9]{1,10}) stage=([a-z_]+) "
    r"encoder_state=([0-9]{1,10}|unknown) encoder_error=([0-9]{1,10}|unknown) "
    r"nv_status=([0-9]{1,10}|unknown) hresult=([0-9]{1,10}|unknown)"
)
_STAGES = frozenset({"adapter", "device_interface", "input_format", "shared_texture",
                     "texture_format", "keyed_mutex", "completion_query", "encoder_prepare"})
_MF = re.compile(r"SM64_GPU_MF operation=([A-Za-z0-9_:.,()><*& \[\]-]{1,180}) hresult=([0-9a-fA-F]{8})")


def startup_context_receipt(raw: bytes) -> dict | None:
    """The appended context receipt; None from an older producer.

    The renderer's GL identity names the GPU even when no surface qualified.
    The bindings cause is present only after a no-surface deadline whose
    latest refusal was BINDINGS, from a renderer that reports one.
    """
    from sm64_events.replay.gpudiagnostics import bindings_cause, bindings_site_name, context_identity_source

    if len(raw) != STARTUP_CONTEXT.size:
        return None
    magic, version, size, epoch, source, *rest = STARTUP_CONTEXT.unpack(raw)
    numbers, strings = rest[:6], rest[6:]
    if magic != b"SM64GPC1" or version != 1 or size != STARTUP_CONTEXT.size:
        return None
    result = dict(epoch=epoch)
    if context_identity_source(source):
        result["context_identity"] = context_identity_source(source)
        result.update((name, value.split(b"\0", 1)[0].decode("ascii", "replace"))
                      for name, value in zip(("context_gl_vendor", "context_gl_renderer", "context_gl_version"),
                                             strings, strict=True))
    site, error, flags = numbers[:3]
    if flags & 1:
        result.update(zip(_BINDINGS_KEYS, numbers, strict=True))
        result["bindings_cause"] = bindings_cause(site, error, flags)
        if site:
            result["bindings_site_name"] = bindings_site_name(site)
    return result


def startup_receipt(raw: bytes, context: bytes | None = None) -> dict | None:
    """Decode an optional producer extension. Missing/unknown stays unknown.

    `context` is the appended context receipt; it joins only the same epoch.
    """
    if len(raw) != STARTUP.size:
        return None
    magic, version, size, epoch, error, width, height, available, high, low, generation, nonce_lo, nonce_hi, *strings = STARTUP.unpack(raw)
    if magic != b"SM64GPU1" or version != 1 or size != STARTUP.size or available not in (0, 1):
        return None
    result = dict(epoch=epoch, load_error=error, width=width, height=height,
                  control_generation=generation, nonce=struct.pack("<QQ", nonce_lo, nonce_hi).hex())
    result.update((name, value.split(b"\0", 1)[0].decode("utf-8", "replace"))
                  for name, value in zip(("gl_vendor", "gl_renderer", "gl_version", "missing_entry_point"), strings, strict=True))
    result["adapter_luid"] = [high - (1 << 32) if high >= 1 << 31 else high, low] if available else None
    extra = startup_context_receipt(context) if context is not None else None
    if extra and extra.pop("epoch") == epoch:
        result.update(extra)
    return result


def native_failure_receipt(stderr: object) -> dict:
    """Known bounded messages only; no arbitrary helper text is returned."""
    if not isinstance(stderr, bytes):
        return {}
    result = {}
    pending_mf = {}
    for line in stderr[-8192:].decode("ascii", "replace").splitlines()[-40:]:
        match = _OPEN.fullmatch(line)
        if match and match[2] in _STAGES:
            values = match.groups()
            numbers = [None if value == "unknown" else int(value) for value in (values[0], *values[2:])]
            if any(number is not None and number > 0xFFFFFFFF for number in numbers):
                continue
            receipt = dict(pending_mf)
            pending_mf = {}
            if numbers[0] == 16:  # Ordinary AV1 negotiation is not a terminal failure.
                continue
            receipt.update((key, number) for key, number in zip(
                ("native_result", "encoder_state", "encoder_error", "driver_error", "native_hresult"), numbers, strict=True)
                          if number is not None)
            receipt["native_stage"] = values[1]
            result = receipt
        match = _MF.fullmatch(line)
        if match:
            pending_mf = {"native_operation": match[1], "native_hresult": int(match[2], 16)}
    return result or pending_mf


def automatic_context(record: dict) -> dict:
    """Select scalar evidence; privacy scrubbing still runs at collection."""
    context = {key: value for key, value in record.items()
               if key in {"operation", "stage", "reason", "encoder_backend", "codec", "requested_codec", "capture_build",
                          "gpu_vendor_id", "gpu_name", "gpu_driver", "native_result", "encoder_result", "native_reason", "native_stage",
                          "native_operation", "native_hresult", "encoder_error", "driver_error"}
               and isinstance(value, (str, int, bool))}
    startup = record.get("startup") or {}
    context.update((key, startup[key]) for key in STARTUP_TEXT_KEYS if isinstance(startup.get(key), str))
    context.update((key, startup[key]) for key in STARTUP_NUMBER_KEYS if type(startup.get(key)) is int)
    context["component"] = "gpu_recording"
    return context
