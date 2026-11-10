"""Live WhatsApp calling for Kisan Saathi.

Flow: Meta sends a `calls` webhook with an SDP offer -> we answer with WebRTC (aiortc),
pre_accept, wait for the media connection, accept, then talk:
  caller speaks -> simple voice activity detection -> Prisma STT -> agent -> Timbre TTS -> caller hears it
and the reference card (weather, crop table) is sent into the chat while the bot speaks.
"""
import asyncio
import io
import os
import time
import wave
from collections import deque
from fractions import Fraction

import av
import httpx
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
from dotenv import load_dotenv

load_dotenv()

import agent  # noqa: E402
import gnani  # noqa: E402

GRAPH = "https://graph.facebook.com/v25.0"
OUT_RATE = 48000
OUT_SAMPLES = 960  # 20 ms at 48 kHz
OUT_BYTES = OUT_SAMPLES * 2  # 16 bit mono

GREETING = "नमस्ते, मैं किसान साथी हूँ। आप क्या जानना चाहते हैं?"
_GREETING_PCM: bytes | None = None  # generated once, reused for every call

NOT_HEARD = "आवाज़ साफ़ नहीं आई। कृपया फिर से बोलिए।"
OOPS = "कुछ गड़बड़ हो गई। कृपया दोबारा कोशिश करें।"

SESSIONS: dict[str, "CallSession"] = {}


async def graph_call(call_id: str, action: str, sdp: str | None = None) -> bool:
    body = {"messaging_product": "whatsapp", "call_id": call_id, "action": action}
    if sdp:
        body["session"] = {"sdp_type": "answer", "sdp": sdp}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{GRAPH}/{os.getenv('PHONE_NUMBER_ID')}/calls",
            headers={"Authorization": f"Bearer {os.getenv('META_TOKEN')}"},
            json=body,
        )
    print("CALL", action, r.status_code, r.text[:200])
    return r.status_code == 200


class TTSTrack(MediaStreamTrack):
    """Outgoing audio. Plays queued speech, silence otherwise, paced in real time."""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._start = None
        self._n = 0

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError
        if self._start is None:
            self._start = time.time()
        wait = self._start + self._n * OUT_SAMPLES / OUT_RATE - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            data = self.queue.get_nowait()
        except asyncio.QueueEmpty:
            data = bytes(OUT_BYTES)
        frame = av.AudioFrame(format="s16", layout="mono", samples=OUT_SAMPLES)
        frame.planes[0].update(data)
        frame.sample_rate = OUT_RATE
        frame.pts = self._n * OUT_SAMPLES
        frame.time_base = Fraction(1, OUT_RATE)
        self._n += 1
        return frame

    def say(self, pcm: bytes):
        for i in range(0, len(pcm), OUT_BYTES):
            chunk = pcm[i : i + OUT_BYTES]
            if len(chunk) < OUT_BYTES:
                chunk += bytes(OUT_BYTES - len(chunk))
            self.queue.put_nowait(chunk)

    def speaking(self) -> bool:
        return not self.queue.empty()


def wav_to_pcm48(wav_bytes: bytes) -> bytes:
    """Timbre WAV -> raw 48 kHz mono 16 bit PCM."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        ch, rate, width = w.getnchannels(), w.getframerate(), w.getsampwidth()
        pcm = w.readframes(w.getnframes())
    if (ch, rate, width) == (1, OUT_RATE, 2):
        return pcm
    arr = np.frombuffer(pcm, dtype=np.int16)
    if ch == 2:
        arr = arr.reshape(-1, 2).mean(axis=1).astype(np.int16)
    frame = av.AudioFrame.from_ndarray(arr.reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = rate
    res = av.AudioResampler(format="s16", layout="mono", rate=OUT_RATE)
    out = b""
    for f in res.resample(frame) + res.resample(None):
        out += f.to_ndarray().tobytes()
    return out


def pcm16k_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


class CallSession:
    def __init__(self, call_id, offer_sdp, phone, send_text, graph=graph_call,
                 stt=gnani.stt, tts=gnani.tts, reply=agent.agent_reply, rtc_config=None):
        self.call_id, self.offer_sdp, self.phone = call_id, offer_sdp, phone
        self.send_text, self.graph = send_text, graph
        self.stt, self.tts, self.reply = stt, tts, reply
        self.pc = RTCPeerConnection(rtc_config) if rtc_config else RTCPeerConnection()
        self.track = TTSTrack()
        self.responding = False
        self.closed = False
        self.accepted = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.threshold = float(os.getenv("VAD_THRESHOLD", "500"))  # raise it for noisy places
        self.silence_end = float(os.getenv("VAD_SILENCE_SEC", "0.7"))

    async def run(self):
        try:
            @self.pc.on("track")
            def on_track(t):
                if t.kind == "audio":
                    self.tasks.append(asyncio.create_task(self._listen(t)))

            @self.pc.on("connectionstatechange")
            async def on_state():
                print("CALL media state:", self.pc.connectionState)
                if self.pc.connectionState in ("failed", "closed"):
                    await self.close()

            await self.pc.setRemoteDescription(RTCSessionDescription(sdp=self.offer_sdp, type="offer"))
            self.pc.addTrack(self.track)
            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)
            sdp = self.pc.localDescription.sdp

            # Start making the greeting audio now, while the call is connecting, so it plays at pickup.
            greeting = asyncio.create_task(self._greeting_pcm())

            if not await self.graph(self.call_id, "pre_accept", sdp):
                return await self.close()
            for _ in range(200):  # wait up to 20 s for the media connection
                if self.pc.connectionState == "connected":
                    break
                await asyncio.sleep(0.1)
            if not await self.graph(self.call_id, "accept", sdp):
                return await self.close()
            self.accepted.set()
            await asyncio.sleep(0.2)
            pcm = await greeting
            if pcm:
                self.track.say(pcm)
            else:
                await self.speak(GREETING)
        except Exception as e:
            print("CALL ERROR:", repr(e))
            await self.close()

    async def _greeting_pcm(self):
        global _GREETING_PCM
        try:
            if _GREETING_PCM is None:
                _GREETING_PCM = wav_to_pcm48(await self.tts(GREETING))
            return _GREETING_PCM
        except Exception as e:
            print("GREETING PREP ERROR:", repr(e))
            return None

    async def speak(self, text: str, card: str = ""):
        wav = await self.tts(text)
        self.track.say(wav_to_pcm48(wav))
        if card:
            await self.send_text(self.phone, card)

    async def _listen(self, track):
        """Half-duplex listener: collect speech, stop on silence, answer, repeat."""
        await self.accepted.wait()
        res = av.AudioResampler(format="s16", layout="mono", rate=16000)
        pre = deque(maxlen=8)  # ~160 ms before speech starts
        buf: list[bytes] = []
        in_speech, loud, quiet, mute_until = False, 0, 0, 0.0
        max_chunks = 25 * 50  # 25 seconds, Prisma accepts about 30 s
        while not self.closed:
            try:
                frame = await track.recv()
            except MediaStreamError:
                break
            for f in res.resample(frame):
                arr = f.to_ndarray().reshape(-1)
                chunk = arr.tobytes()
                if self.responding or self.track.speaking():
                    in_speech, loud, quiet, buf = False, 0, 0, []
                    pre.clear()
                    mute_until = time.time() + 0.5  # ignore the tail of our own voice
                    continue
                if time.time() < mute_until:
                    continue
                rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
                if not in_speech:
                    pre.append(chunk)
                    loud = loud + 1 if rms > self.threshold else 0
                    if loud >= 3:
                        in_speech, quiet = True, 0
                        buf = list(pre)
                else:
                    buf.append(chunk)
                    quiet = quiet + 1 if rms <= self.threshold else 0
                    if quiet >= int(self.silence_end * 50) or len(buf) >= max_chunks:
                        pcm = b"".join(buf)
                        in_speech, loud, quiet, buf = False, 0, 0, []
                        pre.clear()
                        if len(pcm) >= 16000 * 2 * 0.5:  # at least half a second
                            self.responding = True
                            self.tasks.append(asyncio.create_task(self._respond(pcm)))

    async def _respond(self, pcm16k: bytes):
        try:
            t0 = time.time()
            text = await self.stt(pcm16k_to_wav(pcm16k), "call.wav", "audio/wav")
            t1 = time.time()
            print(f"CALL HEARD: {text}  (STT {t1 - t0:.1f}s)")
            if not text:
                await self.speak(NOT_HEARD)
                return
            answer = await self.reply(self.phone, text)
            t2 = time.time()
            card = agent.pop_card(self.phone)
            await self.speak(answer, card)
            print(f"CALL TIMING: agent {t2 - t1:.1f}s, voice {time.time() - t2:.1f}s, total {time.time() - t0:.1f}s")
        except Exception as e:
            print("CALL RESPOND ERROR:", repr(e))
            try:
                await self.speak(OOPS)
            except Exception:
                pass
        finally:
            while self.track.speaking() and not self.closed:
                await asyncio.sleep(0.05)
            self.responding = False

    async def close(self):
        if self.closed:
            return
        self.closed = True
        SESSIONS.pop(self.call_id, None)
        for t in self.tasks:
            t.cancel()
        try:
            await self.pc.close()
        except Exception:
            pass
        print("CALL closed", self.call_id)


async def handle(value: dict, send_text):
    """Called by the webhook for every `calls` event."""
    for call in value.get("calls", []):
        event, call_id = call.get("event"), call.get("id")
        if event == "connect" and call.get("direction", "USER_INITIATED") == "USER_INITIATED":
            phone = (
                call.get("from")
                or (value.get("contacts") or [{}])[0].get("wa_id")
                or call.get("from_user_id")
            )
            print("CALL incoming from", phone, call_id)
            session = CallSession(call_id, call["session"]["sdp"], phone, send_text)
            SESSIONS[call_id] = session
            asyncio.create_task(session.run())
        elif event == "terminate":
            print("CALL terminate", call_id, call.get("status"), call.get("duration"))
            session = SESSIONS.get(call_id)
            if session:
                await session.close()