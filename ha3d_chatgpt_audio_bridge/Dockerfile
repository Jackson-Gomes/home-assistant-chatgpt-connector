ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /app
RUN apk add --no-cache pulseaudio-utils
RUN pip3 install --no-cache-dir websockets==15.0.1

COPY bridge.py /app/bridge.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD ["/run.sh"]
