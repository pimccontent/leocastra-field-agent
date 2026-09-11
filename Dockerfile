# Field Agent image: local SRT listen + packet-bonded forward to LeoCastra.
FROM rust:bookworm AS build
WORKDIR /src
RUN apt-get update \
  && apt-get install -y --no-install-recommends git ca-certificates pkg-config \
  && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/irlserver/srtla_send.git . \
  && cargo build --release --bin srtla_send

FROM debian:bookworm-slim
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates iproute2 python3 iw \
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
