import asyncio
import base64
import json
import time
import uuid
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

import agent
import tts
from db import init_db, get_db, CallLog, Booking, Restaurant, SessionLocal
from config import PORT


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Seed demo restaurant if needed
    import restaurant_seed
    restaurant_seed.seed()
    yield


app = FastAPI(title="VoiceDesk", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ── REST endpoints ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/api/restaurant/{restaurant_id}")
def get_restaurant(restaurant_id: int = 1, db: Session = Depends(get_db)):
    r = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not r:
        return {"error": "not found"}
    return {
        "id": r.id,
        "name": r.name,
        "agent_name": r.agent_name,
        "voice_id": r.voice_id,
        "hours": r.hours,
        "menu_text": r.menu_text,
    }


@app.get("/api/bookings")
def list_bookings(restaurant_id: int = 1, db: Session = Depends(get_db)):
    bookings = (
        db.query(Booking)
        .filter(Booking.restaurant_id == restaurant_id)
        .order_by(Booking.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": b.id,
            "name": b.name,
            "party_size": b.party_size,
            "date": b.date,
            "time": b.time,
            "created_at": b.created_at.isoformat(),
        }
        for b in bookings
    ]


@app.get("/api/call-logs")
def list_call_logs(restaurant_id: int = 1, db: Session = Depends(get_db)):
    logs = (
        db.query(CallLog)
        .filter(CallLog.restaurant_id == restaurant_id)
        .order_by(CallLog.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": l.id,
            "session_id": l.session_id,
            "transcript": l.transcript,
            "duration_seconds": l.duration_seconds,
            "created_at": l.created_at.isoformat(),
        }
        for l in logs
    ]


@app.get("/api/tts")
async def tts_endpoint(text: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM"):
    """Stream TTS audio for a given text snippet."""
    async def audio_stream():
        async for chunk in tts.synthesize_streaming(text, voice_id):
            yield chunk

    return StreamingResponse(audio_stream(), media_type="audio/mpeg")


# ── WebSocket voice session ─────────────────────────────────────────────────

@app.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket):
    await websocket.accept()
    session_id = str(uuid.uuid4())
    restaurant_id = 1
    transcript_parts: list[str] = []
    start_time = time.time()

    print(f"[SESSION START] {session_id}")

    try:
        # Send opening greeting
        try:
            greeting = await asyncio.get_event_loop().run_in_executor(
                None, agent.get_greeting, restaurant_id
            )
            print(f"[GREETING] {greeting}")
            audio_bytes = await tts.synthesize(greeting)
            audio_b64 = base64.b64encode(audio_bytes).decode()
            await websocket.send_json({
                "type": "greeting",
                "text": greeting,
                "audio_b64": audio_b64,
            })
        except Exception as e:
            print(f"[ERROR] Failed to generate/synthesize greeting: {e}")
            await websocket.send_json({
                "type": "error",
                "text": f"Error: {str(e)}",
            })
            return

        # Main message loop
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "transcript":
                user_text = data.get("text", "").strip()
                if not user_text:
                    continue

                print(f"[USER] {user_text}")
                transcript_parts.append(f"User: {user_text}")

                # Notify client we're thinking
                await websocket.send_json({"type": "thinking"})

                try:
                    # Get AI reply (run in executor to avoid blocking event loop)
                    reply = await asyncio.get_event_loop().run_in_executor(
                        None, agent.chat, session_id, user_text, restaurant_id
                    )
                    print(f"[AGENT] {reply}")
                    transcript_parts.append(f"Agent: {reply}")

                    # Synthesize TTS
                    audio_bytes = await tts.synthesize(reply)
                    audio_b64 = base64.b64encode(audio_bytes).decode()

                    await websocket.send_json({
                        "type": "reply",
                        "text": reply,
                        "audio_b64": audio_b64,
                    })
                except Exception as e:
                    print(f"[ERROR] Failed to process message: {e}")
                    await websocket.send_json({
                        "type": "error",
                        "text": f"Error processing message: {str(e)}",
                    })

            elif msg_type == "end_session":
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[ERROR] WebSocket error: {e}")
    finally:
        duration = int(time.time() - start_time)
        full_transcript = "\n".join(transcript_parts)
        db = SessionLocal()
        try:
            log = CallLog(
                restaurant_id=restaurant_id,
                session_id=session_id,
                transcript=full_transcript,
                duration_seconds=duration,
            )
            db.add(log)
            db.commit()
        finally:
            db.close()

        agent.clear_session(session_id)
        print(f"[SESSION END] {session_id} | duration={duration}s | turns={len(transcript_parts)//2}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
