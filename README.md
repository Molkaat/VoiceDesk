# VoiceDesk

A real-time AI voice agent for restaurants. A browser-based caller speaks to your restaurant's AI assistant (Sofia), who can answer questions about the menu, hours, and take table reservations — all via voice, under 1 second latency.

```
Browser mic → Web Speech API (STT) → FastAPI WebSocket → Groq LLaMA-3.3-70B → ElevenLabs TTS → Browser audio
                                                                 ↕
                                                           SQLite (bookings, logs)
```

---

## API Keys Checklist

| Service | Where to get it | Env var |
|---|---|---|
| **Groq** | https://console.groq.com → API Keys | `GROQ_API_KEY` |
| **ElevenLabs** | https://elevenlabs.io → Profile → API Key | `ELEVENLABS_API_KEY` |

Both are free-tier friendly for development.

---

## Local Setup

### 1. Clone & enter directory
```bash
cd voicedesk
```

### 2. Create a virtual environment
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure environment
```bash
cp .env.example .env
# Edit .env and fill in your API keys
```

### 5. Run the server
```bash
python main.py
# or: uvicorn main:app --reload --port 8000
```

### 6. Open the browser UI
Visit: http://localhost:8000

Click **Start Call**, allow microphone access, and start talking to Sofia.

---

## How it works

1. **Browser** — `static/index.html` uses the **Web Speech API** (`SpeechRecognition`) to capture mic audio and transcribe it locally in the browser (free, no STT API needed).
2. **WebSocket** — Final transcripts are sent over `/ws/voice` to the FastAPI backend.
3. **Groq** — `agent.py` builds a dynamic system prompt from the restaurant's DB config and calls `llama-3.3-70b-versatile` for a short, conversational reply.
4. **ElevenLabs** — `tts.py` converts the reply text to MP3 audio using the `eleven_turbo_v2` model (low latency).
5. **Playback** — The server sends base64-encoded MP3 back over the WebSocket; the browser decodes and plays it. Mic is muted while audio plays to prevent echo.
6. **Bookings** — When the caller provides all booking fields, Claude embeds a `BOOKING_JSON:` line in its reply. The server strips it out before speaking and writes the booking to SQLite.

---

## Project Structure

```
voicedesk/
├── main.py              # FastAPI app, WebSocket handler, REST API
├── agent.py             # Groq conversation logic + booking extraction
├── tts.py               # ElevenLabs TTS (streaming)
├── db.py                # SQLAlchemy models: Restaurant, Booking, CallLog
├── config.py            # Env var loader
├── restaurant_seed.py   # Seeds La Bella Ristorante demo data
├── requirements.txt
├── .env.example
└── static/
    └── index.html       # Browser UI (Web Speech API + WebSocket client)
```

---

## Demo Restaurant: La Bella Ristorante

Auto-seeded on first run:

- **Agent**: Sofia — warm, welcoming, slight Italian flair
- **Voice**: ElevenLabs Rachel (`21m00Tcm4TlvDq8ikWAM`)
- **Menu**: Margherita pizza €12, Pasta carbonara €14, Tiramisu €6, House wine €5/glass, and more
- **Hours**: Tue–Sun, lunch 12:00–15:00, dinner 18:00–23:00. Closed Monday.
- **Bookings**: Required for parties of 4+, max 8 people

---

## REST API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Browser UI |
| `WS` | `/ws/voice` | Real-time voice session |
| `GET` | `/api/restaurant/1` | Restaurant info |
| `GET` | `/api/bookings` | List bookings |
| `GET` | `/api/call-logs` | Session transcripts |
| `GET` | `/api/tts?text=Hello` | Synthesise text to MP3 |

---

## Customising for a real restaurant

Edit `restaurant_seed.py` and change the fields, or insert a new `Restaurant` row directly in SQLite. All fields are plain text — no code changes needed.

To change the ElevenLabs voice, update `ELEVENLABS_VOICE_ID` in `.env` or `voice_id` in the DB row. Browse voices at https://elevenlabs.io/voice-library.

---

## Browser Compatibility

Web Speech API is supported in **Chrome** and **Edge** on desktop. It is not available in Firefox or Safari. For production use, replace Web Speech API with a server-side STT service (Deepgram, AssemblyAI, or Whisper).
