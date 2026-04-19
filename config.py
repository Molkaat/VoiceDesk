import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///voicedesk.db")
PORT = int(os.getenv("PORT", "8000"))

# ElevenLabs default voice (Rachel)
DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

# Twilio SMS configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")  # e.g. "+1234567890"

# Enable SMS confirmations (set to False to disable)
SMS_ENABLED = os.getenv("SMS_ENABLED", "false").lower() == "true"
