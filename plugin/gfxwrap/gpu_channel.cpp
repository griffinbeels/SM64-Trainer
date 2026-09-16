#include "gpu_channel.h"
#include <cstring>
#include <cwchar>
#include <limits>

namespace gpu_channel {
namespace {
constexpr uint8_t magic[8] = {'S','M','6','4','G','P','C','1'};
bool zero(const void *p, size_t n) {
    const auto *b = static_cast<const uint8_t *>(p);
    for (size_t i = 0; i < n; ++i) if (b[i]) return false;
    return true;
}
uint64_t align64(uint64_t n) { return (n + 63) & ~uint64_t(63); }
bool text_valid(const uint16_t *name) {
    unsigned i = 0;
    for (; i < 128 && name[i]; ++i) {
        if (name[i] >= 0xdc00 && name[i] <= 0xdfff) return false;
        if (name[i] >= 0xd800 && name[i] <= 0xdbff) {
            if (++i == 128 || name[i] < 0xdc00 || name[i] > 0xdfff) return false;
        }
    }
    return i > 0 && i < 128 && zero(name + i, (128-i)*2);
}
bool identity(const gc_header &h, uint64_t lo, uint64_t hi, uint32_t epoch, uint32_t gen) {
    return h.nonce_lo == lo && h.nonce_hi == hi && h.epoch == epoch && h.generation == gen;
}
bool header_config(const gc_header &h) {
    if (!h.producer_pid || !h.owner_pid || !h.producer_birth || !h.owner_birth
        || (!h.nonce_lo && !h.nonce_hi) || !h.epoch || !h.generation || !h.qpc_frequency
        || h.width < 2 || h.height < 2 || h.width > GC_MAX_DIMENSION || h.height > GC_MAX_DIMENSION
        || !h.offer_count || h.offer_count > GC_MAX_OFFERS || h.bridge_count != GC_BRIDGES
        || !h.table_count || h.table_count > GC_TABLE_FIELDS
        || h.stamp_capacity != GC_STAMP_BYTES || h.sample_stride != 8 || h.format != GC_FORMAT_BGRA8
        || !h.packet_count || h.packet_count > GC_MAX_OFFERS || !h.packet_bytes
        || h.packet_bytes > GC_MAX_PACKET_BYTES || h.pending_bytes < h.packet_bytes
        || uint64_t(h.pending_bytes) > uint64_t(h.packet_count) * h.packet_bytes
        || h.ram_budget > GC_MAX_MAP_BYTES || !text_valid(h.texture_names[0])
        || !text_valid(h.texture_names[1])
        || !memcmp(h.texture_names[0], h.texture_names[1], sizeof h.texture_names[0])
        || !zero(h.reserved, sizeof h.reserved)) return false;
    uint64_t sum = 0;
    for (unsigned i = 0; i < GC_TABLE_FIELDS; ++i) {
        if (i >= h.table_count) {
            if (h.table[i].offset || h.table[i].length) return false;
        } else {
            if (!h.table[i].length || uint64_t(h.table[i].offset) + h.table[i].length > (uint64_t(1) << 32)) return false;
            sum += h.table[i].length;
        }
    }
    return sum <= GC_STAMP_BYTES;
}
bool offer_valid(const gc_offer &o, const gc_header &h, unsigned slot) {
    if (!o.token || !o.occurrence || o.kind != GC_OFFER_READY
        || !identity(h, o.nonce_lo, o.nonce_hi, o.epoch, o.generation)
        || o.list_qpc < 0 || o.boundary_qpc < o.list_qpc || o.width != h.width || o.height != h.height
        || o.table_count != h.table_count || o.sample_bytes != h.sample_bytes || o.stamp_bytes > GC_STAMP_BYTES
        || o.stamp_offset != h.payload_offset + slot*h.payload_stride
        || o.sample_offset != o.stamp_offset + GC_STAMP_BYTES || !zero(o.reserved, sizeof o.reserved)) return false;
    uint64_t sum = 0;
    for (unsigned i = 0; i < GC_TABLE_FIELDS; ++i) {
        if (o.lengths[i] > h.table[i].length) return false;
        sum += o.lengths[i];
    }
    return sum == o.stamp_bytes;
}
}

uint64_t process_birth(HANDLE process) {
    FILETIME created{}, exited{}, kernel{}, user{};
    if (!GetProcessTimes(process, &created, &exited, &kernel, &user)) return 0;
    return (uint64_t(created.dwHighDateTime) << 32) | created.dwLowDateTime;
}

bool layout(gc_header &h) {
    h.version = GC_VERSION; memcpy(h.magic, magic, 8); h.header_bytes = GC_HEADER_BYTES;
    h.even_width = h.width & ~1u; h.even_height = h.height & ~1u;
    h.format = GC_FORMAT_BGRA8; h.sample_stride = 8; h.stamp_capacity = GC_STAMP_BYTES;
    h.bridge_count = GC_BRIDGES;
    if (!header_config(h)) return false;
    uint64_t sample = uint64_t((h.width + 7)/8) * ((h.height+7)/8) * 4;
    h.sample_bytes = h.sample_capacity = uint32_t(sample);
    h.status_offset = GC_HEADER_BYTES; h.status_bytes = sizeof(gc_status);
    h.offer_offset = h.status_offset + h.status_bytes;
    h.decision_offset = h.offer_offset + h.offer_count * sizeof(gc_offer);
    h.bridge_offset = h.decision_offset + h.offer_count * sizeof(gc_decision);
    h.receipt_offset = h.bridge_offset + GC_BRIDGES * sizeof(gc_bridge);
    h.client_offset = h.receipt_offset + GC_BRIDGES * sizeof(gc_receipt);
    h.client_bytes = sizeof(gc_client);
    h.publication_offset = h.client_offset + h.client_bytes;
    h.publication_bytes = sizeof(gc_publication);
    h.payload_offset = h.publication_offset + h.publication_bytes;
    h.payload_stride = uint32_t(align64(GC_STAMP_BYTES + sample));
    uint64_t total = uint64_t(h.payload_offset) + uint64_t(h.payload_stride)*h.offer_count;
    if (total > h.ram_budget || total > GC_MAX_MAP_BYTES) return false;
    h.total_bytes = uint32_t(total);
    return true;
}

bool validate(const gc_header &h, uint64_t mapped_bytes) {
    if ((h.seq & 1) || !h.seq || h.version != GC_VERSION || memcmp(h.magic, magic, 8)
        || h.header_bytes != GC_HEADER_BYTES || h.total_bytes > mapped_bytes
        || !header_config(h)) return false;
    gc_header expected = h;
    if (!layout(expected)) return false;
    return !memcmp(&expected, &h, sizeof h);
}

bool decision_valid(const gc_decision &d, const gc_header &h) {
    if (!d.token || !d.occurrence || !identity(h, d.nonce_lo, d.nonce_hi, d.epoch, d.generation)
        || !zero(d.reserved, sizeof d.reserved)) return false;
    if (d.kind == GC_SELECTED)
        return d.encode_serial && d.nominal_duration && d.pts <= uint64_t(INT64_MAX)
            && d.nominal_duration <= uint64_t(INT64_MAX) - d.pts
            && !(d.flags & ~GC_FORCE_IDR) && !d.reason && !d.retained_occurrence && !d.retained_serial;
    if (d.kind < GC_COALESCED || d.kind > GC_FAILED || d.encode_serial || d.pts || d.nominal_duration || d.flags) return false;
    if (d.kind == GC_COALESCED) return d.retained_occurrence && d.retained_serial && !d.reason;
    return !d.retained_occurrence && !d.retained_serial && d.reason;
}

bool stable_read(const void *source, void *out, uint32_t bytes) {
    if (!source || !out || bytes < 4 || (reinterpret_cast<uintptr_t>(source) & 3)) return false;
    auto *seq = (volatile LONG *)source;
    for (unsigned n = 0; n < 3; ++n) {
        uint32_t before = uint32_t(InterlockedCompareExchange(seq, 0, 0));
        if (before & 1) continue;
        memcpy(out, source, bytes); MemoryBarrier();
        if (before == uint32_t(InterlockedCompareExchange(seq, 0, 0))) return true;
    }
    return false;
}

bool Producer::put(uint32_t offset, const void *data, uint32_t bytes) {
    auto *target = view_ + offset;
    uint32_t seq = uint32_t(InterlockedCompareExchange((volatile LONG *)target, 0, 0));
    if ((seq & 1) || seq >= UINT32_MAX-1) return false;
    InterlockedExchange((volatile LONG *)target, LONG(seq+1));
    memcpy(target+4, static_cast<const uint8_t *>(data)+4, bytes-4); MemoryBarrier();
    InterlockedExchange((volatile LONG *)target, LONG(seq+2));
    return true;
}

Producer::~Producer() { if (!worker_ || worker_ == GetCurrentThreadId()) close(); }

Result Producer::create(const gc_header &config) {
    if (view_ || worker_) return Result::busy;
    gc_header h = config;
    h.seq = 2; h.producer_pid = GetCurrentProcessId(); h.producer_birth = process_birth(GetCurrentProcess());
    if (!layout(h)) return Result::invalid;
    HANDLE owner = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, h.owner_pid);
    if (!owner || process_birth(owner) != h.owner_birth || WaitForSingleObject(owner, 0) != WAIT_TIMEOUT) {
        if (owner) CloseHandle(owner);
        return Result::owner_gone;
    }
    swprintf_s(name_, L"%s%016llx%016llx", GC_NAMESPACE, h.nonce_hi, h.nonce_lo);
    HANDLE mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, h.total_bytes, name_);
    DWORD error = GetLastError();
    if (!mapping || error == ERROR_ALREADY_EXISTS) {
        if (mapping) CloseHandle(mapping); CloseHandle(owner);
        return mapping ? Result::busy : Result::system_error;
    }
    void *view = MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, h.total_bytes);
    if (!view) { CloseHandle(mapping); CloseHandle(owner); return Result::system_error; }
    header_ = h; owner_ = owner; mapping_ = mapping; view_ = static_cast<uint8_t *>(view);
    worker_ = GetCurrentThreadId();
    // New mapping is zero-initialized. Never erase an existing client's halves.
    wchar_t event_name[160];
    swprintf_s(event_name, L"%s.producer", name_); result_ = CreateEventW(nullptr, FALSE, FALSE, event_name);
    swprintf_s(event_name, L"%s.client", name_); wake_ = CreateEventW(nullptr, FALSE, FALSE, event_name);
    if (!result_ || !wake_) { close(); return Result::system_error; }
    // Publish initial status BEFORE immutable header allows the reader to open.
    gc_status initial{}; initial.state = GC_PREPARING;
    initial.nonce_lo = h.nonce_lo; initial.nonce_hi = h.nonce_hi;
    initial.epoch = h.epoch; initial.generation = h.generation;
    gc_publication publication{};
    if (!put(h.publication_offset, &publication, sizeof publication)
        || !put(h.status_offset, &initial, sizeof initial) || !put(0, &h, sizeof h)) { close(); return Result::system_error; }
    return Result::ok;
}

Result Producer::check() {
    if (!view_ || closed_) return Result::closed;
    if (worker_ != GetCurrentThreadId()) return Result::wrong_worker;
    if (WaitForSingleObject(owner_, 0) != WAIT_TIMEOUT) {
        gc_status s{}; s.state = GC_FAULT; s.reason = GC_REASON_CHANNEL | uint32_t(Result::owner_gone);
        s.nonce_lo = header_.nonce_lo; s.nonce_hi = header_.nonce_hi;
        s.epoch = header_.epoch; s.generation = header_.generation;
        terminal_ = s;
        put(header_.status_offset, &s, sizeof s); SetEvent(result_); closed_ = true;
        return Result::owner_gone;
    }
    gc_client client{};
    if (!stable_read(view_ + header_.client_offset, &client, sizeof client)) return Result::busy;
    if (client.seq) {
        if (!identity(header_, client.nonce_lo, client.nonce_hi, client.epoch, client.generation)
            || client.owner_pid != header_.owner_pid || client.owner_birth != header_.owner_birth
            || client.reserved0 || !zero(client.reserved, sizeof client.reserved)
            || (client.state != 1 && client.state != 2 && client.state != 3)
            || client.seq < client_seq_ || (client_ready_ && client.state == 1)) return Result::invalid;
        if (client.state == 2) { closed_ = true; return Result::closed; }
        client_seq_ = client.seq;
        if (client.state == 3) client_ready_ = true;
    } else if (client_seq_) {
        return Result::invalid;
    }
    return Result::ok;
}

Result Producer::reader_ready() {
    const auto result = check();
    if (result != Result::ok) return result;
    return client_ready_ ? Result::ok : Result::empty;
}

Result Producer::publish(unsigned slot, gc_offer o, const void *stamps, const void *sample) {
    Result ready = check(); if (ready != Result::ok) return ready;
    if (slot >= header_.offer_count || !sample || (o.stamp_bytes && !stamps)) return Result::invalid;
    if (offers_[slot].token) return Result::busy;
    o.kind = GC_OFFER_READY; o.epoch = header_.epoch; o.generation = header_.generation;
    o.nonce_lo = header_.nonce_lo; o.nonce_hi = header_.nonce_hi;
    o.width = header_.width; o.height = header_.height;
    o.table_count = header_.table_count; o.sample_bytes = header_.sample_bytes;
    o.stamp_offset = header_.payload_offset + slot*header_.payload_stride;
    o.sample_offset = o.stamp_offset + GC_STAMP_BYTES;
    if (!offer_valid(o, header_, slot)) return Result::invalid;
    if (last_token_ == UINT64_MAX) return Result::exhausted;
    if (o.token != last_token_+1 || o.occurrence <= last_occurrence_) return Result::stale;
    uint8_t *target = view_ + header_.offer_offset + slot*sizeof(gc_offer);
    uint32_t seq = uint32_t(InterlockedCompareExchange((volatile LONG *)target, 0, 0));
    if ((seq & 1) || seq >= UINT32_MAX-1) return Result::exhausted;
    auto *publication = reinterpret_cast<gc_publication *>(view_ + header_.publication_offset);
    uint32_t global = uint32_t(InterlockedCompareExchange((volatile LONG *)&publication->seq, 0, 0));
    if ((global & 1) || global >= UINT32_MAX-1) return Result::exhausted;
    InterlockedExchange((volatile LONG *)&publication->seq, LONG(global+1));
    InterlockedExchange((volatile LONG *)target, LONG(seq+1));
    memset(view_ + o.stamp_offset, 0, GC_STAMP_BYTES);
    if (o.stamp_bytes) memcpy(view_ + o.stamp_offset, stamps, o.stamp_bytes);
    memcpy(view_ + o.sample_offset, sample, o.sample_bytes);
    memcpy(target+4, reinterpret_cast<const uint8_t *>(&o)+4, sizeof o-4);
    MemoryBarrier(); InterlockedExchange((volatile LONG *)target, LONG(seq+2));
    offers_[slot] = o; decided_[slot] = false; bridge_published_[slot] = false; decisions_[slot] = {};
    last_token_ = o.token; last_occurrence_ = o.occurrence;
    publication->last_token = o.token; MemoryBarrier();
    InterlockedExchange((volatile LONG *)&publication->seq, LONG(global+2));
    SetEvent(result_);
    return Result::ok;
}

Result Producer::take_decision(unsigned &slot, gc_decision &out) {
    Result ready = check(); if (ready != Result::ok) return ready;
    unsigned head = GC_MAX_OFFERS;
    for (unsigned i = 0; i < header_.offer_count; ++i)
        if (offers_[i].token && !decided_[i] && (head == GC_MAX_OFFERS || offers_[i].occurrence < offers_[head].occurrence)) head = i;
    bool torn = false;
    for (unsigned i = 0; i < header_.offer_count; ++i) {
        gc_decision d{};
        if (!stable_read(view_ + header_.decision_offset + i*sizeof d, &d, sizeof d)) { torn = true; continue; }
        if (d.seq == decision_seq_[i]) continue;
        if (!d.seq || !decision_valid(d, header_)) return Result::invalid;
        if (!offers_[i].token || d.token != offers_[i].token || d.occurrence != offers_[i].occurrence) return Result::stale;
        if (decided_[i]) return Result::repeated;
        if (i != head) return Result::out_of_order;
        if (d.kind == GC_SELECTED && (d.encode_serial <= last_encode_ || (have_pts_ && d.pts <= last_pts_))) return Result::stale;
        decided_[i] = true; decisions_[i] = d; decision_seq_[i] = d.seq;
        if (d.kind == GC_SELECTED) { last_encode_ = d.encode_serial; last_pts_ = d.pts; have_pts_ = true; }
        slot = i; out = d;
        return Result::ok;
    }
    return torn ? Result::busy : Result::empty;
}

Result Producer::retire_offer(unsigned slot, uint64_t token) {
    Result ready = check(); if (ready != Result::ok) return ready;
    if (slot >= header_.offer_count || !token) return Result::invalid;
    if (offers_[slot].token != token) return Result::stale;
    if (!decided_[slot] || (decisions_[slot].kind == GC_SELECTED && !bridge_published_[slot])) return Result::busy;
    gc_offer empty{};
    auto *publication = reinterpret_cast<gc_publication *>(view_ + header_.publication_offset);
    uint32_t global = uint32_t(InterlockedCompareExchange((volatile LONG *)&publication->seq, 0, 0));
    if ((global & 1) || global >= UINT32_MAX-1) return Result::exhausted;
    InterlockedExchange((volatile LONG *)&publication->seq, LONG(global+1));
    bool published = put(header_.offer_offset + slot*sizeof empty, &empty, sizeof empty);
    MemoryBarrier(); InterlockedExchange((volatile LONG *)&publication->seq, LONG(global+2));
    if (!published) return Result::exhausted;
    offers_[slot] = {}; decisions_[slot] = {}; decided_[slot] = false;
    SetEvent(result_); return Result::ok;
}

Result Producer::publish_bridge(unsigned index, uint64_t token, const gc_decision &d) {
    Result ready = check(); if (ready != Result::ok) return ready;
    if (index >= GC_BRIDGES || !token || !decision_valid(d, header_) || d.kind != GC_SELECTED) return Result::invalid;
    if (bridges_[index].token) return Result::busy;
    if (last_bridge_token_ == UINT64_MAX) return Result::exhausted;
    if (token != last_bridge_token_+1) return Result::stale;
    unsigned accepted = GC_MAX_OFFERS;
    for (unsigned i = 0; i < header_.offer_count; ++i)
        if (decided_[i] && !memcmp(&d, &decisions_[i], sizeof d)) accepted = i;
    if (accepted == GC_MAX_OFFERS) return Result::stale;
    if (bridge_published_[accepted]) return Result::repeated;
    for (unsigned i=0; i<header_.offer_count; ++i)
        if (decided_[i] && decisions_[i].kind == GC_SELECTED && !bridge_published_[i]
            && decisions_[i].encode_serial < d.encode_serial) return Result::out_of_order;
    for (const auto &b : bridges_) if (b.token && b.encode_serial == d.encode_serial) return Result::repeated;
    gc_bridge b{}; b.ready = 1; b.token = token; b.occurrence = d.occurrence;
    b.encode_serial = d.encode_serial; b.pts = d.pts; b.nominal_duration = d.nominal_duration;
    b.nonce_lo = header_.nonce_lo; b.nonce_hi = header_.nonce_hi;
    b.epoch = header_.epoch; b.generation = header_.generation; b.texture_index = index; b.flags = d.flags;
    auto *publication = reinterpret_cast<gc_publication *>(view_ + header_.publication_offset);
    uint32_t global = uint32_t(InterlockedCompareExchange((volatile LONG *)&publication->seq, 0, 0));
    if ((global & 1) || global >= UINT32_MAX-1) return Result::exhausted;
    InterlockedExchange((volatile LONG *)&publication->seq, LONG(global+1));
    bool published = put(header_.bridge_offset + index*sizeof b, &b, sizeof b);
    if (published) publication->last_bridge_token = token;
    MemoryBarrier(); InterlockedExchange((volatile LONG *)&publication->seq, LONG(global+2));
    if (!published) return Result::exhausted;
    bridges_[index] = b; bridge_published_[accepted] = true; receipted_[index] = false; last_bridge_token_ = token; SetEvent(result_);
    return Result::ok;
}

Result Producer::take_receipt(unsigned &index, gc_receipt &out) {
    Result ready = check(); if (ready != Result::ok) return ready;
    bool torn = false;
    for (unsigned i = 0; i < GC_BRIDGES; ++i) {
        gc_receipt r{};
        if (!stable_read(view_ + header_.receipt_offset + i*sizeof r, &r, sizeof r)) { torn = true; continue; }
        if (r.seq == receipt_seq_[i]) continue;
        if (!r.seq || !identity(header_, r.nonce_lo, r.nonce_hi, r.epoch, r.generation)
            || r.accepted != 1 || r.reason || r.texture_index != i || !zero(r.reserved, sizeof r.reserved)) return Result::invalid;
        const auto &b = bridges_[i];
        if (!b.token || r.token != b.token || r.occurrence != b.occurrence || r.encode_serial != b.encode_serial) return Result::stale;
        if (receipted_[i]) return Result::repeated;
        receipted_[i] = true; receipt_seq_[i] = r.seq; index = i; out = r;
        return Result::ok; // No image/key mutation here.
    }
    return torn ? Result::busy : Result::empty;
}

Result Producer::retire_bridge_after_key0(unsigned i, uint64_t token) {
    Result ready = check(); if (ready != Result::ok) return ready;
    if (i >= GC_BRIDGES || !token) return Result::invalid;
    if (bridges_[i].token != token) return Result::stale;
    gc_bridge empty{};
    auto *publication = reinterpret_cast<gc_publication *>(view_ + header_.publication_offset);
    uint32_t global = uint32_t(InterlockedCompareExchange((volatile LONG *)&publication->seq, 0, 0));
    if ((global & 1) || global >= UINT32_MAX-1) return Result::exhausted;
    InterlockedExchange((volatile LONG *)&publication->seq, LONG(global+1));
    bool published = put(header_.bridge_offset + i*sizeof empty, &empty, sizeof empty);
    MemoryBarrier(); InterlockedExchange((volatile LONG *)&publication->seq, LONG(global+2));
    if (!published) return Result::exhausted;
    bridges_[i] = {}; receipted_[i] = false; SetEvent(result_);
    return Result::ok;
}

Result Producer::status(uint32_t state, uint32_t reason, uint64_t heartbeat, uint64_t frontier,
                        uint64_t occurrence, uint64_t serial) {
    Result ready = check(); if (ready != Result::ok) return ready;
    if (state < GC_PREPARING || state > GC_FAULT || serial > last_token_) return Result::invalid;
    gc_status s{}; s.state = state; s.reason = reason; s.heartbeat_qpc = heartbeat;
    s.frontier_qpc = frontier; s.frontier_occurrence = occurrence; s.frontier_serial = serial;
    s.nonce_lo = header_.nonce_lo; s.nonce_hi = header_.nonce_hi;
    s.epoch = header_.epoch; s.generation = header_.generation;
    if (!put(header_.status_offset, &s, sizeof s)) return Result::exhausted;
    if (state == GC_CLOSED || state == GC_FAULT) { terminal_ = s; closed_ = true; }
    SetEvent(result_); return Result::ok;
}

Result Producer::close(uint32_t reason) {
    if (worker_ && worker_ != GetCurrentThreadId()) return Result::wrong_worker;
    if (view_) {
        gc_status s = terminal_;
        if (!s.state) {
            s.state = GC_CLOSED; s.nonce_lo = header_.nonce_lo; s.nonce_hi = header_.nonce_hi;
            s.epoch = header_.epoch; s.generation = header_.generation;
        }
        if (!s.reason) s.reason = reason;
        terminal_ = s;
        put(header_.status_offset, &s, sizeof s);
        if (result_) SetEvent(result_);
        UnmapViewOfFile(view_); view_ = nullptr;
    }
    if (mapping_) CloseHandle(mapping_); if (owner_) CloseHandle(owner_);
    if (wake_) CloseHandle(wake_); if (result_) CloseHandle(result_);
    mapping_ = owner_ = wake_ = result_ = nullptr; closed_ = true;
    // Object is one-session-only; new owner/generation requires a fresh instance.
    return Result::ok;
}
}
