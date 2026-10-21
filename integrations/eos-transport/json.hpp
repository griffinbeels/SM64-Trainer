// SPDX-License-Identifier: MIT
// Finite private-IPC JSON: flat objects containing ASCII strings or uint64s.
#pragma once
#include <cstdint>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>

namespace eos_transport {
constexpr size_t LineLimit = 4096;
struct Invalid : std::runtime_error { Invalid() : std::runtime_error("invalid_input") {} };
inline void need(bool value) { if (!value) throw Invalid(); }
struct Value { bool number = false; uint64_t integer = 0; std::string text; };
using Object = std::map<std::string, Value>;
class Parser {
    std::string_view input;
    size_t position = 0;
    void whitespace() { while (position < input.size() && (input[position] == ' ' || input[position] == '\t' || input[position] == '\r')) ++position; }
    char take() { need(position < input.size()); return input[position++]; }
    std::string string() {
        need(take() == '"');
        std::string result;
        while (position < input.size()) {
            char value = take();
            if (value == '"') return result;
            if (value == '\\') {
                value = take();
                if (value == '"' || value == '\\' || value == '/') result.push_back(value);
                else if (value == 'u') {
                    unsigned code = 0;
                    for (unsigned at = 0; at < 4; ++at) {
                        const char digit = take();
                        code *= 16;
                        if (digit >= '0' && digit <= '9') code += digit - '0';
                        else if (digit >= 'a' && digit <= 'f') code += digit - 'a' + 10;
                        else if (digit >= 'A' && digit <= 'F') code += digit - 'A' + 10;
                        else throw Invalid();
                    }
                    need(code >= 32 && code <= 126);
                    result.push_back(static_cast<char>(code));
                } else throw Invalid();
            } else { need(static_cast<unsigned char>(value) >= 32 && static_cast<unsigned char>(value) <= 126); result.push_back(value); }
        }
        throw Invalid();
    }
    Value value() {
        need(position < input.size());
        if (input[position] == '"') return Value{false, 0, string()};
        Value result{true, 0, {}};
        const size_t begin = position;
        while (position < input.size() && input[position] >= '0' && input[position] <= '9') {
            const unsigned digit = take() - '0';
            need(result.integer <= (std::numeric_limits<uint64_t>::max() - digit) / 10);
            result.integer = result.integer * 10 + digit;
        }
        need(position > begin && (position - begin == 1 || input[begin] != '0'));
        return result;
    }
public:
    explicit Parser(std::string_view text) : input(text) { need(!text.empty() && text.size() <= LineLimit); }
    Object parse() {
        Object result;
        whitespace(); need(take() == '{'); whitespace();
        if (position < input.size() && input[position] == '}') { ++position; whitespace(); need(position == input.size()); return result; }
        while (true) {
            need(result.size() < 16);
            const auto key = string();
            need(!key.empty() && key.size() <= 40);
            whitespace(); need(take() == ':'); whitespace();
            need(result.emplace(key, value()).second);
            whitespace(); const char separator = take(); whitespace();
            if (separator == '}') { need(position == input.size()); return result; }
            need(separator == ',');
        }
    }
};
inline const Value& field(const Object& object, const char* name) { const auto found = object.find(name); need(found != object.end()); return found->second; }
inline uint64_t number(const Object& object, const char* name) { const auto& value = field(object, name); need(value.number); return value.integer; }
inline const std::string& text(const Object& object, const char* name) { const auto& value = field(object, name); need(!value.number); return value.text; }
inline std::string quote(std::string_view value) {
    std::string result = "\"";
    for (unsigned char byte : value) {
        need(byte >= 32 && byte <= 126);
        if (byte == '"' || byte == '\\') result += '\\';
        result += static_cast<char>(byte);
    }
    return result + '"';
}
inline std::string integer(uint64_t value) { return std::to_string(value); }
inline std::string event(const char* type, const std::string& fields = {}) {
    return "{\"v\":1,\"type\":" + quote(type) + fields + "}";
}
}  // namespace eos_transport
