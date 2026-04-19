#!/usr/bin/env python3
"""Quick test to verify TTS API is working."""

import asyncio
import sys
sys.path.insert(0, '.')

from tts import synthesize

async def test_tts():
    try:
        print("Testing ElevenLabs TTS API...")
        audio = await synthesize("Hello, this is Sofia from La Bella Ristorante!")
        print(f"✓ Success! Got {len(audio)} bytes of audio")
        return True
    except Exception as e:
        print(f"✗ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_tts())
    sys.exit(0 if success else 1)
