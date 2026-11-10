// SPDX-License-Identifier: MIT
// Generic transport envelope. Application payloads are never interpreted here.
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace eos_transport {
constexpr size_t PayloadLimit = 1024, ControlLimit = 512, HeaderBytes = 64;
using Nonce = std::array<uint8_t, 16>;
enum class Kind : uint8_t { Hello = 1, Ack = 2, Data = 3, Control = 4 };
struct Packet { Kind kind = Kind::Hello; Nonce sender{}, recipient{}; uint64_t sequence = 0; std::vector<uint8_t> payload; };
inline bool nonzero(const Nonce& nonce) { return std::any_of(nonce.begin(), nonce.end(), [](uint8_t value) { return value != 0; }); }
inline std::string hex(const Nonce& nonce) {
    const char digits[] = "0123456789abcdef"; std::string result;
    for (auto value : nonce) { result += digits[value >> 4]; result += digits[value & 15]; }
    return result;
}
inline bool unhex(const std::string& text, Nonce& nonce) {
    if (text.size() != 32) return false;
    Nonce result{};
    for (size_t at = 0; at < text.size(); ++at) {
        const auto digit = text[at]; unsigned value = 0;
        if (digit >= '0' && digit <= '9') value = digit - '0';
        else if (digit >= 'a' && digit <= 'f') value = digit - 'a' + 10;
        else if (digit >= 'A' && digit <= 'F') value = digit - 'A' + 10;
        else return false;
        result[at / 2] = static_cast<uint8_t>((result[at / 2] << 4) | value);
    }
    nonce = result; return true;
}
inline void put(uint8_t* destination, uint64_t value, size_t bytes) { for (size_t at = 0; at < bytes; ++at) destination[at] = static_cast<uint8_t>(value >> (8 * at)); }
inline uint64_t get(const uint8_t* source, size_t bytes) { uint64_t value = 0; for (size_t at = 0; at < bytes; ++at) value |= static_cast<uint64_t>(source[at]) << (8 * at); return value; }
inline std::vector<uint8_t> encode(const Packet& packet) {
    std::vector<uint8_t> bytes(HeaderBytes + packet.payload.size(), 0);
    bytes[0] = 'E'; bytes[1] = 'T'; bytes[2] = 'P'; bytes[3] = '1'; put(bytes.data() + 4, 1, 2);
    bytes[6] = static_cast<uint8_t>(packet.kind); put(bytes.data() + 8, HeaderBytes, 2); put(bytes.data() + 10, packet.payload.size(), 2);
    std::copy(packet.sender.begin(), packet.sender.end(), bytes.begin() + 16); std::copy(packet.recipient.begin(), packet.recipient.end(), bytes.begin() + 32);
    put(bytes.data() + 48, packet.sequence, 8); std::copy(packet.payload.begin(), packet.payload.end(), bytes.begin() + HeaderBytes); return bytes;
}
inline bool decode(const uint8_t* bytes, size_t size, bool control_socket, uint8_t channel, Packet& result) {
    if (!bytes || size < HeaderBytes || size > HeaderBytes + PayloadLimit || bytes[0] != 'E' || bytes[1] != 'T' || bytes[2] != 'P' || bytes[3] != '1' ||
            get(bytes + 4, 2) != 1 || bytes[7] || get(bytes + 8, 2) != HeaderBytes || get(bytes + 10, 2) != size - HeaderBytes ||
            get(bytes + 12, 4) || get(bytes + 56, 8)) return false;
    Packet packet{}; packet.kind = static_cast<Kind>(bytes[6]); packet.sequence = get(bytes + 48, 8);
    std::copy(bytes + 16, bytes + 32, packet.sender.begin()); std::copy(bytes + 32, bytes + 48, packet.recipient.begin());
    if (!nonzero(packet.sender)) return false;
    if (packet.kind == Kind::Hello || packet.kind == Kind::Ack) {
        if (channel || size != HeaderBytes || packet.sequence || (packet.kind == Kind::Hello ? nonzero(packet.recipient) : !nonzero(packet.recipient))) return false;
    } else if (packet.kind == Kind::Data || packet.kind == Kind::Control) {
        if (!packet.sequence || !nonzero(packet.recipient) || size == HeaderBytes ||
                (packet.kind == Kind::Data && (control_socket || channel != 1)) ||
                (packet.kind == Kind::Control && (!control_socket || channel != 2 || size > HeaderBytes + ControlLimit))) return false;
        packet.payload.assign(bytes + HeaderBytes, bytes + size);
    } else return false;
    result = std::move(packet); return true;
}
inline std::string base64(const std::vector<uint8_t>& bytes) {
    constexpr char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string result;
    for (size_t at = 0; at < bytes.size(); at += 3) {
        const uint32_t word = (static_cast<uint32_t>(bytes[at]) << 16) | (at + 1 < bytes.size() ? static_cast<uint32_t>(bytes[at + 1]) << 8 : 0) | (at + 2 < bytes.size() ? bytes[at + 2] : 0);
        result += alphabet[word >> 18]; result += alphabet[(word >> 12) & 63];
        result += at + 1 < bytes.size() ? alphabet[(word >> 6) & 63] : '=';
        result += at + 2 < bytes.size() ? alphabet[word & 63] : '=';
    }
    return result;
}
inline bool unbase64(const std::string& text, size_t maximum, std::vector<uint8_t>& result) {
    if (text.empty() || text.size() % 4 || text.size() > 4 * ((maximum + 2) / 3)) return false;
    constexpr char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::vector<uint8_t> output;
    for (size_t at = 0; at < text.size(); at += 4) {
        uint32_t word = 0; unsigned padding = 0;
        for (size_t digit = 0; digit < 4; ++digit) {
            const char value = text[at + digit];
            if (value == '=') { if (at + 4 != text.size() || digit < 2) return false; ++padding; word <<= 6; }
            else {
                if (padding) return false;
                const auto found = std::find(alphabet, alphabet + 64, value); if (found == alphabet + 64) return false;
                word = (word << 6) | static_cast<uint32_t>(found - alphabet);
            }
        }
        if (padding > 2 || (padding == 2 && (word & 0xffff)) || (padding == 1 && (word & 0xff))) return false;
        output.push_back(static_cast<uint8_t>(word >> 16)); if (padding < 2) output.push_back(static_cast<uint8_t>(word >> 8)); if (!padding) output.push_back(static_cast<uint8_t>(word));
        if (output.size() > maximum) return false;
    }
    result = std::move(output); return true;
}
}  // namespace eos_transport
