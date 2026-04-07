import httpx
from config import ELEVENLABS_API_KEY, DEFAULT_VOICE_ID

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"


async def synthesize(text: str, voice_id: str = DEFAULT_VOICE_ID) -> bytes:
    """Convert text to speech and return raw MP3 bytes."""
    url = f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2",
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.80,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.content


async def synthesize_streaming(text: str, voice_id: str = DEFAULT_VOICE_ID):
    """Yield MP3 audio chunks as they arrive from ElevenLabs."""
    url = f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}/stream"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2",
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.80,
        },
    }
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk
