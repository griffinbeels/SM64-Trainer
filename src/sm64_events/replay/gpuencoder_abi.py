import ctypes as C

U = C.c_uint32
I = C.c_int32
Q = C.c_uint64
W = C.c_uint16


class Options(C.Structure):
    _fields_ = (
        [
            (n, U)
            for n in [
                "struct_size",
                "version",
                "width",
                "height",
                "nominal_fps_num",
                "nominal_fps_den",
                "profile",
                "preset",
                "tuning",
                "rate_control",
                "b_frames",
                "cq",
                "max_bitrate",
                "vbv_buffer_bits",
                "gop_frames",
                "initial_qp_p",
                "initial_qp_i",
                "initial_qp_b",
            ]
        ]
        + [("idr_interval_ticks", Q)]
        + [
            (n, U)
            for n in [
                "input_format",
                "signal_color",
                "full_range",
                "matrix",
                "primaries",
                "transfer",
                "max_packet_bytes",
            ]
        ]
        # `codec` takes the FIRST of the five checked-zero reserved words, so
        # the struct keeps its size, its version and every other offset.
        # GBENC_CODEC_H264 is 0 deliberately: a caller that predates AV1 sends
        # a zeroed word and still gets H.264, and an encoder that predates AV1
        # refuses a nonzero one loudly instead of encoding the wrong codec.
        + [("codec", U), ("reserved", U * 4)]
    )


CODEC_H264 = 0
CODEC_AV1 = 1
# libav stream name per native codec; the one place the two vocabularies meet.
CODEC_STREAM_NAMES = {CODEC_H264: "h264", CODEC_AV1: "av1"}


class Packet(C.Structure):
    _fields_ = [
        ("struct_size", U),
        ("version", U),
        ("data", C.POINTER(C.c_uint8)),
        ("bytes", U),
        ("flags", U),
        ("occurrence", Q),
        ("pts", Q),
        ("duration", Q),
    ]


Callback = C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(Packet))


class Sink(C.Structure):
    _fields_ = [
        ("struct_size", U),
        ("version", U),
        ("on_packet", Callback),
        ("user", C.c_void_p),
    ]


Names = (W * 128) * 2


class Config(C.Structure):
    _fields_ = [
        ("struct_size", U),
        ("version", U),
        ("adapter_high", I),
        ("adapter_low", U),
        ("slot_count", U),
        ("reserved", U),
        ("names", Names),
        ("encoder", Options),
        ("sink", Sink),
    ]


class Picture(C.Structure):
    _fields_ = [
        ("struct_size", U),
        ("version", U),
        ("slot", U),
        ("flags", U),
        ("occurrence", Q),
        ("pts", Q),
        ("duration", Q),
    ]


class Status(C.Structure):
    _fields_ = (
        [
            (n, U)
            for n in [
                "struct_size",
                "version",
                "state",
                "failure",
                "hresult",
                "encoder_state",
                "encoder_error",
                "nv_status",
                "held_mask",
                "pending_mask",
                "owner_thread",
            ]
        ]
        + [("adapter_high", I), ("adapter_low", U), ("reserved", U)]
        + [
            (n, Q)
            for n in [
                "submitted",
                "completed",
                "delivered",
                "acquired",
                "released",
                "timeouts",
            ]
        ]
    )


class Abi(C.Structure):
    _fields_ = [
        (n, U)
        for n in [
            "struct_size",
            "version",
            "pointer_bits",
            "config_bytes",
            "picture_bytes",
            "status_bytes",
            "options_bytes",
            "packet_bytes",
            "sink_bytes",
            "reserved",
        ]
    ]


class RetainedAbi(C.Structure):
    _fields_ = [
        (n, U) for n in ["struct_size", "version", "query_bytes", "repeat_bytes"]
    ]


class Retained(C.Structure):
    _fields_ = [(n, U) for n in ["struct_size", "version", "valid", "reserved"]] + [
        (n, Q)
        for n in [
            "generation",
            "selected_serial",
            "source_copies",
            "repeats",
            "last_serial",
        ]
    ]


class Repeat(C.Structure):
    _fields_ = [(n, U) for n in ["struct_size", "version", "flags", "reserved"]] + [
        (n, Q) for n in ["generation", "occurrence", "pts", "duration"]
    ]


SIGNATURES = {
    "Abi": [C.POINTER(Abi)],
    "Open": [C.POINTER(Config), C.POINTER(Q)],
    "Submit": [Q, C.POINTER(Picture)],
    "Poll": [Q],
    "Finish": [Q],
    "Close": [Q, C.POINTER(Status)],
    "Status": [Q, C.POINTER(Status)],
    "RetainedAbi": [C.POINTER(RetainedAbi)],
    "Retained": [Q, C.POINTER(Retained)],
    "Repeat": [Q, C.POINTER(Repeat)],
}


def initialize(cls):
    value = cls()
    value.struct_size = C.sizeof(cls)
    value.version = 1
    return value


def snapshot(value):
    return {name: getattr(value, name) for name, _ in value._fields_}


def bind(lib):
    for name, args in SIGNATURES.items():
        fn = getattr(lib, "SM64GpuEncoder" + name + "V1")
        fn.argtypes = args
        fn.restype = U
    base = initialize(Abi)
    extension = initialize(RetainedAbi)
    if lib.SM64GpuEncoderAbiV1(C.byref(base)) or lib.SM64GpuEncoderRetainedAbiV1(
        C.byref(extension)
    ):
        raise ValueError("encoder ABI query rejected")
    expected = [
        64,
        C.sizeof(Config),
        C.sizeof(Picture),
        C.sizeof(Status),
        C.sizeof(Options),
        C.sizeof(Packet),
        C.sizeof(Sink),
        0,
    ]
    observed = [
        getattr(base, n)
        for n in [
            "pointer_bits",
            "config_bytes",
            "picture_bytes",
            "status_bytes",
            "options_bytes",
            "packet_bytes",
            "sink_bytes",
            "reserved",
        ]
    ]
    if (
        base.struct_size != C.sizeof(Abi)
        or base.version != 1
        or observed != expected
        or extension.struct_size != C.sizeof(RetainedAbi)
        or extension.version != 1
        or extension.query_bytes != C.sizeof(Retained)
        or extension.repeat_bytes != C.sizeof(Repeat)
    ):
        raise ValueError("encoder ABI size/version mismatch")
    if [
        C.sizeof(x)
        for x in [Config, Picture, Status, Options, Packet, Sink, Retained, Repeat]
    ] != [688, 40, 104, 128, 48, 24, 56, 48]:
        raise ValueError("x64 process and exact POD alignment required")
