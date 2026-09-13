# Field Agent image: local SRT listen + packet-bonded forward to LeoCastra.
FROM rust:bookworm AS build
WORKDIR /src
RUN apt-get update \
  && apt-get install -y --no-install-recommends git ca-certificates pkg-config python3 \
  && rm -rf /var/lib/apt/lists/*
# Core 2 Duo kits have no POPCNT/AVX. mimalloc v3 and x86-64-v2 SIGILL there.
ENV RUSTFLAGS="-C target-cpu=x86-64"
ENV CARGO_BUILD_JOBS=1
ENV CFLAGS="-O2 -march=x86-64 -mno-sse4.2 -mno-popcnt -mno-avx"
ENV CXXFLAGS="-O2 -march=x86-64 -mno-sse4.2 -mno-popcnt -mno-avx"
COPY docker/patch-srtla-send.py /tmp/patch-srtla-send.py
RUN git clone --depth 1 https://github.com/irlserver/srtla_send.git . \
  && python3 /tmp/patch-srtla-send.py \
  && cargo build --release --bin srtla_send

FROM debian:bookworm-slim
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates iproute2 iputils-ping python3 iw \
  && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/srtla_send /usr/local/bin/srtla_send
COPY leocastra-field-agent.sh /usr/local/bin/leocastra-field-agent.sh
COPY status-server.py /usr/local/lib/leocastra-field-agent-status.py
COPY web /usr/local/share/leocastra-field-agent/web
RUN sed -i 's/\r$//' /usr/local/bin/leocastra-field-agent.sh /usr/local/lib/leocastra-field-agent-status.py \
  && chmod +x /usr/local/bin/leocastra-field-agent.sh /usr/local/bin/srtla_send \
  && chmod 644 /usr/local/lib/leocastra-field-agent-status.py \
    && mkdir -p /var/lib/leocastra /etc/leocastra
ENV SRT_LISTEN_PORT=4001 \
    CONFIG_DIR=/var/lib/leocastra \
    UPLINKS_FILE=/var/lib/leocastra/uplinks \
    SRTLA_SEND_BIN=/usr/local/bin/srtla_send \
    STATUS_PORT=8088 \
    METRICS_BIND=127.0.0.1:9099 \
    WEB_ROOT=/usr/local/share/leocastra-field-agent/web
ENTRYPOINT ["/usr/local/bin/leocastra-field-agent.sh"]
