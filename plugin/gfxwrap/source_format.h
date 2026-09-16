/* Raw RGB byte-transfer contract; these names do not request gamma conversion.
 * Alpha is outside the old BGR readback contract and must not affect RGB. */
#pragma once
enum rb_source_format {
    RB_SOURCE_UNKNOWN = 0,
    RB_SOURCE_RGB8_LINEAR = 1,
    RB_SOURCE_RGB8_SRGB = 2
};
