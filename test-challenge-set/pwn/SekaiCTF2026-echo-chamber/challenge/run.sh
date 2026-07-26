#!/bin/sh

export GLIBC_TUNABLES="glibc.malloc.tcache_count=0:glibc.malloc.tcache_max=0:glibc.malloc.arena_max=8"

cp /app/libc.so.6 /app/ld-linux-x86-64.so.2 /app/chal /tmp
cd /tmp
while [ 1 ]; do
    ./chal
done
