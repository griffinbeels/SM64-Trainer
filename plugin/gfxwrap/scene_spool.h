/* The live spool's writer (M4 round 2): turns the scene tap's ring records
 * into rolling segment files a reader can open while they grow. It runs on
 * the tap's own thread (scene_tap.cpp), never on the emulation thread, and
 * holds no thread of its own, so a bench host can feed it recorded records.
 * Format and evidence: the header comment of scene_spool.cpp. */
#pragma once
#include <windows.h>
#include <stdint.h>

// The tap's record format, shared by the producer (scene_tap.cpp) and this
// writer; tools/scene_lab/tap.py reads it.
namespace scene_record {
constexpr uint32_t kMagic = 0x31544C53;   // "SLT1" little-endian
enum : uint32_t { REC_PAD = 0, REC_TASK = 1, REC_PRESENT = 2, REC_FULL = 3, REC_REGIONS = 4, REC_VI = 5,
                  REC_BOOT = 6, REC_PACKED = 7 };
enum : uint32_t { REGIONS_TASK = 0, REGIONS_LEVEL_KEY = 1, REGIONS_LEVEL_DELTA = 2 };
enum : uint32_t { VI_KEY = 0, VI_DELTA = 1 };
enum : uint32_t { WRITER_KEY = 1 };       // header flag: a key the writer wrote from its own shadow
// lost: the producer's dropped-record count when it committed this record.
struct RecordHeader { uint32_t magic, type, bytes, sequence; int64_t qpc; uint32_t lost, flags; };
static_assert(sizeof(RecordHeader) == 32, "tap record header");
}  // namespace scene_record

namespace scene_spool {

struct Span { uint32_t begin, bytes; };

// What the writer needs from an arming: the delta-coded spans it keeps
// shadows of (to write each segment's keys), and where the identity extras
// live (for the index rows). Spans as the producer settled them.
struct Layout {
    uint32_t rdram_size = 0;
    int64_t qpc_frequency = 1;
    Span level[8] = {};
    uint32_t level_count = 0;
    Span vi[9] = {};               // RDRAM spans, then SP DMEM at 0x04000000 when copied
    uint32_t vi_count = 0;
    Span timer = {}, level_id = {}, area_id = {};   // identity extras (RDRAM addresses), {0, 0} when absent
};

struct Settings {
    uint64_t window_ms = 15 * 60 * 1000;   // closed segments older than this are deleted
    uint64_t max_bytes = 2ull << 30;       // and the oldest closed ones while the session holds more
    uint64_t segment_ms = 15 * 1000;       // a new segment (with keys) at the first task this much later
    uint64_t segment_bytes = 256ull << 20; // or once the open segment holds this much
    uint32_t dedupe = 1;                   // closure runs and task regions against the reference image
    uint32_t compress = 1;                 // XPRESS (ntdll) when the system has it
};

enum Reason : uint32_t {
    CLOSE_ROLLOVER = 1, CLOSE_LEASE_STOPPED = 2, CLOSE_LEASE_LAPSED = 3, CLOSE_ROM_CLOSED = 4,
    CLOSE_WRITE_FAILED = 5, CLOSE_REARMED = 6, CLOSE_UNLOADED = 7, CLOSE_NO_MEMORY = 8,
};

struct Stats {
    uint64_t records = 0, input_bytes = 0, output_bytes = 0, index_bytes = 0, key_bytes = 0;
    uint32_t tasks = 0, presents = 0, refused = 0, lost = 0, holes = 0;
    uint32_t segments = 0, deleted = 0, delete_retries = 0, compressed = 0;
};

// A growable scratch buffer that reports failure instead of throwing: the
// writer lives inside Project64.
struct Buffer {
    uint8_t *data = nullptr;
    size_t size = 0;
    bool reserve(size_t bytes);   // contents are not kept
    void release();
};

class Writer {
public:
    Writer() = default;
    ~Writer();
    Writer(const Writer &) = delete;
    Writer &operator=(const Writer &) = delete;
    // A session folder that already exists; the writer adds boot.bin and
    // seg-NNNNNN.{tap,idx}. False when its buffers cannot be allocated.
    bool begin(const wchar_t *session_dir, const Layout &layout, const Settings &settings);
    // After a lapse: the producer's next level and VI records are keys again
    // and its loss count restarts, so the shadows write no key until then.
    void resume();
    // One tap record (32-byte header, then its body). False after a write failure.
    bool add(const uint8_t *record);
    // Heartbeat row, the newest task's row once 100 ms old, and pruning.
    void tick(int64_t now_qpc);
    void close_segment(uint32_t reason);
    void flush_rows() { flush_task_row(); }   // the newest task's row now (a test host reads right after)
    bool failed() const { return failed_; }
    bool segment_open() const { return open_; }
    uint32_t write_error() const { return error_; }
    const Stats &stats() const { return stats_; }

private:
    struct Segment { uint32_t number; uint64_t bytes; int64_t first_qpc, last_qpc; bool index_gone; };
    struct Pending { bool live, written, shown, refused; uint32_t index, timer, place; int64_t qpc; uint64_t offset; };
    bool open_segment(uint32_t first_task, int64_t qpc);
    bool write_keys(uint32_t task_index, int64_t qpc);
    bool put_packed(const scene_record::RecordHeader &header, const uint8_t *body, uint32_t body_bytes,
                    bool dedupe, uint32_t flags);
    bool row(uint32_t kind, uint32_t a, uint32_t b, uint32_t c, int64_t d, uint64_t e);
    void flush_task_row();
    void note_loss(uint32_t stamp, uint32_t owner, bool is_task, int64_t qpc);
    void apply_shadow(bool level, const uint8_t *body);
    void prune(int64_t now_qpc);
    bool write_all(HANDLE file, const void *bytes, uint32_t n);
    void fail(uint32_t error);
    uint64_t utc_now(int64_t *qpc) const;
    int64_t ticks(uint64_t ms) const { return (int64_t)(ms * (uint64_t)layout_.qpc_frequency / 1000); }

    static constexpr uint32_t kMaxClosed = 4096;
    Layout layout_;
    Settings settings_;
    wchar_t dir_[MAX_PATH] = {};
    HANDLE tap_ = INVALID_HANDLE_VALUE, index_ = INVALID_HANDLE_VALUE;
    Segment current_ = {};
    Segment closed_[kMaxClosed] = {};
    uint32_t closed_count_ = 0;
    uint64_t closed_bytes_ = 0;
    bool open_ = false, failed_ = false, boot_written_ = false, began_ = false;
    uint32_t error_ = 0, next_segment_ = 1;
    // Reference image: the bytes closures and task regions showed since the
    // segment began (zero at its start). A reader rebuilds the same image.
    Buffer ref_;
    // Shadows of the delta-coded spans as last emitted, for segment keys.
    Buffer level_shadow_, vi_shadow_;
    uint32_t level_total_ = 0, vi_total_ = 0, level_segments_[16] = {}, vi_index_ = 0, vi_tasks_ = 0;
    bool level_valid_ = false, vi_valid_ = false;
    Buffer stream_, out_, key_, pieces_;
    void *compress_ = nullptr, *xpress_work_ = nullptr;   // ntdll's RtlCompressBuffer and its workspace
    // The newest task's row waits for the next task (or 100 ms) so the
    // present that shows it can be flagged on it.
    Pending pending_ = {};
    uint32_t lost_seen_ = 0, owner_ = 0, hole_first_ = 0, hole_last_ = 0;
    bool have_owner_ = false, hole_waiting_ = false;
    int64_t last_beat_ = 0, last_prune_ = 0;
    Stats stats_;
};

}  // namespace scene_spool
