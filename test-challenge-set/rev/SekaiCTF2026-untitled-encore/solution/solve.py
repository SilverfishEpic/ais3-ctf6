#!/usr/bin/env python3
import argparse
import os
import shutil
import struct
import subprocess
from pathlib import Path


MAGIC = bytes.fromhex("d6 27 91 a8 4c 03 f5 7e b9 10 6a c2 35 88")
CPP_FLAG_TAG = bytes.fromhex("9e 41 d4 72 0b c9 5a ef 36 a1 80 2d f7 64 b8 13")
SELECTOR = bytes([0x25, 0x00, 0x39, 0x04, 0x1D, 0x6A, 0xE5, 0x7F])
CHART_LEN = 40

OP_BYTE = 0x21
OP_NOTE = 0x44
OP_PROOF = 0x62
OP_KEY = 0x8B
OP_DONE = 0xF0


def rol32(value: int, shift: int) -> int:
    shift &= 31
    return ((value << shift) | (value >> (32 - shift))) & 0xFFFFFFFF


def rol8(value: int, shift: int) -> int:
    shift &= 7
    return ((value << shift) | (value >> (8 - shift))) & 0xFF


def ror8(value: int, shift: int) -> int:
    shift &= 7
    return ((value >> shift) | (value << (8 - shift))) & 0xFF


def sekai_hash(parts: list[bytes]) -> bytes:
    state = bytearray(bytes.fromhex(
        "53 45 4b 41 49 5f 73 62 66 5f 76 69 72 74 75 61"
        "6c 5f 6c 69 76 65 5f 32 30 32 36 21 39 04 1d 6a"
    ))
    n = 0
    for part in parts:
        for b in part:
            i = n & 31
            j = (i * 7 + 13) & 31
            left = rol8(b ^ state[j], n & 7)
            state[i] = rol8((state[i] + left + ((n * 0x3D) & 0xFF)) & 0xFF, (state[j] ^ b) & 7)
            state[j] ^= ror8((state[i] + b + i) & 0xFF, (i + n) & 7)
            n += 1
    for r in range(96):
        i = r & 31
        j = (r * 11 + 5) & 31
        k = (r * 17 + 9) & 31
        state[i] = rol8((state[i] + state[j] + r) & 0xFF, state[k] & 7)
        state[j] ^= ror8((state[i] + state[k] + 0xA5) & 0xFF, r & 7)
    return bytes(state)


def xor_stream(key: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(sekai_hash([b"stream:", key, struct.pack("<I", counter)]))
        counter += 1
    return bytes(out[:length])


def read_sections(elf: bytes) -> dict[str, bytes]:
    if elf[:4] != b"\x7fELF" or elf[4] != 2 or elf[5] != 1:
        raise ValueError("expected a 64-bit little-endian ELF")
    shoff = struct.unpack_from("<Q", elf, 40)[0]
    shentsize = struct.unpack_from("<H", elf, 58)[0]
    shnum = struct.unpack_from("<H", elf, 60)[0]
    shstrndx = struct.unpack_from("<H", elf, 62)[0]
    headers = [elf[shoff + i * shentsize:shoff + (i + 1) * shentsize] for i in range(shnum)]
    shstr = headers[shstrndx]
    names_off = struct.unpack_from("<Q", shstr, 24)[0]
    names_size = struct.unpack_from("<Q", shstr, 32)[0]
    names = elf[names_off:names_off + names_size]
    out = {}
    for header in headers:
        name_off = struct.unpack_from("<I", header, 0)[0]
        end = names.index(b"\x00", name_off)
        name = names[name_off:end].decode()
        off = struct.unpack_from("<Q", header, 24)[0]
        size = struct.unpack_from("<Q", header, 32)[0]
        out[name] = elf[off:off + size]
    return out


def parse_blob(rodata: bytes):
    p = rodata.index(MAGIC) + len(MAGIC)
    version = rodata[p]
    p += 1
    assert version == 2
    flag_len = rodata[p]
    p += 1
    bytecode_len = struct.unpack_from("<H", rodata, p)[0]
    p += 2
    ciphertext = rodata[p:p + flag_len]
    p += flag_len
    bytecode = rodata[p:p + bytecode_len]
    return ciphertext, bytecode


def extract_challenge_elf(blob: bytes) -> bytes:
    if blob[:6] == b"\x7fELF\x02\x01":
        try:
            sections = read_sections(blob)
            parse_blob(sections[".rodata"])
            return blob
        except Exception:
            pass

    start = 0
    while True:
        off = blob.find(b"\x7fELF\x02\x01", start)
        if off == -1:
            raise ValueError("could not find embedded elf64-bpf artifact")
        candidate = blob[off:]
        try:
            sections = read_sections(candidate)
            parse_blob(sections[".rodata"])
            return candidate
        except Exception:
            pass
        start = off + 1


def parse_cpp_flag_blob(blob: bytes) -> bytes | None:
    start = 0
    while True:
        off = blob.find(CPP_FLAG_TAG, start)
        if off == -1:
            return None
        p = off + len(CPP_FLAG_TAG)
        if p + 4 <= len(blob):
            flag_len = struct.unpack_from("<I", blob, p)[0]
            p += 4
            if 0 < flag_len < 128 and p + flag_len <= len(blob):
                return blob[p:p + flag_len]
        start = off + 1


def enc_key(pc: int, slot: int) -> int:
    if slot == 0:
        return (pc * 17 + 0xA3) & 0xFF
    if slot == 1:
        return (pc * 29 + 0x11) & 0xFF
    if slot == 2:
        return (pc * 31 + 0x7B) & 0xFF
    return (pc * 37 + 0xC5) & 0xFF


def decode_instruction(bytecode: bytes, pc: int):
    raw = bytecode[pc:pc + 4]
    return tuple(raw[i] ^ enc_key(pc, i) for i in range(4))


def byte_update_state(state: int, off: int, value: int, mix: int) -> int:
    state = (state + value) & 0xFFFFFFFF
    state ^= mix
    state = (state * 33) & 0xFFFFFFFF
    state = (state + off) & 0xFFFFFFFF
    return state


def note_checks(state: int, idx: int, x: int, y: int):
    t = (state + x * 17 + y * 31 + idx * 73) & 0xFFFF
    b = (t ^ (t >> 8)) & 0xFF
    state = rol32(((state ^ t) + ((x << 8) | y) + b) & 0xFFFFFFFF, (idx & 7) + 1)
    c = state & 0xFF
    return b, c, state


def proof_byte(state: int, chart: bytes, idx: int, b: int, c: int):
    chart_idx = (idx * 7 + 3) & 31
    value = (((state >> ((idx & 3) * 8)) + b + chart[chart_idx] * c + idx) & 0xFF)
    state = rol32((state + value * (c | 1) + b) & 0xFFFFFFFF, (idx & 7) + 1)
    return value, state


def key_byte(state: int, chart: bytes, proof: bytes, idx: int, b: int, c: int):
    proof_idx = (idx * 5 + 1) & 31
    chart_idx = (idx * 11 + 7) & 31
    value = (((state >> ((idx & 3) * 8)) + chart[chart_idx] * (b | 1) + proof[proof_idx] + c + idx) & 0xFF)
    state = rol32(((state ^ value) + b + c) & 0xFFFFFFFF, (c & 7) + 1)
    return value, state


def chart_stats(chart: bytes):
    lane_sums = [0] * 5
    kind_sums = [0] * 3
    tick = 0
    a = 0x6D697275
    b = 0x6E656E65
    c = 0x6B616E61
    mask = 0
    canonical = bytearray(b"MMW4CC-SEKAI-CANON\0")
    for i in range(20):
        x = chart[i * 2]
        delta = chart[i * 2 + 1]
        lane = x & 7
        kind = (x >> 3) & 3
        flick = (x >> 5) & 1
        parity = (x >> 6) & 1
        tick += delta * 120 + ((i ^ lane) & 3)
        a = rol32((a + (lane + 1) * 0x45D9F3B + delta * 0x119DE1F3 + i) & 0xFFFFFFFF,
                  (kind + 1) * 3 + flick)
        b ^= rol32((tick & 0xFFFF) | (x << 16) | (delta << 24), (i + lane) & 31)
        c = (c + (a ^ b) + (kind + 17) * (flick + 3)) & 0xFFFFFFFF
        lane_sums[lane] += delta + kind * 3 + flick * 5 + parity
        kind_sums[kind] += lane + delta + (i & 3)
        mask |= 1 << ((lane * 5 + ((tick // 120) + kind)) % 25)
        canonical.extend(struct.pack("<HBBBB", tick & 0xFFFF, lane, delta, kind, flick))
        canonical.extend(struct.pack("<f", lane + ((delta & 3) * 0.125)))
        canonical.extend(struct.pack("<f", 1.0 + ((kind + flick) % 3)))
    return a, b, c, mask, lane_sums, kind_sums, bytes(canonical)


def cpp_key(chart: bytes) -> bytes:
    a, b, c, mask, lane_sums, kind_sums, canonical = chart_stats(chart)
    trailer = struct.pack("<IIII", a, b, c, mask)
    trailer += bytes(v & 0xFF for v in lane_sums)
    trailer += bytes(v & 0xFF for v in kind_sums)
    return sekai_hash([b"cpp-chart-key:", canonical, trailer])


def packed_domain():
    for x in range(256):
        if (x & 7) <= 4 and ((x >> 3) & 3) <= 2:
            yield x


def solve_bytecode(bytecode: bytes):
    start_state = 0x31C3F00D
    start_chart = [None] * CHART_LEN
    start_proof = [0] * 32
    start_key = [0] * 32
    header = SELECTOR + struct.pack("<I", CHART_LEN)
    decoded = [decode_instruction(bytecode, pc) for pc in range(0, len(bytecode), 4)]

    def rec(ip: int, state: int, chart: list[int | None], proof: list[int], key: list[int]):
        if ip >= len(decoded):
            return None

        op, a, b, c = decoded[ip]
        if op == OP_BYTE:
            assert header[a] == b
            return rec(ip + 1, byte_update_state(state, a, b, c), chart, proof, key)

        elif op == OP_NOTE:
            candidates = []
            for x in packed_domain():
                for y in range(3, 17):
                    bb, cc, new_state = note_checks(state, a, x, y)
                    if bb == b and cc == c:
                        candidates.append((x, y, new_state))
            for x, y, new_state in candidates:
                if chart[a * 2] is not None and (chart[a * 2] != x or chart[a * 2 + 1] != y):
                    continue
                next_chart = chart[:]
                next_chart[a * 2] = x
                next_chart[a * 2 + 1] = y
                result = rec(ip + 1, new_state, next_chart, proof[:], key[:])
                if result is not None:
                    return result
            return None

        elif op == OP_PROOF:
            if any(v is None for v in chart):
                return None
            ch = bytes(v if v is not None else 0 for v in chart)
            value, state = proof_byte(state, ch, a, b, c)
            proof = proof[:]
            proof[a] = value
            return rec(ip + 1, state, chart, proof, key)

        elif op == OP_KEY:
            if any(v is None for v in chart):
                return None
            ch = bytes(chart)
            pr = bytes(proof)
            value, state = key_byte(state, ch, pr, a, b, c)
            key = key[:]
            key[a] = value
            return rec(ip + 1, state, chart, proof, key)

        elif op == OP_DONE:
            if any(v is None for v in chart):
                return None
            return bytes(chart), bytes(proof), bytes(key)

        else:
            raise ValueError(f"unknown vm opcode {op:#x} at {ip * 4:#x}")

    result = rec(0, start_state, start_chart, start_proof, start_key)
    if result is None:
        raise ValueError("no solution")
    return result


def windows_temp_paths() -> tuple[str, Path]:
    out = subprocess.check_output(["cmd.exe", "/c", "echo %TEMP%"], text=True, stderr=subprocess.STDOUT)
    candidates = [line.strip() for line in out.splitlines() if len(line.strip()) >= 3 and line.strip()[1:3] == ":\\"]
    if not candidates:
        raise RuntimeError("could not determine Windows %TEMP%")
    win_temp = candidates[-1].rstrip("\\")
    drive = win_temp[0].lower()
    rest = win_temp[3:].replace("\\", "/")
    return win_temp, Path(f"/mnt/{drive}") / rest


def run_verifier(binary: Path, chart: bytes, calldata: bytes) -> str:
    name = binary.name.lower()
    if name.endswith(".exe"):
        if os.name == "nt":
            return subprocess.check_output([str(binary), "--check-chart", chart.hex()], text=True)
        win_temp, posix_temp = windows_temp_paths()
        posix_temp.mkdir(parents=True, exist_ok=True)
        target = posix_temp / "untitled-encore-solver.exe"
        shutil.copy2(binary, target)
        try:
            return subprocess.check_output([str(target), "--check-chart", chart.hex()], text=True)
        finally:
            try:
                target.unlink()
            except OSError:
                pass
    if name == "untitled-encore-native":
        return subprocess.check_output([str(binary), "--check-chart", chart.hex()], text=True)
    return subprocess.check_output([str(binary), "check", calldata.hex()], text=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    native_build = root / "src" / "build-linux" / "untitled-encore-native"
    native_dist = root / "dist" / "untitled-encore.exe"
    default_program = (
        native_dist
        if native_dist.exists()
        else native_build
        if native_build.exists()
        else native_dist
    )
    default_binary = (
        native_dist
        if native_dist.exists()
        else native_build
        if native_build.exists()
        else native_dist
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("program", nargs="?", default=str(default_program))
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()

    artifact = Path(args.program).read_bytes()
    elf = extract_challenge_elf(artifact)
    cpp_ciphertext = parse_cpp_flag_blob(artifact)
    sections = read_sections(elf)
    ciphertext, bytecode = parse_blob(sections[".rodata"])
    chart, proof, key = solve_bytecode(bytecode)
    if cpp_ciphertext is None:
        flag = bytes(a ^ b for a, b in zip(ciphertext, xor_stream(key, len(ciphertext))))
    else:
        final_key = sekai_hash([b"cpp-final:", cpp_key(chart), key, chart])
        flag = bytes(a ^ b for a, b in zip(cpp_ciphertext, xor_stream(final_key, len(cpp_ciphertext))))
    calldata = SELECTOR + struct.pack("<I", len(chart)) + chart + proof

    print(f"bytecode: {len(bytecode)} bytes")
    print(f"chart:    {chart.hex()}")
    print(f"proof:    {proof.hex()}")
    print(f"calldata: {calldata.hex()}")
    print(f"flag:     {flag.decode()}")

    if args.run:
        if default_binary.suffix.lower() != ".exe" and not os.access(default_binary, os.X_OK):
            raise SystemExit(f"{default_binary} is not executable")
        out = run_verifier(default_binary, chart, calldata)
        print(f"binary:   {out.strip()}")


if __name__ == "__main__":
    main()
