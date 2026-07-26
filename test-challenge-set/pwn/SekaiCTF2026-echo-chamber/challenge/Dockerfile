FROM ubuntu:22.04@sha256:ce941a2a18bbb922e434d6d6d2b31e571a5c3826eaf6ada0a41dcc905bd2d906

RUN mkdir -p /app
COPY libc.so.6 /app/
COPY ld-linux-x86-64.so.2 /app/
COPY chal /app/chal
COPY run.sh /app/run.sh
COPY flag.txt /app/flag.txt
RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]