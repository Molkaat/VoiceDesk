import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///voicedesk.db")
PORT = int(os.getenv("PORT", "8000"))

# ElevenLabs default voice (Rachel)
DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
