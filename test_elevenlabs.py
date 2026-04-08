#!/usr/bin/env python3
"""Test ElevenLabs API connectivity and credentials."""

import httpx
from config import ELEVENLABS_API_KEY, DEFAULT_VOICE_ID

print(f"Testing ElevenLabs API...")
print(f"API Key: {ELEVENLABS_API_KEY[:20]}..." if ELEVENLABS_API_KEY else "API Key: NOT SET")
print(f"Voice ID: {DEFAULT_VOICE_ID}")
print()

# Test 1: Check account
print("Test 1: Checking account status...")
try:
    response = httpx.get(
        "https://api.elevenlabs.io/v1/user",
        headers={"xi-api-key": ELEVENLABS_API_KEY}
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Account: {data}")
    else:
        print(f"Error: {response.text}")
except Exception as e:
    print(f"Error: {e}")

print()

# Test 2: List available voices
print("Test 2: Listing available voices...")
try:
    response = httpx.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": ELEVENLABS_API_KEY}
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        voices = data.get('voices', [])
        print(f"Found {len(voices)} voices:")
        for voice in voices:
            print(f"  - {voice.get('name')} (ID: {voice.get('voice_id')})")
    else:
        print(f"Error: {response.text}")
except Exception as e:
    print(f"Error: {e}")

print()

# Test 3: Try text-to-speech
print(f"Test 3: Testing TTS with voice ID {DEFAULT_VOICE_ID}...")
try:
    response = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{DEFAULT_VOICE_ID}",
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "text": "Hello, this is a test.",
            "model_id": "eleven_turbo_v2",
        }
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        print(f"✓ Success! Got {len(response.content)} bytes of audio")
    else:
        print(f"✗ Error: {response.text}")
except Exception as e:
    print(f"Error: {e}")
