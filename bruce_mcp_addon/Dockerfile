FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
ARG TUNNEL_CLIENT_VERSION=v0.0.14
RUN arch="$(uname -m)" && \
    case "$arch" in \
      x86_64) tc_arch="amd64" ;; \
      aarch64) tc_arch="arm64" ;; \
      *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac && \
    curl -fsSL "https://github.com/openai/tunnel-client/releases/download/$TUNNEL_CLIENT_VERSION/tunnel-client-$TUNNEL_CLIENT_VERSION-linux-$tc_arch.zip" -o /tmp/tunnel-client.zip && \
    unzip -q /tmp/tunnel-client.zip -d /tmp/tunnel-client && \
    bin="$(find /tmp/tunnel-client -type f -name tunnel-client | head -n 1)" && \
    test -n "$bin" && install -m 0755 "$bin" /usr/local/bin/tunnel-client && \
    rm -rf /tmp/tunnel-client.zip /tmp/tunnel-client
COPY mqtt_transport.py mcp_server.py addon_entry.py /app/
COPY run.sh /run.sh
RUN chmod 755 /run.sh
CMD ["python", "-u", "/app/addon_entry.py"]
