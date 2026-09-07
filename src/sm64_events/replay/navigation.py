"""Navigation bounds from validated picture associations, without changing them."""


def captured_input_span(meta: dict) -> list[int] | None:
    """Only newly captured pictures extend the input axis.

    A heartbeat in the pre-buffer can retain a previous attempt's picture
    from minutes ago. Its counter still identifies that picture, but must
    not expand the timeline through all the game frames since its capture.
    Genuine capture holes inside this extent remain holes.
    """
    frames = meta.get("frame_map") or []
    repeats = meta.get("repeats") or []
    captured = [frame for slot, frame in enumerate(frames)
                if frame is not None
                and not (slot < len(repeats) and repeats[slot])]
    return [min(captured), max(captured)] if captured else None


def attempt_start_slot(meta: dict, anchor_frame: int | None) -> int | None:
    """First available picture at the attempt boundary, never an earlier input.

    Missing reset pictures cannot be recovered by seeking. A clip containing
    a counter restart needs occurrence identity before a raw counter can
    locate its start; decline that lookup rather than pick another visit.
    """
    if anchor_frame is None:
        return None
    frames = meta.get("frame_map") or []
    known = [(slot, frame) for slot, frame in enumerate(frames) if frame is not None]
    if any(right[1] < left[1] for left, right in zip(known, known[1:], strict=False)):
        return None
    return next((slot for slot, frame in known if frame >= anchor_frame), None)
