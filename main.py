import asyncio
import base64
import json
import time
import uuid
import os
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

import agent
import tts
from db import init_db, get_db, CallLog, Booking, Transfer, Restaurant, SessionLocal
from config import PORT


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    import restaurant_seed
    restaurant_seed.seed()
    yield


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="VoiceDesk", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ── REST endpoints ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = BASE_DIR / "static" / "index.html"
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    html_path = BASE_DIR / "static" / "dashboard.html"
    with open(html_path, encoding="utf-8") as f:
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
            "phone": b.phone or b.caller_phone if hasattr(b, "caller_phone") else b.phone,
            "party_size": b.party_size,
            "date": b.date,
            "time": b.time,
            "occasion": b.occasion or "",
            "special_requests": b.special_requests or "",
            "has_allergy": b.has_allergy or False,
            "language": b.language or "en",
            "status": b.status or "confirmed",
            "source": b.source or "phone_agent",
            "created_at": b.created_at.isoformat() + "Z" if b.created_at else "",
        }
        for b in bookings
    ]


@app.get("/api/call-logs")
def list_call_logs(restaurant_id: int = 1, db: Session = Depends(get_db)):
    logs = (
        db.query(CallLog)
        .filter(CallLog.restaurant_id == restaurant_id)
        .order_by(CallLog.created_at.desc())
        .limit(30)
        .all()
    )
    return [
        {
            "id": l.id,
            "session_id": l.session_id,
            "caller_phone": l.caller_phone or "",
            "transcript": l.transcript or "",
            "duration_seconds": l.duration_seconds or 0,
            "turns_to_complete": l.turns_to_complete or 0,
            "language": l.language or "en",
            "escalated": l.escalated or False,
            "escalation_reason": l.escalation_reason or "",
            "created_at": l.created_at.isoformat() + "Z" if l.created_at else "",
        }
        for l in logs
    ]


@app.get("/api/transfers")
def list_transfers(restaurant_id: int = 1, db: Session = Depends(get_db)):
    transfers = (
        db.query(Transfer)
        .filter(Transfer.restaurant_id == restaurant_id, Transfer.resolved == False)
        .order_by(Transfer.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": t.id,
            "reason": t.reason,
            "caller_name": t.caller_name or "",
            "callback": t.callback or "",
            "booking_date": t.booking_date or "",
            "notes": t.notes or "",
            "resolved": t.resolved,
            "created_at": t.created_at.isoformat() + "Z" if t.created_at else "",
        }
        for t in transfers
    ]


@app.patch("/api/transfers/{transfer_id}/resolve")
def resolve_transfer(transfer_id: int, db: Session = Depends(get_db)):
    t = db.query(Transfer).filter(Transfer.id == transfer_id).first()
    if not t:
        return {"error": "not found"}
    t.resolved = True
    db.commit()
    return {"ok": True}


@app.get("/api/tts")
async def tts_endpoint(text: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM"):
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
    call_context = "new"
    escalated = False
    caller_phone = None

    print(f"[SESSION START] {session_id}")

    try:
        greeting_sent = False

        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "caller_phone":
                caller_phone = data.get("phone")
                print(f"[CALLER PHONE] {session_id}: {caller_phone}")
                agent.set_caller_phone(session_id, caller_phone)
                continue

            if msg_type == "context":
                action = data.get("action", "new")
                if action == "cancel_reservation":
                    call_context = "cancel"
                elif action == "modify_reservation":
                    call_context = "modify"
                elif action == "request_manager":
                    call_context = "manager"
                else:
                    call_context = "new"
                print(f"[CONTEXT] {session_id}: {call_context}")
                agent.set_call_context(session_id, call_context)
                continue

            if not greeting_sent and msg_type in ["greeting_request", "transcript"]:
                greeting_sent = True
                try:
                    greeting = await asyncio.get_event_loop().run_in_executor(
                        None, agent.get_greeting, restaurant_id, call_context
                    )
                    print(f"[GREETING] {greeting}")
                    audio_bytes = await tts.synthesize(greeting)
                    audio_b64 = base64.b64encode(audio_bytes).decode()
                    print(f"[AUDIO-GREETING] Generated: {len(audio_bytes)} bytes → {len(audio_b64)} b64 chars")
                    await websocket.send_json({
                        "type": "greeting",
                        "text": greeting,
                        "audio_b64": audio_b64,
                    })
                except Exception as e:
                    print(f"[ERROR] Greeting failed: {e}")
                    import traceback
                    traceback.print_exc()
                    await websocket.send_json({"type": "error", "text": str(e)})
                    return

            if msg_type == "greeting_request":
                continue

            elif msg_type == "transcript":
                user_text = data.get("text", "").strip()
                if not user_text:
                    continue

                print(f"[USER] {user_text}")
                transcript_parts.append(f"User: {user_text}")
                await websocket.send_json({"type": "thinking"})

                try:
                    reply, manager_requested = await asyncio.get_event_loop().run_in_executor(
                        None, agent.chat, session_id, user_text, restaurant_id
                    )
                    print(f"[AGENT] {reply}")
                    transcript_parts.append(f"Agent: {reply}")

                    if manager_requested:
                        escalated = True
                        await websocket.send_json({
                            "type": "transfer",
                            "manager_phone": "+33 758087825",
                        })

                    # Check for fresh booking (created in last 5s)
                    db = SessionLocal()
                    booking_confirmation = None
                    try:
                        latest = (
                            db.query(Booking)
                            .filter(Booking.restaurant_id == restaurant_id)
                            .order_by(Booking.id.desc())
                            .first()
                        )
                        if latest and latest.created_at:
                            age = datetime.now() - latest.created_at
                            if age < timedelta(seconds=5):
                                booking_confirmation = {
                                    "id": latest.id,
                                    "name": latest.name,
                                    "date": str(latest.date),
                                    "time": latest.time,
                                    "guests": latest.party_size,
                                    "occasion": latest.occasion or "",
                                    "has_allergy": latest.has_allergy or False,
                                    "special_requests": latest.special_requests or "",
                                }
                    finally:
                        db.close()

                    audio_bytes = await tts.synthesize(reply)
                    audio_b64 = base64.b64encode(audio_bytes).decode()
                    print(f"[AUDIO] Generated audio: {len(audio_bytes)} bytes → {len(audio_b64)} b64 chars")

                    response_msg = {
                        "type": "reply",
                        "text": reply,
                        "audio_b64": audio_b64,
                    }
                    if booking_confirmation:
                        response_msg["booking_confirmed"] = booking_confirmation

                    await websocket.send_json(response_msg)

                except Exception as e:
                    print(f"[ERROR] Message processing failed: {e}")
                    await websocket.send_json({"type": "error", "text": str(e)})

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
            caller_phone = agent.get_caller_phone(session_id)
            log = CallLog(
                restaurant_id=restaurant_id,
                session_id=session_id,
                caller_phone=caller_phone,
                transcript=full_transcript,
                duration_seconds=duration,
                escalated=escalated,
            )
            db.add(log)
            db.commit()
        finally:
            db.close()

        agent.clear_session(session_id)
        print(f"[SESSION END] {session_id} | {duration}s | {len(transcript_parts)//2} turns")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)