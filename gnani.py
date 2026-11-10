import os
import subprocess

import httpx
import imageio_ffmpeg
from dotenv import load_dotenv

load_dotenv()

STT_URL = "https://api.vachana.ai/stt/v3"
TTS_URL = "https://api.vachana.ai/api/v1/tts/inference"


def _key() -> str:
    return os.getenv("Gnani_API_KEY", "")


async def stt(audio_bytes: bytes, filename: str = "voice.ogg", mime: str = "audio/ogg") -> str:
    """Prisma speech-to-text. Accepts OGG directly (WhatsApp voice notes are OGG/Opus).
    Audio must be short (about 30 seconds or less)."""
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            STT_URL,
            headers={"X-API-Key-ID": _key()},
            files={"audio_file": (filename, audio_bytes, mime)},
            data={"language_code": os.getenv("STT_LANG", "hi-IN")},
        )
    print("STT:", r.status_code, r.text[:300])
    r.raise_for_status()
    return r.json().get("transcript", "").strip()


async def tts(text: str) -> bytes:
    """Timbre text-to-speech. Returns WAV bytes."""
    body = {
        "text": text,
        "voice": os.getenv("TTS_VOICE", "Nalini"),
        "model": "timbre-v2.5",
        "language": "hi-IN",
        "speed": 1.0,
        "audio_config": {
            "sample_rate": 48000,
            "num_channels": 1,
            "sample_width": 2,
            "encoding": "linear_pcm",
            "container": "wav",
        },
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            TTS_URL,
            headers={"X-API-Key-ID": _key(), "Content-Type": "application/json"},
            json=body,
        )
    print("TTS:", r.status_code, len(r.content), "bytes")
    r.raise_for_status()
    return r.content


def wav_to_ogg_opus(wav_bytes: bytes) -> bytes:
    """WhatsApp voice notes need OGG/Opus. Uses the ffmpeg bundled with imageio-ffmpeg."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    p = subprocess.run(
        [ffmpeg, "-y", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
        input=wav_bytes,
        capture_output=True,
    )
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + p.stderr.decode(errors="ignore")[-500:])
    return p.stdout