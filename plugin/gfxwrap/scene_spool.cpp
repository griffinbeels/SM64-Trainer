/* The live spool's writer (M4 round 2): the scene tap's records as rolling
 * segment files under <lab>\live\<session>\, each one readable on its own
 * given the ROM, while the newest one grows.
 *
 * A segment is seg-NNNNNN.tap (tap records, the same 32-byte headers as
 * tap.bin) and seg-NNNNNN.idx (32-byte rows, append-only). It starts at a
 * task: first the writer's keys (the level regions and, when copied, the VI
 * spans, whole, from the writer's own shadows of what the producer emitted;
 * header flag WRITER_KEY), then that task's records as the producer made
 * them. A reader decoding from the segment's first byte rebuilds every
 * task's scene state without any earlier segment. Rows:
 *   OPEN  {segment, first task, 0, qpc, utc}      the (qpc, UTC) pair
 *   TASK  {index, global_timer, level | area << 16, qpc, offset | flags << 48}
 *         written when the next task arrives or 100 ms later; flags 1 shown
 *         (a present followed it), 2 refused (the walker refused its list)
 *   SHOWN {index}                                   a present after its row
 *   HOLE  {first, last, records lost so far, qpc}   tasks a full ring made incomplete
 *   BEAT  {lost, refused, tasks, qpc, utc}          every second while open
 *   CLOSE {reason, last task, 0, qpc, bytes}
 * A reader takes whole rows and whole records only: the tail of the open
 * files may be torn mid-write.
 *
 * Disk: a closure or task-region record is PACKED: its RDRAM bytes are cut
 * into pieces at every 256-byte RDRAM boundary, and a piece equal to the
 * reference image (what closures and task regions showed at that address
 * since the segment began, zero at its start) is left out and named by a
 * cleared bit. Level and VI records are already deltas and pass whole. Every
 * packed stream of 256 bytes or more is then compressed with XPRESS through
 * ntdll's RtlCompressBuffer (part of Windows: no vendored code) when it gets
 * smaller. REC_PACKED body: {inner type, inner body bytes, codec (0 stored,
 * 1 XPRESS), stream bytes, payload bytes, pieces, dedupe, 0}, then the
 * payload; the stream is [bitmap, padded to 4][the inner body without the
 * pieces left out]. The disk rate and the writer's cost of each choice, measured
 * on a recorded session: tools/scene_lab/live_spool.py's docstring.
 *
 * Bounds: the writer deletes its own oldest closed segments once older than
 * the window or while the session holds more than the byte cap (a segment a
 * reader holds open is retried later), and opens a new segment every
 * segment_ms or segment_bytes. */
#define NOMINMAX
#define _CRT_SECURE_NO_WARNINGS
#include "scene_spool.h"
#include <stdio.h>
#include <string.h>
#include <wchar.h>

namespace scene_spool {
namespace {

using namespace scene_record;

enum : uint32_t { ROW_OPEN = 1, ROW_TASK = 2, ROW_SHOWN = 3, ROW_HOLE = 4, ROW_BEAT = 5, ROW_CLOSE = 6 };
enum : uint32_t { TASK_SHOWN = 1, TASK_REFUSED = 2 };
enum : uint32_t { CODEC_STORED = 0, CODEC_XPRESS = 1 };
constexpr uint32_t kPackedHead = 32, kBlock = 256, kCompressMin = 256;
constexpr USHORT kFormatXpress = 3;              // COMPRESSION_FORMAT_XPRESS, standard engine
constexpr uint64_t kRowDelayMs = 100, kBeatMs = 1000, kPruneMs = 2000;

typedef LONG(NTAPI *WorkSpaceSizeFn)(USHORT, PULONG, PULONG);
typedef LONG(NTAPI *CompressFn)(USHORT, PUCHAR, ULONG, PUCHAR, ULONG, ULONG, PULONG, PVOID);

uint32_t align32(uint64_t bytes) { return (uint32_t)((bytes + 31) & ~31ull); }
uint32_t word(const uint8_t *at, uint32_t index) { uint32_t v; memcpy(&v, at + 4 * index, 4); return v; }

CompressFn compressor(uint32_t *work_bytes) {
    HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    if (!ntdll) return nullptr;
    auto size = (WorkSpaceSizeFn)(void *)GetProcAddress(ntdll, "RtlGetCompressionWorkSpaceSize");
    auto compress = (CompressFn)(void *)GetProcAddress(ntdll, "RtlCompressBuffer");
    ULONG buffer = 0, fragment = 0;
    if (!size || !compress || size(kFormatXpress, &buffer, &fragment) != 0) return nullptr;
    *work_bytes = buffer;
    return compress;
}

// A game value (big-endian) from a task record's identity extras, which hold
// Project64's word-swapped RDRAM: byte b of the game lives at b ^ 3.
uint32_t identity(const uint8_t *body, Span where) {
    if (!where.bytes) return 0;
    const uint32_t runs = word(body, 5), extras = word(body, 6), closure = word(body, 7);
    const uint8_t *spec = body + 112 + 8ull * runs, *data = spec + 8ull * extras + closure;
    for (uint32_t i = 0; i < extras; ++i) {
        const uint32_t begin = word(spec, 2 * i) & ~3u, bytes = (word(spec, 2 * i + 1) + 3) & ~3u;
        if (where.begin >= begin && where.begin + where.bytes <= begin + bytes) {
            uint32_t value = 0;
            for (uint32_t b = where.begin; b < where.begin + where.bytes; ++b)
                value = value << 8 | data[(b ^ 3) - begin];
            return value;
        }
        data += bytes;
    }
    return 0;
}

// The RDRAM spans a packable record carries: (prefix bytes before its RDRAM
// bytes, spans as {begin, bytes} into `spans`, content bytes); false when the
// record does not describe itself consistently (it is then written unpacked).
bool rdram_spans(const uint8_t *body, uint32_t body_bytes, uint32_t type, uint32_t rdram_size,
                 Buffer &spans, uint32_t *count, uint32_t *prefix, uint32_t *content) {
    const uint8_t *table;
    uint64_t total = 0;
    if (type == REC_TASK) {
        if (body_bytes < 112) return false;
        const uint64_t runs = word(body, 5), extras = word(body, 6);
        *prefix = (uint32_t)(112 + 8 * runs + 8 * extras);
        *content = word(body, 7);
        *count = (uint32_t)runs;
        table = body + 112;
    } else {
        if (body_bytes < 96 || word(body, 1) != REGIONS_TASK) return false;
        *count = word(body, 2);
        *prefix = 96 + 8 * *count;
        *content = word(body, 3);
        table = body + 96;
    }
    if ((uint64_t)*prefix + *content > body_bytes || !spans.reserve(8ull * *count + 8)) return false;
    Span *out = (Span *)spans.data;
    for (uint32_t i = 0; i < *count; ++i) {
        const uint32_t a = word(table, 2 * i), b = word(table, 2 * i + 1);
        const uint32_t bytes = type == REC_TASK ? b - a : b;
        if ((type == REC_TASK && b < a) || (uint64_t)a + bytes > rdram_size) return false;
        out[i] = {a, bytes};
        total += bytes;
    }
    return total == *content;
}

}  // namespace

bool Buffer::reserve(size_t bytes) {
    if (bytes <= size) return true;
    release();
    size_t want = bytes < (1u << 20) ? (1u << 20) : bytes + bytes / 4;
    data = (uint8_t *)VirtualAlloc(nullptr, want, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    size = data ? want : 0;
    return data != nullptr;
}

void Buffer::release() {
    if (data) VirtualFree(data, 0, MEM_RELEASE);
    data = nullptr;
    size = 0;
}

Writer::~Writer() {
    if (open_) close_segment(CLOSE_UNLOADED);
    ref_.release(); level_shadow_.release(); vi_shadow_.release();
    stream_.release(); out_.release(); key_.release(); pieces_.release();
    if (xpress_work_) VirtualFree(xpress_work_, 0, MEM_RELEASE);
}

bool Writer::begin(const wchar_t *session_dir, const Layout &layout, const Settings &settings) {
    if (open_) close_segment(CLOSE_REARMED);
    layout_ = layout;
    settings_ = settings;
    wcsncpy(dir_, session_dir, MAX_PATH - 1);
    dir_[MAX_PATH - 1] = 0;
    level_total_ = vi_total_ = 0;
    for (uint32_t i = 0; i < layout.level_count; ++i) level_total_ += layout.level[i].bytes;
    for (uint32_t i = 0; i < layout.vi_count; ++i) vi_total_ += layout.vi[i].bytes;
    if (!ref_.reserve(layout.rdram_size) || !level_shadow_.reserve(level_total_ + 1)
        || !vi_shadow_.reserve(vi_total_ + 1)) return false;
    uint32_t work_bytes = 0;
    if (!xpress_work_ && (compress_ = (void *)compressor(&work_bytes)) != nullptr)
        xpress_work_ = VirtualAlloc(nullptr, work_bytes ? work_bytes : 1, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    stats_ = Stats();
    failed_ = boot_written_ = false;
    error_ = 0;
    next_segment_ = 1;
    closed_count_ = 0;
    closed_bytes_ = 0;
    pending_ = Pending();
    last_beat_ = last_prune_ = 0;
    began_ = true;
    resume();
    return true;
}

void Writer::resume() {
    level_valid_ = vi_valid_ = false;
    lost_seen_ = 0;
    have_owner_ = hole_waiting_ = false;
}

bool Writer::write_all(HANDLE file, const void *bytes, uint32_t n) {
    DWORD done = 0;
    if (file == INVALID_HANDLE_VALUE || !WriteFile(file, bytes, n, &done, nullptr) || done != n) {
        fail(file == INVALID_HANDLE_VALUE ? ERROR_INVALID_HANDLE : GetLastError());
        return false;
    }
    return true;
}

void Writer::fail(uint32_t error) {
    if (failed_) return;
    failed_ = true;
    error_ = error ? error : ERROR_WRITE_FAULT;
    if (tap_ != INVALID_HANDLE_VALUE) CloseHandle(tap_);
    if (index_ != INVALID_HANDLE_VALUE) CloseHandle(index_);
    tap_ = index_ = INVALID_HANDLE_VALUE;
    open_ = false;
}

uint64_t Writer::utc_now(int64_t *qpc) const {
    FILETIME now;
    LARGE_INTEGER counter;
    GetSystemTimePreciseAsFileTime(&now);
    QueryPerformanceCounter(&counter);
    *qpc = counter.QuadPart;
    return (uint64_t)now.dwHighDateTime << 32 | now.dwLowDateTime;
}

bool Writer::row(uint32_t kind, uint32_t a, uint32_t b, uint32_t c, int64_t d, uint64_t e) {
    uint8_t bytes[32];
    const uint32_t head[4] = {kind, a, b, c};
    memcpy(bytes, head, 16);
    memcpy(bytes + 16, &d, 8);
    memcpy(bytes + 24, &e, 8);
    if (!write_all(index_, bytes, 32)) return false;
    stats_.index_bytes += 32;
    return true;
}

void Writer::flush_task_row() {
    if (!pending_.live || pending_.written || !open_) return;
    const uint64_t flags = (pending_.shown ? TASK_SHOWN : 0) | (pending_.refused ? TASK_REFUSED : 0);
    row(ROW_TASK, pending_.index, pending_.timer, pending_.place, pending_.qpc, pending_.offset | flags << 48);
    pending_.written = true;
}

bool Writer::open_segment(uint32_t first_task, int64_t qpc) {
    wchar_t path[MAX_PATH];
    swprintf(path, MAX_PATH, L"%ls\\seg-%06u.tap", dir_, next_segment_);
    tap_ = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    swprintf(path, MAX_PATH, L"%ls\\seg-%06u.idx", dir_, next_segment_);
    index_ = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (tap_ == INVALID_HANDLE_VALUE || index_ == INVALID_HANDLE_VALUE) {
        fail(GetLastError());
        return false;
    }
    current_ = {next_segment_++, 0, qpc, qpc, false};
    open_ = true;
    ++stats_.segments;
    memset(ref_.data, 0, layout_.rdram_size);    // the reference image starts empty
    int64_t now = 0;
    const uint64_t utc = utc_now(&now);
    if (!row(ROW_OPEN, current_.number, first_task, 0, now, utc)) return false;
    last_beat_ = now;
    if (hole_waiting_) {
        hole_waiting_ = false;
        if (!row(ROW_HOLE, hole_first_, hole_last_, stats_.lost, qpc, 0)) return false;
    }
    return write_keys(first_task, qpc);
}

// The writer's shadows as whole key records, ahead of the segment's first task.
bool Writer::write_keys(uint32_t task_index, int64_t qpc) {
    if (level_valid_ && level_total_) {
        const uint32_t count = layout_.level_count, body = 96 + 8 * count + level_total_;
        if (!key_.reserve(body)) { fail(ERROR_OUTOFMEMORY); return false; }
        const uint32_t fields[8] = {task_index, REGIONS_LEVEL_KEY, count, level_total_, 0, 0, kBlock, 0};
        memcpy(key_.data, fields, 32);
        memcpy(key_.data + 32, level_segments_, 64);
        memcpy(key_.data + 96, layout_.level, 8ull * count);
        memcpy(key_.data + 96 + 8 * count, level_shadow_.data, level_total_);
        const RecordHeader header = {kMagic, REC_REGIONS, align32(32ull + body), 0xFFFFFFFF, qpc, lost_seen_, 0};
        if (!put_packed(header, key_.data, body, false, WRITER_KEY)) return false;
        stats_.key_bytes += level_total_;
    }
    if (vi_valid_ && vi_total_) {
        const uint32_t count = layout_.vi_count, body = 32 + 8 * count + vi_total_;
        if (!key_.reserve(body)) { fail(ERROR_OUTOFMEMORY); return false; }
        const uint32_t fields[8] = {vi_index_, VI_KEY, count, vi_total_, 0, 0, kBlock, vi_tasks_};
        memcpy(key_.data, fields, 32);
        memcpy(key_.data + 32, layout_.vi, 8ull * count);
        memcpy(key_.data + 32 + 8 * count, vi_shadow_.data, vi_total_);
        const RecordHeader header = {kMagic, REC_VI, align32(32ull + body), 0xFFFFFFFF, qpc, lost_seen_, 0};
        if (!put_packed(header, key_.data, body, false, WRITER_KEY)) return false;
        stats_.key_bytes += vi_total_;
    }
    return true;
}

// A level (or VI) record into the writer's shadow: a key replaces it, a delta
// patches it. Spans the arming did not name are ignored (none should exist).
void Writer::apply_shadow(bool level, const uint8_t *body) {
    const Span *spans = level ? layout_.level : layout_.vi;
    const uint32_t span_count = level ? layout_.level_count : layout_.vi_count;
    const uint32_t count = word(body, 2);
    const bool key = level ? word(body, 1) == REGIONS_LEVEL_KEY : word(body, 1) == VI_KEY;
    bool &valid = level ? level_valid_ : vi_valid_;
    if (!key && !valid) return;
    uint8_t *shadow = level ? level_shadow_.data : vi_shadow_.data;
    const uint8_t *table = body + (level ? 96 : 32), *data = table + 8ull * count;
    for (uint32_t i = 0; i < count; ++i) {
        const uint32_t begin = word(table, 2 * i), bytes = word(table, 2 * i + 1);
        uint32_t offset = 0;
        for (uint32_t s = 0; s < span_count; offset += spans[s++].bytes) {
            if (begin >= spans[s].begin && (uint64_t)begin + bytes <= (uint64_t)spans[s].begin + spans[s].bytes) {
                memcpy(shadow + offset + (begin - spans[s].begin), data, bytes);
                break;
            }
        }
        data += bytes;
    }
    if (key) valid = true;
    if (level) {
        uint32_t segments[16];
        memcpy(segments, body + 32, 64);
        for (uint32_t s = 0; s < 16; ++s)
            if (segments[s]) { memcpy(level_segments_, segments, 64); break; }
    } else {
        vi_index_ = word(body, 0);
        vi_tasks_ = word(body, 7);
    }
}

// The record as REC_PACKED (see the header comment), compressed when that helps.
bool Writer::put_packed(const RecordHeader &header, const uint8_t *body, uint32_t body_bytes, bool dedupe,
                        uint32_t flags) {
    uint32_t count = 0, prefix = 0, content = 0, pieces = 0;
    dedupe = dedupe && settings_.dedupe
             && rdram_spans(body, body_bytes, header.type, layout_.rdram_size, pieces_, &count, &prefix, &content);
    const uint8_t *stream = body;
    uint32_t stream_bytes = body_bytes;
    if (dedupe) {
        const Span *spans = (const Span *)pieces_.data;
        for (uint32_t i = 0; i < count; ++i) {
            const uint32_t begin = spans[i].begin, end = begin + spans[i].bytes;
            pieces += end > begin ? (end - 1) / kBlock - begin / kBlock + 1 : 0;
        }
        const uint32_t bitmap = ((pieces + 7) / 8 + 3) & ~3u;
        if (!stream_.reserve((size_t)bitmap + body_bytes)) { fail(ERROR_OUTOFMEMORY); return false; }
        uint8_t *bits = stream_.data, *at = stream_.data + bitmap;
        memset(bits, 0, bitmap);
        memcpy(at, body, prefix);
        at += prefix;
        const uint8_t *now = body + prefix;
        uint32_t piece = 0;
        for (uint32_t i = 0; i < count; ++i) {
            for (uint32_t a = spans[i].begin, end = a + spans[i].bytes; a < end; ++piece) {
                const uint32_t next = (a / kBlock + 1) * kBlock, n = (next < end ? next : end) - a;
                if (memcmp(now, ref_.data + a, n)) {
                    bits[piece >> 3] |= (uint8_t)(1u << (piece & 7));
                    memcpy(at, now, n);
                    at += n;
                }
                now += n;
                a += n;
            }
        }
        memcpy(at, body + prefix + content, body_bytes - prefix - content);
        at += body_bytes - prefix - content;
        now = body + prefix;   // the reference follows what this record showed, in span order
        for (uint32_t i = 0; i < count; ++i) {
            memcpy(ref_.data + spans[i].begin, now, spans[i].bytes);
            now += spans[i].bytes;
        }
        stream = stream_.data;
        stream_bytes = (uint32_t)(at - stream_.data);
    }
    const size_t room = (size_t)stream_bytes + stream_bytes / 8 + 4096;
    if (!out_.reserve(64 + room)) { fail(ERROR_OUTOFMEMORY); return false; }
    uint32_t codec = CODEC_STORED, payload = stream_bytes;
    if (settings_.compress && xpress_work_ && stream_bytes >= kCompressMin) {
        const CompressFn compress = (CompressFn)compress_;
        ULONG final_bytes = 0;
        if (compress(kFormatXpress, (PUCHAR)stream, stream_bytes, out_.data + 64, (ULONG)room, 4096,
                     &final_bytes, xpress_work_) == 0 && final_bytes < stream_bytes) {
            codec = CODEC_XPRESS;
            payload = final_bytes;
            ++stats_.compressed;
        }
    }
    if (codec == CODEC_STORED) memcpy(out_.data + 64, stream, stream_bytes);
    const uint32_t bytes = align32(64ull + payload);
    const RecordHeader outer = {kMagic, REC_PACKED, bytes, header.sequence, header.qpc, header.lost, flags};
    const uint32_t head[8] = {header.type, body_bytes, codec, stream_bytes, payload, pieces, dedupe ? 1u : 0u, 0};
    memcpy(out_.data, &outer, 32);
    memcpy(out_.data + 32, head, 32);
    memset(out_.data + 64 + payload, 0, bytes - 64 - payload);
    if (!write_all(tap_, out_.data, bytes)) return false;
    current_.bytes += bytes;
    stats_.output_bytes += bytes;
    return true;
}

// Records were dropped (the ring was full) between the previous record and
// this one: the previous record's task through this one (a task record itself
// arrived whole, so it starts after the hole) is incomplete.
void Writer::note_loss(uint32_t stamp, uint32_t owner, bool is_task, int64_t qpc) {
    stats_.lost += stamp - lost_seen_;
    lost_seen_ = stamp;
    const uint32_t first = have_owner_ ? owner_ : owner;
    uint32_t last = is_task ? owner - 1 : owner;
    if (is_task && (!owner || !have_owner_)) return;   // lost before this capture's first task (its boot copy)
    if (last < first) last = first;
    ++stats_.holes;
    if (open_) row(ROW_HOLE, first, last, stats_.lost, qpc, 0);
    else {
        hole_first_ = hole_waiting_ ? hole_first_ : first;
        hole_last_ = last;
        hole_waiting_ = true;
    }
}

bool Writer::add(const uint8_t *record) {
    if (failed_ || !began_) return false;
    RecordHeader header;
    memcpy(&header, record, 32);
    if (header.type == REC_PAD) return true;
    const uint8_t *body = record + 32;
    const uint32_t body_bytes = header.bytes - 32;
    ++stats_.records;
    stats_.input_bytes += header.bytes;
    if (header.type == REC_BOOT) {
        if (boot_written_) return true;
        wchar_t path[MAX_PATH];
        swprintf(path, MAX_PATH, L"%ls\\boot.bin", dir_);
        HANDLE file = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
                                  FILE_ATTRIBUTE_NORMAL, nullptr);
        DWORD done = 0;
        boot_written_ = file != INVALID_HANDLE_VALUE && WriteFile(file, record, header.bytes, &done, nullptr)
                        && done == header.bytes;
        if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
        return true;   // without it a package only lacks the act selector's code row
    }
    const bool is_task = header.type == REC_TASK;
    uint32_t owner = have_owner_ ? owner_ : 0;
    if (is_task) owner = word(body, 10);
    else if (header.type == REC_REGIONS) owner = word(body, 0);
    if (is_task) {
        flush_task_row();
        if (header.lost != lost_seen_) note_loss(header.lost, owner, true, header.qpc);
        if (open_ && (header.qpc - current_.first_qpc >= ticks(settings_.segment_ms)
                      || current_.bytes >= settings_.segment_bytes)) close_segment(CLOSE_ROLLOVER);
        if (!open_ && !open_segment(owner, header.qpc)) return false;
        const uint64_t offset = current_.bytes;
        if (!put_packed(header, body, body_bytes, true, 0)) return false;
        const uint32_t place = (identity(body, layout_.level_id) & 0xFFFF) | identity(body, layout_.area_id) << 16;
        pending_ = {true, false, false, word(body, 0) != 0, owner, identity(body, layout_.timer), place,
                    header.qpc, offset};
        owner_ = owner;
        have_owner_ = true;
        ++stats_.tasks;
        stats_.refused += word(body, 0) != 0;
        current_.last_qpc = header.qpc;
        return true;
    }
    if (!open_ && !open_segment(owner, header.qpc)) return false;
    if (header.lost != lost_seen_) note_loss(header.lost, owner, false, header.qpc);
    if (header.type == REC_PRESENT) {
        ++stats_.presents;
        if (pending_.live && !pending_.shown) {
            pending_.shown = true;
            if (pending_.written && !row(ROW_SHOWN, pending_.index, 0, 0, header.qpc, 0)) return false;
        }
        if (!write_all(tap_, record, header.bytes)) return false;
        current_.bytes += header.bytes;
        stats_.output_bytes += header.bytes;
        return true;
    }
    if (header.type == REC_REGIONS && word(body, 1) != REGIONS_TASK) apply_shadow(true, body);
    else if (header.type == REC_VI) apply_shadow(false, body);
    return put_packed(header, body, body_bytes, header.type == REC_REGIONS, 0);
}

void Writer::close_segment(uint32_t reason) {
    if (!open_) return;
    flush_task_row();
    int64_t now = 0;
    utc_now(&now);
    row(ROW_CLOSE, reason, pending_.index, 0, now, current_.bytes);
    pending_.live = false;
    if (tap_ != INVALID_HANDLE_VALUE) CloseHandle(tap_);
    if (index_ != INVALID_HANDLE_VALUE) CloseHandle(index_);
    tap_ = index_ = INVALID_HANDLE_VALUE;
    open_ = false;
    if (closed_count_ == kMaxClosed) {   // cannot happen while pruning works; forget the oldest
        memmove(closed_, closed_ + 1, sizeof closed_[0] * (kMaxClosed - 1));
        --closed_count_;
    }
    closed_[closed_count_++] = current_;
    closed_bytes_ += current_.bytes;
    prune(now);
}

// Oldest first, while older than the window or over the byte cap. The index
// goes first so readers stop listing the segment; a .tap a reader holds open
// stays until a later pass.
void Writer::prune(int64_t now_qpc) {
    last_prune_ = now_qpc;
    while (closed_count_) {
        Segment &oldest = closed_[0];
        const bool expired = now_qpc - oldest.last_qpc > ticks(settings_.window_ms);
        const bool over = closed_bytes_ + (open_ ? current_.bytes : 0) > settings_.max_bytes;
        if (!expired && !over) break;
        wchar_t path[MAX_PATH];
        if (!oldest.index_gone) {
            swprintf(path, MAX_PATH, L"%ls\\seg-%06u.idx", dir_, oldest.number);
            oldest.index_gone = DeleteFileW(path) || GetLastError() == ERROR_FILE_NOT_FOUND;
        }
        swprintf(path, MAX_PATH, L"%ls\\seg-%06u.tap", dir_, oldest.number);
        if (!oldest.index_gone || !(DeleteFileW(path) || GetLastError() == ERROR_FILE_NOT_FOUND)) {
            ++stats_.delete_retries;
            break;
        }
        closed_bytes_ -= oldest.bytes;
        memmove(closed_, closed_ + 1, sizeof closed_[0] * (closed_count_ - 1));
        --closed_count_;
        ++stats_.deleted;
    }
}

void Writer::tick(int64_t now_qpc) {
    if (!began_ || failed_) return;
    if (pending_.live && !pending_.written && now_qpc - pending_.qpc > ticks(kRowDelayMs)) flush_task_row();
    if (open_ && now_qpc - last_beat_ >= ticks(kBeatMs)) {
        int64_t now = 0;
        const uint64_t utc = utc_now(&now);
        row(ROW_BEAT, stats_.lost, stats_.refused, stats_.tasks, now, utc);
        last_beat_ = now;
    }
    if (now_qpc - last_prune_ >= ticks(kPruneMs)) prune(now_qpc);
}

}  // namespace scene_spool
