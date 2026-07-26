#include <algorithm>
#include <array>
#include <cctype>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "generated/embedded_bpf.h"
#include "generated/flag_blob.h"
#include "mmw4cc/NoteTypes.h"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

namespace {

constexpr std::uint8_t kSelector[8] = {0x25, 0x00, 0x39, 0x04, 0x1d, 0x6a, 0xe5, 0x7f};
constexpr std::uint8_t kBlobMagic[] = {
    0xd6, 0x27, 0x91, 0xa8, 0x4c, 0x03, 0xf5,
    0x7e, 0xb9, 0x10, 0x6a, 0xc2, 0x35, 0x88,
};
constexpr std::uint8_t kFlagTag[] = {
    0x9e, 0x41, 0xd4, 0x72, 0x0b, 0xc9, 0x5a, 0xef,
    0x36, 0xa1, 0x80, 0x2d, 0xf7, 0x64, 0xb8, 0x13,
};
constexpr std::size_t kChartLen = 40;
constexpr std::size_t kProofLen = 32;
constexpr std::size_t kCalldataLen = 8 + 4 + kChartLen + kProofLen;
constexpr std::uint64_t kInputBase = 0x10000000ULL;
constexpr std::uint64_t kScratchBase = 0x20000000ULL;
constexpr std::uint64_t kBytecodeBase = 0x30000000ULL;

enum InnerOp : std::uint8_t {
    kOpByte = 0x21,
    kOpNote = 0x44,
    kOpProof = 0x62,
    kOpKey = 0x8b,
    kOpDone = 0xf0,
};

struct Insn {
    std::uint8_t opcode{};
    std::size_t dst{};
    std::size_t src{};
    std::int16_t off{};
    std::int32_t imm{};
};

struct Challenge {
    std::vector<Insn> text;
    std::vector<std::uint8_t> bytecode;
};

struct InnerResult {
    std::array<std::uint8_t, kProofLen> proof{};
    std::array<std::uint8_t, 32> expected_key{};
};

enum class Label : std::uint8_t {
    Stream,
    Canonical,
    ChartKey,
    Final,
};

[[noreturn]] void fail() {
    throw std::runtime_error(std::string());
}

std::uint16_t read_u16(const std::vector<std::uint8_t>& input, std::size_t off) {
    if (off + 2 > input.size()) {
        fail();
    }
    return static_cast<std::uint16_t>(input[off]) |
           (static_cast<std::uint16_t>(input[off + 1]) << 8);
}

std::uint32_t read_u32(const std::vector<std::uint8_t>& input, std::size_t off) {
    if (off + 4 > input.size()) {
        fail();
    }
    return static_cast<std::uint32_t>(input[off]) |
           (static_cast<std::uint32_t>(input[off + 1]) << 8) |
           (static_cast<std::uint32_t>(input[off + 2]) << 16) |
           (static_cast<std::uint32_t>(input[off + 3]) << 24);
}

std::uint64_t read_u64(const std::vector<std::uint8_t>& input, std::size_t off) {
    if (off + 8 > input.size()) {
        fail();
    }
    std::uint64_t out = 0;
    for (std::size_t i = 0; i < 8; ++i) {
        out |= static_cast<std::uint64_t>(input[off + i]) << (i * 8);
    }
    return out;
}

std::uint8_t rol8(std::uint8_t value, std::size_t shift) {
    shift &= 7;
    return static_cast<std::uint8_t>((value << shift) | (value >> ((8 - shift) & 7)));
}

std::uint8_t ror8(std::uint8_t value, std::size_t shift) {
    shift &= 7;
    return static_cast<std::uint8_t>((value >> shift) | (value << ((8 - shift) & 7)));
}

std::uint32_t rol32(std::uint32_t value, std::size_t shift) {
    shift &= 31;
    return (value << shift) | (value >> ((32 - shift) & 31));
}

std::array<std::uint8_t, 32> sekai_hash(const std::vector<std::vector<std::uint8_t>>& parts) {
    std::array<std::uint8_t, 32> state = {
        0x53, 0x45, 0x4b, 0x41, 0x49, 0x5f, 0x73, 0x62,
        0x66, 0x5f, 0x76, 0x69, 0x72, 0x74, 0x75, 0x61,
        0x6c, 0x5f, 0x6c, 0x69, 0x76, 0x65, 0x5f, 0x32,
        0x30, 0x32, 0x36, 0x21, 0x39, 0x04, 0x1d, 0x6a,
    };
    std::size_t n = 0;
    for (const auto& part : parts) {
        for (std::uint8_t byte : part) {
            const std::size_t i = n & 31;
            const std::size_t j = (i * 7 + 13) & 31;
            const std::uint8_t left = rol8(static_cast<std::uint8_t>(byte ^ state[j]), n & 7);
            state[i] = rol8(static_cast<std::uint8_t>(state[i] + left + ((n * 0x3d) & 0xff)),
                            (state[j] ^ byte) & 7);
            state[j] ^= ror8(static_cast<std::uint8_t>(state[i] + byte + i), (i + n) & 7);
            ++n;
        }
    }
    for (std::size_t r = 0; r < 96; ++r) {
        const std::size_t i = r & 31;
        const std::size_t j = (r * 11 + 5) & 31;
        const std::size_t k = (r * 17 + 9) & 31;
        state[i] = rol8(static_cast<std::uint8_t>(state[i] + state[j] + r), state[k] & 7);
        state[j] ^= ror8(static_cast<std::uint8_t>(state[i] + state[k] + 0xa5), r & 7);
    }
    return state;
}

std::vector<std::uint8_t> as_vec(const std::array<std::uint8_t, 32>& value) {
    return std::vector<std::uint8_t>(value.begin(), value.end());
}

std::vector<std::uint8_t> decode_label(const std::uint8_t* data, std::size_t len, std::uint8_t seed) {
    std::vector<std::uint8_t> out;
    out.reserve(len);
    for (std::size_t i = 0; i < len; ++i) {
        out.push_back(static_cast<std::uint8_t>(data[i] ^ rol8(static_cast<std::uint8_t>(seed + i * 29U), i)));
    }
    return out;
}

std::vector<std::uint8_t> label(Label id) {
    static constexpr std::uint8_t kStream[] = {
        0xe8, 0x05, 0x25, 0xf2, 0x91, 0xe8, 0x68,
    };
    static constexpr std::uint8_t kCanonical[] = {
        0x0c, 0xf1, 0xba, 0xf0, 0x18, 0x19, 0xd6, 0x55, 0x6c, 0xc7,
        0xcc, 0x4d, 0xf4, 0x14, 0xb4, 0x34, 0x5e, 0x12, 0x2d,
    };
    static constexpr std::uint8_t kChartKey[] = {
        0xa4, 0xb9, 0x74, 0xdd, 0xd0, 0x63, 0x3c,
        0x3b, 0xdb, 0xb4, 0xcc, 0x55, 0x4b, 0x32,
    };
    static constexpr std::uint8_t kFinal[] = {
        0x0e, 0x65, 0xee, 0x0b, 0x78, 0xb6, 0xa8, 0x7d, 0x39, 0xde,
    };

    switch (id) {
    case Label::Stream:
        return decode_label(kStream, sizeof(kStream), 0x9b);
    case Label::Canonical:
        return decode_label(kCanonical, sizeof(kCanonical), 0x41);
    case Label::ChartKey:
        return decode_label(kChartKey, sizeof(kChartKey), 0xc7);
    case Label::Final:
        return decode_label(kFinal, sizeof(kFinal), 0x6d);
    default:
        fail();
    }
}

std::vector<std::uint8_t> xor_stream(const std::array<std::uint8_t, 32>& key, std::size_t length) {
    std::vector<std::uint8_t> out;
    std::uint32_t counter = 0;
    while (out.size() < length) {
        std::vector<std::uint8_t> counter_bytes = {
            static_cast<std::uint8_t>(counter & 0xff),
            static_cast<std::uint8_t>((counter >> 8) & 0xff),
            static_cast<std::uint8_t>((counter >> 16) & 0xff),
            static_cast<std::uint8_t>((counter >> 24) & 0xff),
        };
        auto block = sekai_hash({label(Label::Stream), as_vec(key), counter_bytes});
        out.insert(out.end(), block.begin(), block.end());
        ++counter;
    }
    out.resize(length);
    return out;
}

std::vector<std::uint8_t> elf_section(const std::vector<std::uint8_t>& elf, std::string_view wanted) {
    if (elf.size() < 64 || elf[0] != 0x7f || elf[1] != 'E' || elf[2] != 'L' || elf[3] != 'F' ||
        elf[4] != 2 || elf[5] != 1) {
        fail();
    }
    const std::size_t shoff = static_cast<std::size_t>(read_u64(elf, 40));
    const std::size_t shentsize = read_u16(elf, 58);
    const std::size_t shnum = read_u16(elf, 60);
    const std::size_t shstrndx = read_u16(elf, 62);
    if (shentsize < 64 || shstrndx >= shnum) {
        fail();
    }

    auto section_header = [&](std::size_t idx) {
        const std::size_t off = shoff + idx * shentsize;
        if (off + shentsize > elf.size()) {
            fail();
        }
        return off;
    };

    const std::size_t shstr = section_header(shstrndx);
    const std::size_t names_off = static_cast<std::size_t>(read_u64(elf, shstr + 24));
    const std::size_t names_size = static_cast<std::size_t>(read_u64(elf, shstr + 32));
    if (names_off + names_size > elf.size()) {
        fail();
    }

    for (std::size_t idx = 0; idx < shnum; ++idx) {
        const std::size_t sh = section_header(idx);
        const std::size_t name_off = names_off + read_u32(elf, sh);
        if (name_off >= names_off + names_size) {
            fail();
        }
        std::size_t end = name_off;
        while (end < names_off + names_size && elf[end] != 0) {
            ++end;
        }
        const std::string name(reinterpret_cast<const char*>(&elf[name_off]), end - name_off);
        if (name == wanted) {
            const std::size_t off = static_cast<std::size_t>(read_u64(elf, sh + 24));
            const std::size_t size = static_cast<std::size_t>(read_u64(elf, sh + 32));
            if (off + size > elf.size()) {
                fail();
            }
            return std::vector<std::uint8_t>(elf.begin() + static_cast<std::ptrdiff_t>(off),
                                             elf.begin() + static_cast<std::ptrdiff_t>(off + size));
        }
    }
    fail();
}

std::vector<Insn> parse_text(const std::vector<std::uint8_t>& text) {
    if (text.size() % 8 != 0) {
        fail();
    }
    std::vector<Insn> out;
    out.reserve(text.size() / 8);
    for (std::size_t off = 0; off < text.size(); off += 8) {
        Insn ins;
        ins.opcode = text[off];
        ins.dst = text[off + 1] & 0x0f;
        ins.src = text[off + 1] >> 4;
        ins.off = static_cast<std::int16_t>(static_cast<std::uint16_t>(text[off + 2]) |
                                            (static_cast<std::uint16_t>(text[off + 3]) << 8));
        ins.imm = static_cast<std::int32_t>(read_u32(text, off + 4));
        out.push_back(ins);
    }
    return out;
}

Challenge parse_challenge(const std::vector<std::uint8_t>& program) {
    Challenge chal;
    chal.text = parse_text(elf_section(program, ".text"));
    const auto rodata = elf_section(program, ".rodata");
    const std::vector<std::uint8_t> magic(kBlobMagic, kBlobMagic + sizeof(kBlobMagic));
    auto it = std::search(rodata.begin(), rodata.end(), magic.begin(), magic.end());
    if (it == rodata.end()) {
        fail();
    }
    std::size_t p = static_cast<std::size_t>(std::distance(rodata.begin(), it)) + magic.size();
    if (p + 4 > rodata.size() || rodata[p++] != 2) {
        fail();
    }
    const std::size_t flag_len = rodata[p++];
    const std::size_t bytecode_len = static_cast<std::size_t>(rodata[p]) |
                                     (static_cast<std::size_t>(rodata[p + 1]) << 8);
    p += 2 + flag_len;
    if (p + bytecode_len > rodata.size()) {
        fail();
    }
    chal.bytecode.assign(rodata.begin() + static_cast<std::ptrdiff_t>(p),
                         rodata.begin() + static_cast<std::ptrdiff_t>(p + bytecode_len));
    return chal;
}

std::uint64_t mem_read(std::uint64_t addr,
                       std::size_t size,
                       const std::vector<std::uint8_t>& input,
                       const std::array<std::uint8_t, 64>& scratch,
                       const std::vector<std::uint8_t>& bytecode) {
    auto read_from = [&](std::uint64_t base, const std::uint8_t* data, std::size_t len)
        -> std::optional<std::uint64_t> {
        if (addr < base) {
            return std::nullopt;
        }
        const std::uint64_t rel = addr - base;
        if (rel > len || rel + size > len) {
            return std::nullopt;
        }
        std::uint64_t out = 0;
        for (std::size_t i = 0; i < size; ++i) {
            out |= static_cast<std::uint64_t>(data[rel + i]) << (i * 8);
        }
        return out;
    };
    if (auto out = read_from(kInputBase, input.data(), input.size())) {
        return *out;
    }
    if (auto out = read_from(kScratchBase, scratch.data(), scratch.size())) {
        return *out;
    }
    if (auto out = read_from(kBytecodeBase, bytecode.data(), bytecode.size())) {
        return *out;
    }
    fail();
}

void mem_write(std::uint64_t addr, std::size_t size, std::uint64_t value, std::array<std::uint8_t, 64>& scratch) {
    if (addr < kScratchBase) {
        fail();
    }
    const std::uint64_t rel = addr - kScratchBase;
    if (rel > scratch.size() || rel + size > scratch.size()) {
        fail();
    }
    for (std::size_t i = 0; i < size; ++i) {
        scratch[rel + i] = static_cast<std::uint8_t>((value >> (i * 8)) & 0xff);
    }
}

std::uint64_t alu64(std::uint8_t op, std::uint64_t lhs, std::uint64_t rhs) {
    switch (op) {
    case 0x00:
        return lhs + rhs;
    case 0x10:
        return lhs - rhs;
    case 0x20:
        return lhs * rhs;
    case 0x40:
        return lhs | rhs;
    case 0x50:
        return lhs & rhs;
    case 0x60:
        return lhs << (rhs & 63);
    case 0x70:
        return lhs >> (rhs & 63);
    case 0xa0:
        return lhs ^ rhs;
    case 0xb0:
        return rhs;
    default:
        fail();
    }
}

std::array<std::uint8_t, 32> run_bpf(const Challenge& chal, const std::vector<std::uint8_t>& input) {
    std::array<std::uint64_t, 11> regs{};
    std::array<std::uint8_t, 64> scratch{};
    regs[1] = kInputBase;
    regs[2] = kScratchBase;
    regs[3] = kBytecodeBase;
    regs[4] = chal.bytecode.size();
    regs[5] = input.size();

    std::int64_t pc = 0;
    for (std::size_t steps = 0; steps < 200000; ++steps) {
        if (pc < 0 || static_cast<std::size_t>(pc) >= chal.text.size()) {
            fail();
        }
        const Insn ins = chal.text[static_cast<std::size_t>(pc++)];
        switch (ins.opcode) {
        case 0x61:
        case 0x69:
        case 0x71:
        case 0x79: {
            const std::size_t size = ins.opcode == 0x61 ? 4 : ins.opcode == 0x69 ? 2 : ins.opcode == 0x71 ? 1 : 8;
            const std::uint64_t addr = regs[ins.src] + static_cast<std::uint64_t>(static_cast<std::int64_t>(ins.off));
            regs[ins.dst] = mem_read(addr, size, input, scratch, chal.bytecode);
            break;
        }
        case 0x73: {
            const std::uint64_t addr = regs[ins.dst] + static_cast<std::uint64_t>(static_cast<std::int64_t>(ins.off));
            mem_write(addr, 1, regs[ins.src], scratch);
            break;
        }
        case 0x05:
            pc += ins.off;
            break;
        case 0x15:
        case 0x1d:
        case 0x25:
        case 0x35:
        case 0x3d:
        case 0x55:
        case 0x5d:
        case 0xb5: {
            const std::uint64_t lhs = regs[ins.dst];
            const std::uint64_t rhs = (ins.opcode & 0x08) != 0
                                          ? regs[ins.src]
                                          : static_cast<std::uint64_t>(static_cast<std::int64_t>(ins.imm));
            bool take = false;
            switch (ins.opcode & 0xf0) {
            case 0x10:
                take = lhs == rhs;
                break;
            case 0x20:
                take = lhs > rhs;
                break;
            case 0x30:
                take = lhs >= rhs;
                break;
            case 0x50:
                take = lhs != rhs;
                break;
            case 0xb0:
                take = lhs <= rhs;
                break;
            default:
                fail();
            }
            if (take) {
                pc += ins.off;
            }
            break;
        }
        case 0x95:
            if (regs[0] == 0) {
                std::array<std::uint8_t, 32> key{};
                std::copy_n(scratch.begin(), key.size(), key.begin());
                return key;
            }
            fail();
        default:
            if ((ins.opcode & 0x07) == 0x07 || (ins.opcode & 0x07) == 0x04) {
                const bool is32 = (ins.opcode & 0x07) == 0x04;
                const std::uint8_t op = ins.opcode & 0xf0;
                const std::uint64_t rhs = (ins.opcode & 0x08) != 0
                                              ? regs[ins.src]
                                              : (is32 ? static_cast<std::uint64_t>(static_cast<std::uint32_t>(ins.imm))
                                                      : static_cast<std::uint64_t>(static_cast<std::int64_t>(ins.imm)));
                const std::uint64_t lhs = is32 ? static_cast<std::uint64_t>(static_cast<std::uint32_t>(regs[ins.dst]))
                                               : regs[ins.dst];
                std::uint64_t result = alu64(op, lhs, rhs);
                if (is32) {
                    result = static_cast<std::uint32_t>(result);
                }
                regs[ins.dst] = result;
                break;
            }
            fail();
        }
    }
    fail();
}

std::uint8_t enc_key(std::size_t pc, std::size_t slot) {
    switch (slot) {
    case 0:
        return static_cast<std::uint8_t>(pc * 17 + 0xa3);
    case 1:
        return static_cast<std::uint8_t>(pc * 29 + 0x11);
    case 2:
        return static_cast<std::uint8_t>(pc * 31 + 0x7b);
    default:
        return static_cast<std::uint8_t>(pc * 37 + 0xc5);
    }
}

std::array<std::uint8_t, 4> decode_inner(const std::vector<std::uint8_t>& bytecode, std::size_t pc) {
    if (pc + 4 > bytecode.size()) {
        fail();
    }
    std::array<std::uint8_t, 4> out{};
    for (std::size_t i = 0; i < out.size(); ++i) {
        out[i] = bytecode[pc + i] ^ enc_key(pc, i);
    }
    return out;
}

std::uint32_t byte_update_state(std::uint32_t state, std::uint8_t off, std::uint8_t value, std::uint8_t mix) {
    state += value;
    state ^= mix;
    state *= 33;
    state += off;
    return state;
}

std::tuple<std::uint8_t, std::uint8_t, std::uint32_t> note_checks(std::uint32_t state,
                                                                  std::uint8_t idx,
                                                                  std::uint8_t x,
                                                                  std::uint8_t y) {
    const std::uint32_t t = (state + x * 17U + y * 31U + idx * 73U) & 0xffffU;
    const std::uint8_t b = static_cast<std::uint8_t>(t ^ (t >> 8));
    state = rol32(((state ^ t) + ((static_cast<std::uint32_t>(x) << 8) | y) + b), (idx & 7) + 1);
    const std::uint8_t c = static_cast<std::uint8_t>(state);
    return {b, c, state};
}

std::pair<std::uint8_t, std::uint32_t> proof_byte(std::uint32_t state,
                                                  const std::vector<std::uint8_t>& chart,
                                                  std::uint8_t idx,
                                                  std::uint8_t b,
                                                  std::uint8_t c) {
    const std::size_t chart_idx = (idx * 7 + 3) & 31;
    const std::uint8_t value = static_cast<std::uint8_t>((state >> ((idx & 3) * 8)) + b + chart[chart_idx] * c + idx);
    state = rol32(state + value * (c | 1U) + b, (idx & 7) + 1);
    return {value, state};
}

std::pair<std::uint8_t, std::uint32_t> key_byte(std::uint32_t state,
                                                const std::vector<std::uint8_t>& chart,
                                                const std::array<std::uint8_t, kProofLen>& proof,
                                                std::uint8_t idx,
                                                std::uint8_t b,
                                                std::uint8_t c) {
    const std::size_t proof_idx = (idx * 5 + 1) & 31;
    const std::size_t chart_idx = (idx * 11 + 7) & 31;
    const std::uint8_t value =
        static_cast<std::uint8_t>((state >> ((idx & 3) * 8)) + chart[chart_idx] * (b | 1U) + proof[proof_idx] + c + idx);
    state = rol32((state ^ value) + b + c, (c & 7) + 1);
    return {value, state};
}

InnerResult execute_inner_bytecode(const std::vector<std::uint8_t>& bytecode, const std::vector<std::uint8_t>& chart) {
    if (chart.size() != kChartLen) {
        fail();
    }
    std::vector<std::uint8_t> header(kSelector, kSelector + sizeof(kSelector));
    header.push_back(static_cast<std::uint8_t>(kChartLen));
    header.push_back(0);
    header.push_back(0);
    header.push_back(0);

    InnerResult result;
    std::uint32_t state = 0x31c3f00dU;
    for (std::size_t pc = 0; pc < bytecode.size(); pc += 4) {
        const auto ins = decode_inner(bytecode, pc);
        switch (ins[0]) {
        case kOpByte:
            if (ins[1] >= header.size() || header[ins[1]] != ins[2]) {
                fail();
            }
            state = byte_update_state(state, ins[1], ins[2], ins[3]);
            break;
        case kOpNote: {
            const std::size_t off = static_cast<std::size_t>(ins[1]) * 2;
            if (off + 1 >= chart.size()) {
                fail();
            }
            auto [b, c, next_state] = note_checks(state, ins[1], chart[off], chart[off + 1]);
            if (b != ins[2] || c != ins[3]) {
                fail();
            }
            state = next_state;
            break;
        }
        case kOpProof: {
            auto [value, next_state] = proof_byte(state, chart, ins[1], ins[2], ins[3]);
            result.proof[ins[1]] = value;
            state = next_state;
            break;
        }
        case kOpKey: {
            auto [value, next_state] = key_byte(state, chart, result.proof, ins[1], ins[2], ins[3]);
            result.expected_key[ins[1]] = value;
            state = next_state;
            break;
        }
        case kOpDone:
            return result;
        default:
            fail();
        }
    }
    fail();
}

void put_u16(std::vector<std::uint8_t>& out, std::uint16_t value) {
    out.push_back(static_cast<std::uint8_t>(value & 0xff));
    out.push_back(static_cast<std::uint8_t>((value >> 8) & 0xff));
}

void put_u32(std::vector<std::uint8_t>& out, std::uint32_t value) {
    out.push_back(static_cast<std::uint8_t>(value & 0xff));
    out.push_back(static_cast<std::uint8_t>((value >> 8) & 0xff));
    out.push_back(static_cast<std::uint8_t>((value >> 16) & 0xff));
    out.push_back(static_cast<std::uint8_t>((value >> 24) & 0xff));
}

void put_f32(std::vector<std::uint8_t>& out, float value) {
    static_assert(sizeof(float) == sizeof(std::uint32_t), "float must be 32-bit");
    std::uint32_t raw = 0;
    std::memcpy(&raw, &value, sizeof(raw));
    put_u32(out, raw);
}

struct ChartStats {
    std::uint32_t a{};
    std::uint32_t b{};
    std::uint32_t c{};
    std::uint32_t mask{};
    std::array<std::uint16_t, 5> lane_sums{};
    std::array<std::uint16_t, 3> kind_sums{};
    std::vector<std::uint8_t> canonical;
};

ChartStats chart_stats(const std::vector<std::uint8_t>& chart) {
    if (chart.size() != kChartLen) {
        fail();
    }
    ChartStats st;
    st.a = 0x6d697275U;
    st.b = 0x6e656e65U;
    st.c = 0x6b616e61U;
    st.canonical = label(Label::Canonical);
    std::uint32_t tick = 0;
    for (std::size_t i = 0; i < 20; ++i) {
        const std::uint8_t x = chart[i * 2];
        const std::uint8_t delta = chart[i * 2 + 1];
        const std::uint8_t lane = x & 7;
        const std::uint8_t kind = (x >> 3) & 3;
        const std::uint8_t flick = (x >> 5) & 1;
        const std::uint8_t parity = (x >> 6) & 1;
        if (lane > 4 || kind > 2 || delta < 3 || delta > 16 || (((i * 3 + lane) & 1) != parity)) {
            fail();
        }
        tick += delta * 120U + static_cast<std::uint32_t>((i ^ lane) & 3);
        st.a = rol32(st.a + (lane + 1U) * 0x45d9f3bU + delta * 0x119de1f3U + static_cast<std::uint32_t>(i),
                     (kind + 1U) * 3U + flick);
        st.b ^= rol32((tick & 0xffffU) | (static_cast<std::uint32_t>(x) << 16U) |
                          (static_cast<std::uint32_t>(delta) << 24U),
                      (i + lane) & 31U);
        st.c += (st.a ^ st.b) + (kind + 17U) * (flick + 3U);
        st.lane_sums[lane] = static_cast<std::uint16_t>(st.lane_sums[lane] + delta + kind * 3U + flick * 5U + parity);
        st.kind_sums[kind] = static_cast<std::uint16_t>(st.kind_sums[kind] + lane + delta + (i & 3U));
        st.mask |= 1U << ((lane * 5U + ((tick / 120U) + kind)) % 25U);
        put_u16(st.canonical, static_cast<std::uint16_t>(tick & 0xffffU));
        st.canonical.push_back(lane);
        st.canonical.push_back(delta);
        st.canonical.push_back(kind);
        st.canonical.push_back(flick);
        put_f32(st.canonical, static_cast<float>(lane) + static_cast<float>(delta & 3U) * 0.125f);
        put_f32(st.canonical, 1.0f + static_cast<float>((kind + flick) % 3U));
    }
    return st;
}

void validate_cpp_chart(const std::vector<std::uint8_t>& chart) {
    const ChartStats st = chart_stats(chart);
    if (st.a != kChartCheckA || st.b != kChartCheckB || st.c != kChartCheckC || st.mask != kChartCheckMask) {
        fail();
    }
    for (std::size_t i = 0; i < st.lane_sums.size(); ++i) {
        if (st.lane_sums[i] != kChartLaneSums[i]) {
            fail();
        }
    }
    for (std::size_t i = 0; i < st.kind_sums.size(); ++i) {
        if (st.kind_sums[i] != kChartKindSums[i]) {
            fail();
        }
    }
}

std::array<std::uint8_t, 32> derive_cpp_key(const std::vector<std::uint8_t>& chart) {
    const ChartStats st = chart_stats(chart);
    std::vector<std::uint8_t> trailer;
    put_u32(trailer, st.a);
    put_u32(trailer, st.b);
    put_u32(trailer, st.c);
    put_u32(trailer, st.mask);
    for (auto value : st.lane_sums) {
        trailer.push_back(static_cast<std::uint8_t>(value & 0xff));
    }
    for (auto value : st.kind_sums) {
        trailer.push_back(static_cast<std::uint8_t>(value & 0xff));
    }
    return sekai_hash({label(Label::ChartKey), st.canonical, trailer});
}

std::vector<std::uint8_t> make_calldata(const std::vector<std::uint8_t>& chart, const InnerResult& inner) {
    std::vector<std::uint8_t> calldata(kSelector, kSelector + sizeof(kSelector));
    put_u32(calldata, static_cast<std::uint32_t>(chart.size()));
    calldata.insert(calldata.end(), chart.begin(), chart.end());
    calldata.insert(calldata.end(), inner.proof.begin(), inner.proof.end());
    if (calldata.size() != kCalldataLen) {
        fail();
    }
    return calldata;
}

std::vector<std::uint8_t> decrypt_flag(const std::array<std::uint8_t, 32>& bpf_key,
                                       const std::array<std::uint8_t, 32>& cpp_key,
                                       const std::vector<std::uint8_t>& chart) {
    const std::vector<std::uint8_t> blob(kFlagBlob, kFlagBlob + kFlagBlobLen);
    const std::vector<std::uint8_t> tag(kFlagTag, kFlagTag + sizeof(kFlagTag));
    if (blob.size() < tag.size() + 4 || !std::equal(tag.begin(), tag.end(), blob.begin())) {
        fail();
    }
    const std::size_t len_off = tag.size();
    const std::size_t flag_len = static_cast<std::size_t>(blob[len_off]) |
                                 (static_cast<std::size_t>(blob[len_off + 1]) << 8U) |
                                 (static_cast<std::size_t>(blob[len_off + 2]) << 16U) |
                                 (static_cast<std::size_t>(blob[len_off + 3]) << 24U);
    if (len_off + 4 + flag_len > blob.size()) {
        fail();
    }
    const auto final_key = sekai_hash({label(Label::Final), as_vec(cpp_key), as_vec(bpf_key), chart});
    const auto stream = xor_stream(final_key, flag_len);
    std::vector<std::uint8_t> flag(flag_len);
    for (std::size_t i = 0; i < flag_len; ++i) {
        flag[i] = blob[len_off + 4 + i] ^ stream[i];
    }
    if (flag.size() < 7 || flag[0] != 0x53 || flag[1] != 0x45 || flag[2] != 0x4b ||
        flag[3] != 0x41 || flag[4] != 0x49 || flag[5] != 0x7b || flag.back() != 0x7d) {
        fail();
    }
    return flag;
}

std::vector<std::uint8_t> verify_chart(const std::vector<std::uint8_t>& chart) {
    validate_cpp_chart(chart);
    const std::vector<std::uint8_t> program(kEmbeddedProgram, kEmbeddedProgram + kEmbeddedProgramLen);
    const Challenge chal = parse_challenge(program);
    const InnerResult inner = execute_inner_bytecode(chal.bytecode, chart);
    const auto calldata = make_calldata(chart, inner);
    const auto bpf_key = run_bpf(chal, calldata);
    if (bpf_key != inner.expected_key) {
        fail();
    }
    return decrypt_flag(bpf_key, derive_cpp_key(chart), chart);
}

int hex_value(char c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

std::vector<std::uint8_t> hex_decode(std::string_view input) {
    std::string compact;
    for (char c : input) {
        if (!std::isspace(static_cast<unsigned char>(c))) {
            compact.push_back(c);
        }
    }
    if (compact.size() % 2 != 0) {
        fail();
    }
    std::vector<std::uint8_t> out;
    out.reserve(compact.size() / 2);
    for (std::size_t i = 0; i < compact.size(); i += 2) {
        const int hi = hex_value(compact[i]);
        const int lo = hex_value(compact[i + 1]);
        if (hi < 0 || lo < 0) {
            fail();
        }
        out.push_back(static_cast<std::uint8_t>((hi << 4) | lo));
    }
    return out;
}

std::optional<std::string> read_text_file(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        return std::nullopt;
    }
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

std::vector<std::uint8_t> parse_chart_text(const std::string& text) {
    auto try_hex = [&]() -> std::optional<std::vector<std::uint8_t>> {
        try {
            auto decoded = hex_decode(text);
            if (decoded.size() == kChartLen) {
                return decoded;
            }
        } catch (const std::exception&) {
        }
        return std::nullopt;
    };
    if (auto out = try_hex()) {
        return *out;
    }

    std::vector<int> nums;
    std::string cur;
    for (char ch : text) {
        if (std::isdigit(static_cast<unsigned char>(ch))) {
            cur.push_back(ch);
        } else if (!cur.empty()) {
            nums.push_back(std::stoi(cur));
            cur.clear();
        }
    }
    if (!cur.empty()) {
        nums.push_back(std::stoi(cur));
    }
    if (nums.size() != 80) {
        fail();
    }
    std::vector<std::uint8_t> chart;
    chart.reserve(kChartLen);
    for (std::size_t i = 0; i < 20; ++i) {
        const int lane = nums[i * 4];
        const int delta = nums[i * 4 + 1];
        const int kind = nums[i * 4 + 2];
        const int flick = nums[i * 4 + 3];
        const int parity = static_cast<int>((i * 3 + lane) & 1);
        chart.push_back(static_cast<std::uint8_t>(lane | (kind << 3) | (flick << 5) | (parity << 6)));
        chart.push_back(static_cast<std::uint8_t>(delta));
    }
    return chart;
}

std::vector<std::uint8_t> parse_chart_arg(const std::string& value) {
    if (auto file = read_text_file(value)) {
        return parse_chart_text(*file);
    }
    return parse_chart_text(value);
}

std::string bytes_to_hex(const std::vector<std::uint8_t>& bytes) {
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (auto b : bytes) {
        out << std::setw(2) << static_cast<int>(b);
    }
    return out.str();
}

void usage(const char* argv0) {
    std::cerr << "usage:\n";
    std::cerr << "  " << argv0 << " --check-chart <40-byte-chart-hex-or-file>\n";
    std::cerr << "  " << argv0 << " --print-template\n";
}

std::vector<std::uint8_t> default_chart() {
    std::vector<std::uint8_t> chart(kChartLen);
    for (std::size_t i = 0; i < 20; ++i) {
        const int lane = static_cast<int>((i * 2 + 1) % 5);
        const int delta = 6 + static_cast<int>(i % 5);
        const int kind = static_cast<int>(i % 3);
        const int flick = static_cast<int>((i / 3) & 1);
        const int parity = static_cast<int>((i * 3 + lane) & 1);
        chart[i * 2] = static_cast<std::uint8_t>(lane | (kind << 3) | (flick << 5) | (parity << 6));
        chart[i * 2 + 1] = static_cast<std::uint8_t>(delta);
    }
    return chart;
}

#ifdef _WIN32
struct GuiState {
    std::vector<std::uint8_t> chart = default_chart();
    int selected = 0;
    std::string last_status = "Edit the chart, then press Enter.";
};

GuiState g_gui;

int lane_of(const std::vector<std::uint8_t>& chart, int row) {
    return chart[static_cast<std::size_t>(row) * 2] & 7;
}

int kind_of(const std::vector<std::uint8_t>& chart, int row) {
    return (chart[static_cast<std::size_t>(row) * 2] >> 3) & 3;
}

int flick_of(const std::vector<std::uint8_t>& chart, int row) {
    return (chart[static_cast<std::size_t>(row) * 2] >> 5) & 1;
}

int delta_of(const std::vector<std::uint8_t>& chart, int row) {
    return chart[static_cast<std::size_t>(row) * 2 + 1];
}

void set_note(GuiState& state, int row, int lane, int delta, int kind, int flick) {
    lane = std::max(0, std::min(4, lane));
    delta = std::max(3, std::min(16, delta));
    kind = std::max(0, std::min(2, kind));
    flick = std::max(0, std::min(1, flick));
    const int parity = static_cast<int>((row * 3 + lane) & 1);
    state.chart[static_cast<std::size_t>(row) * 2] = static_cast<std::uint8_t>(lane | (kind << 3) | (flick << 5) | (parity << 6));
    state.chart[static_cast<std::size_t>(row) * 2 + 1] = static_cast<std::uint8_t>(delta);
}

void check_gui_chart(HWND hwnd) {
    try {
        const auto flag = verify_chart(g_gui.chart);
        std::string text(flag.begin(), flag.end());
        g_gui.last_status = text;
        MessageBoxA(hwnd, text.c_str(), "Untitled Encore", MB_OK | MB_ICONINFORMATION);
    } catch (const std::exception&) {
        g_gui.last_status = "MISS";
        MessageBoxA(hwnd, "MISS", "Untitled Encore", MB_OK | MB_ICONWARNING);
    }
}

void paste_chart_from_clipboard(HWND hwnd) {
    if (!OpenClipboard(hwnd)) {
        return;
    }
    HANDLE h = GetClipboardData(CF_TEXT);
    if (h != nullptr) {
        const char* data = static_cast<const char*>(GlobalLock(h));
        if (data != nullptr) {
            try {
                g_gui.chart = parse_chart_text(data);
                g_gui.last_status = "Pasted chart.";
            } catch (const std::exception&) {
                g_gui.last_status = "Clipboard is not a chart.";
            }
            GlobalUnlock(h);
        }
    }
    CloseClipboard();
    InvalidateRect(hwnd, nullptr, TRUE);
}

void draw_text(HDC hdc, int x, int y, const std::string& text) {
    TextOutA(hdc, x, y, text.c_str(), static_cast<int>(text.size()));
}

void draw_gui(HWND hwnd, HDC hdc) {
    RECT rc{};
    GetClientRect(hwnd, &rc);
    HBRUSH bg = CreateSolidBrush(RGB(26, 28, 33));
    FillRect(hdc, &rc, bg);
    DeleteObject(bg);
    SetBkMode(hdc, TRANSPARENT);
    SetTextColor(hdc, RGB(235, 238, 242));

    draw_text(hdc, 24, 18, "Untitled Encore");
    draw_text(hdc, 24, 42, "Arrows: row/lane  Q/A: delta  W/S: kind  F: flick  Ctrl+V: paste chart  Enter: playtest");
    draw_text(hdc, 24, 66, "Status: " + g_gui.last_status);

    const int left = 40;
    const int top = 106;
    const int row_h = 24;
    const int lane_w = 54;
    HPEN grid_pen = CreatePen(PS_SOLID, 1, RGB(70, 76, 86));
    HGDIOBJ old_pen = SelectObject(hdc, grid_pen);
    for (int row = 0; row < 20; ++row) {
        const int y = top + row * row_h;
        RECT row_rc{left - 34, y, left + lane_w * 5 + 220, y + row_h - 2};
        HBRUSH row_brush = CreateSolidBrush(row == g_gui.selected ? RGB(53, 68, 88) : RGB(34, 38, 45));
        FillRect(hdc, &row_rc, row_brush);
        DeleteObject(row_brush);
        draw_text(hdc, left - 28, y + 4, std::to_string(row + 1));
        for (int lane = 0; lane < 5; ++lane) {
            Rectangle(hdc, left + lane * lane_w, y + 3, left + (lane + 1) * lane_w - 4, y + row_h - 5);
        }
        const int lane = lane_of(g_gui.chart, row);
        HBRUSH note_brush = CreateSolidBrush(kind_of(g_gui.chart, row) == 2 ? RGB(244, 116, 96) :
                                             kind_of(g_gui.chart, row) == 1 ? RGB(92, 199, 240) :
                                                                              RGB(105, 220, 154));
        SelectObject(hdc, note_brush);
        Ellipse(hdc, left + lane * lane_w + 16, y + 5, left + lane * lane_w + 34, y + 21);
        DeleteObject(note_brush);
        std::ostringstream meta;
        meta << "d=" << delta_of(g_gui.chart, row) << " k=" << kind_of(g_gui.chart, row)
             << " f=" << flick_of(g_gui.chart, row);
        draw_text(hdc, left + lane_w * 5 + 20, y + 4, meta.str());
    }
    SelectObject(hdc, old_pen);
    DeleteObject(grid_pen);
}

LRESULT CALLBACK wnd_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_PAINT: {
        PAINTSTRUCT ps{};
        HDC hdc = BeginPaint(hwnd, &ps);
        draw_gui(hwnd, hdc);
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_KEYDOWN: {
        const bool ctrl = (GetKeyState(VK_CONTROL) & 0x8000) != 0;
        if (ctrl && wp == 'V') {
            paste_chart_from_clipboard(hwnd);
            return 0;
        }
        int lane = lane_of(g_gui.chart, g_gui.selected);
        int delta = delta_of(g_gui.chart, g_gui.selected);
        int kind = kind_of(g_gui.chart, g_gui.selected);
        int flick = flick_of(g_gui.chart, g_gui.selected);
        switch (wp) {
        case VK_UP:
            g_gui.selected = std::max(0, g_gui.selected - 1);
            break;
        case VK_DOWN:
            g_gui.selected = std::min(19, g_gui.selected + 1);
            break;
        case VK_LEFT:
            set_note(g_gui, g_gui.selected, lane - 1, delta, kind, flick);
            break;
        case VK_RIGHT:
            set_note(g_gui, g_gui.selected, lane + 1, delta, kind, flick);
            break;
        case 'Q':
            set_note(g_gui, g_gui.selected, lane, delta + 1, kind, flick);
            break;
        case 'A':
            set_note(g_gui, g_gui.selected, lane, delta - 1, kind, flick);
            break;
        case 'W':
            set_note(g_gui, g_gui.selected, lane, delta, kind + 1, flick);
            break;
        case 'S':
            set_note(g_gui, g_gui.selected, lane, delta, kind - 1, flick);
            break;
        case 'F':
            set_note(g_gui, g_gui.selected, lane, delta, kind, flick ^ 1);
            break;
        case VK_RETURN:
            check_gui_chart(hwnd);
            break;
        default:
            break;
        }
        InvalidateRect(hwnd, nullptr, TRUE);
        return 0;
    }
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    default:
        return DefWindowProc(hwnd, msg, wp, lp);
    }
}

int run_gui() {
    HINSTANCE inst = GetModuleHandle(nullptr);
    WNDCLASSA wc{};
    wc.lpfnWndProc = wnd_proc;
    wc.hInstance = inst;
    wc.lpszClassName = "UntitledEncoreWindow";
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    if (!RegisterClassA(&wc)) {
        return 1;
    }
    HWND hwnd = CreateWindowExA(0, wc.lpszClassName, "Untitled Encore", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                CW_USEDEFAULT, CW_USEDEFAULT, 720, 690, nullptr, nullptr, inst, nullptr);
    if (hwnd == nullptr) {
        return 1;
    }
    MSG msg{};
    while (GetMessage(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }
    return static_cast<int>(msg.wParam);
}
#endif

} // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 1) {
#ifdef _WIN32
            return run_gui();
#else
            usage(argv[0]);
            return 2;
#endif
        }
        const std::string cmd = argv[1];
        if (cmd == "--check-chart") {
            if (argc < 3) {
                usage(argv[0]);
                return 2;
            }
            const auto chart = parse_chart_arg(argv[2]);
            const auto flag = verify_chart(chart);
            std::cout << std::string(flag.begin(), flag.end()) << "\n";
            return 0;
        }
        if (cmd == "--print-template") {
            const auto chart = default_chart();
            std::cout << bytes_to_hex(chart) << "\n";
            return 0;
        }
        if (cmd == "-h" || cmd == "--help") {
            usage(argv[0]);
            return 0;
        }
        usage(argv[0]);
        return 2;
    } catch (const std::exception& ex) {
        std::cerr << "MISS\n";
        std::cerr << ex.what() << "\n";
        return 1;
    }
}
