ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /app
RUN apk add --no-cache curl unzip cups cups-client cups-filters
RUN printf 'ChatGPT_Printer ipp://192.168.0.104/ipp/print everywhere\n' > /etc/printcap || true
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

ARG TUNNEL_CLIENT_VERSION=v0.0.14
RUN arch="$(uname -m)" && \
    case "$arch" in \
      x86_64) tc_arch="amd64" ;; \
      aarch64) tc_arch="arm64" ;; \
      *) echo "Unsupported architecture for tunnel-client: $arch" >&2; exit 1 ;; \
    esac && \
    curl -fsSL "https://github.com/openai/tunnel-client/releases/download/$TUNNEL_CLIENT_VERSION/tunnel-client-$TUNNEL_CLIENT_VERSION-linux-$tc_arch.zip" -o /tmp/tunnel-client.zip && \
    unzip -q /tmp/tunnel-client.zip -d /tmp/tunnel-client && \
    bin="$(find /tmp/tunnel-client -type f -name tunnel-client | head -n1)" && \
    test -n "$bin" && install -m 0755 "$bin" /usr/local/bin/tunnel-client && \
    rm -rf /tmp/tunnel-client /tmp/tunnel-client.zip

COPY ha_client.py server.py admin_server.py apto3d_files.py assist_admin.py brain_admin.py brain_service.py /app/
COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD ["/run.sh"]
