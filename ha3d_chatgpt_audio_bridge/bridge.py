import asyncio
import json
import os
import sys
from contextlib import suppress

from websockets.asyncio.server import serve


FIFO_PATH = os.environ.get("HA3D_AUDIO_FIFO", "/tmp/ha3d_chatgpt_mic.raw")
SAMPLE_RATE = int(os.environ.get("HA3D_AUDIO_RATE", "48000"))
CHANNELS = 1
SAMPLE_BYTES = 2
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * CHANNELS * SAMPLE_BYTES * FRAME_MS // 1000
MAX_BUFFER_BYTES = SAMPLE_RATE * CHANNELS * SAMPLE_BYTES * 2


class AudioState:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.active = False
        self.clients = 0

    def start(self) -> None:
        self.buffer.clear()
        self.active = True

    def stop(self) -> None:
        self.active = False
        self.buffer.clear()

    def feed(self, data: bytes) -> None:
        if not self.active or not data:
            return
        self.buffer.extend(data)
        if len(self.buffer) > MAX_BUFFER_BYTES:
            del self.buffer[: len(self.buffer) - MAX_BUFFER_BYTES]

    def frame(self) -> bytes:
        if not self.active:
            return bytes(FRAME_BYTES)
        take = min(FRAME_BYTES, len(self.buffer))
        if take:
            chunk = bytes(self.buffer[:take])
            del self.buffer[:take]
        else:
            chunk = b""
        if len(chunk) < FRAME_BYTES:
            chunk += bytes(FRAME_BYTES - len(chunk))
        return chunk


state = AudioState()


def log(message: str) -> None:
    print(f"[ha3d-audio-bridge] {message}", flush=True)


async def open_fifo_writer() -> int:
    while True:
        try:
            return os.open(FIFO_PATH, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            log(f"waiting for PulseAudio pipe source: {exc}")
            await asyncio.sleep(1)


async def audio_pump() -> None:
    fd = await open_fifo_writer()
    log(f"PCM pump ready: {SAMPLE_RATE} Hz mono s16le")
    loop = asyncio.get_running_loop()
    next_tick = loop.time()
    try:
        while True:
            next_tick += FRAME_MS / 1000
            frame = state.frame()
            try:
                os.write(fd, frame)
            except BlockingIOError:
                pass
            except OSError as exc:
                log(f"pipe write failed, reopening: {exc}")
                with suppress(OSError):
                    os.close(fd)
                fd = await open_fifo_writer()
            await asyncio.sleep(max(0, next_tick - loop.time()))
    finally:
        with suppress(OSError):
            os.close(fd)


async def handler(ws) -> None:
    state.clients += 1
    owns_stream = False
    try:
        await ws.send(json.dumps({
            "type": "hello",
            "ready": True,
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "format": "s16le",
            "clients": state.clients,
        }))
        async for message in ws:
            if isinstance(message, bytes):
                if owns_stream:
                    state.feed(message)
                continue

            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                continue

            command = payload.get("type")
            if command == "ping":
                await ws.send(json.dumps({"type": "pong", "active": state.active}))
            elif command == "start":
                requested_rate = int(payload.get("sample_rate", SAMPLE_RATE))
                if requested_rate != SAMPLE_RATE:
                    await ws.send(json.dumps({
                        "type": "error",
                        "message": f"expected {SAMPLE_RATE} Hz PCM, got {requested_rate} Hz",
                    }))
                    continue
                state.start()
                owns_stream = True
                await ws.send(json.dumps({"type": "started", "sample_rate": SAMPLE_RATE}))
                log("microphone stream started")
            elif command == "stop":
                if owns_stream:
                    state.stop()
                    owns_stream = False
                    log("microphone stream stopped")
                await ws.send(json.dumps({"type": "stopped"}))
    finally:
        state.clients = max(0, state.clients - 1)
        if owns_stream:
            state.stop()
            log("microphone stream disconnected; returning to silence")


async def main() -> None:
    log(f"starting WebSocket ingress on 0.0.0.0:8099; fifo={FIFO_PATH}")
    pump = asyncio.create_task(audio_pump())
    try:
        async with serve(handler, "0.0.0.0", 8099, max_size=2 * 1024 * 1024, compression=None):
            await asyncio.Future()
    finally:
        pump.cancel()
        with suppress(asyncio.CancelledError):
            await pump


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
