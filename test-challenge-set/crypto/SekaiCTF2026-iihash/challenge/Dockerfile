FROM python:3.12-slim-bookworm AS app

WORKDIR /app

RUN pip install --no-cache-dir xxhash

COPY challenge.py /app/challenge.py
COPY flag.txt /app/flag.txt

RUN printf '#!/bin/sh\ncd /app\nexec python3 /app/challenge.py\n' > /app/run && \
    chmod 755 /app/run && \
    chmod 644 /app/flag.txt /app/challenge.py

FROM ghcr.io/es3n1n/jail:latest
COPY --from=app / /srv
ENV JAIL_MEM=512M JAIL_CPU=1000 JAIL_TIME=1200 JAIL_PIDS=20 JAIL_POW=10000 JAIL_PROXY_PROTOCOL=true
