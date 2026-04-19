import asyncio
import base64
import json
import time
import uuid
import os
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Body
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

import agent
import tts
import scheduler
from db import init_db, get_db, CallLog, Booking, Transfer, Restaurant, SessionLocal
from config import PORT


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    import restaurant_seed
    restaurant_seed.seed()
    restaurant_seed.seed_slot_configs()
    scheduler.start_scheduler()
    yield
    scheduler.stop_scheduler()


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
        "avg_cover": r.avg_cover,
    }


@app.patch("/api/restaurant/{restaurant_id}/avg-cover")
def update_avg_cover(restaurant_id: int, avg_cover: int, db: Session = Depends(get_db)):
    """Update the average cover price for a restaurant."""
    r = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not r:
        return {"error": "not found"}
    if avg_cover < 1:
        return {"error": "avg_cover must be at least 1"}
    r.avg_cover = avg_cover
    db.commit()
    return {"ok": True, "avg_cover": r.avg_cover}


@app.get("/api/call-logs/{call_log_id}/transcript")
def get_call_transcript(call_log_id: int, db: Session = Depends(get_db)):
    """Get full transcript for a specific call log."""
    log = db.query(CallLog).filter(CallLog.id == call_log_id).first()
    if not log:
        return {"error": "not found"}
    return {
        "id": log.id,
        "session_id": log.session_id,
        "transcript": log.transcript or "",
        "duration_seconds": log.duration_seconds or 0,
        "turns_to_complete": log.turns_to_complete or 0,
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


@app.patch("/api/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: int, db: Session = Depends(get_db)):
    """Cancel a booking and optionally send SMS confirmation."""
    b = db.query(Booking).filter(Booking.id == booking_id).first()
    if not b:
        return {"error": "not found"}
    
    old_status = b.status
    b.status = "cancelled"
    db.commit()
    
    # Send cancellation SMS if phone is available
    if b.phone:
        try:
            import sms
            sms.send_cancellation_confirmation(
                phone=b.phone,
                guest_name=b.name,
            )
        except Exception as e:
            print(f"[WARNING] Failed to send cancellation SMS: {e}")
    
    return {"ok": True, "old_status": old_status, "new_status": b.status}


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


# ── Availability Settings ──────────────────────────────────────────────────

@app.get("/api/availability")
def list_availability(restaurant_id: int = 1, db: Session = Depends(get_db)):
    """Return all SlotConfig rows grouped by day_of_week."""
    from db import SlotConfig
    slots = (
        db.query(SlotConfig)
        .filter(SlotConfig.restaurant_id == restaurant_id)
        .order_by(SlotConfig.day_of_week, SlotConfig.slot_time)
        .all()
    )
    return [
        {
            "id": s.id,
            "day_of_week": s.day_of_week,
            "slot_time": s.slot_time,
            "service": s.service,
            "max_covers": s.max_covers,
            "is_closed": s.is_closed,
        }
        for s in slots
    ]


@app.patch("/api/availability/{slot_id}")
def update_availability(
    slot_id: int,
    db: Session = Depends(get_db),
    max_covers: int = Body(None),
    is_closed: bool = Body(None),
):
    """Update max_covers and/or is_closed for a slot."""
    from db import SlotConfig
    slot = db.query(SlotConfig).filter(SlotConfig.id == slot_id).first()
    if not slot:
        return {"error": "not found"}
    
    if max_covers is not None:
        slot.max_covers = max_covers
    if is_closed is not None:
        slot.is_closed = is_closed
    
    db.commit()
    db.refresh(slot)
    return {
        "id": slot.id,
        "day_of_week": slot.day_of_week,
        "slot_time": slot.slot_time,
        "service": slot.service,
        "max_covers": slot.max_covers,
        "is_closed": slot.is_closed,
    }


@app.post("/api/availability/close-day")
def close_day(
    day_of_week: str = Body(...),
    restaurant_id: int = Body(1),
    db: Session = Depends(get_db)
):
    """Close all slots for a specific day."""
    from db import SlotConfig
    slots = (
        db.query(SlotConfig)
        .filter(
            SlotConfig.restaurant_id == restaurant_id,
            SlotConfig.day_of_week == day_of_week
        )
        .all()
    )
    for slot in slots:
        slot.is_closed = True
    db.commit()
    return {"ok": True, "closed_count": len(slots)}


@app.get("/api/overrides")
def get_overrides(restaurant_id: int = 1, db: Session = Depends(get_db)):
    """Get all date-specific overrides (e.g., 'closed for wedding')."""
    from db import SlotOverride
    overrides = (
        db.query(SlotOverride)
        .filter(SlotOverride.restaurant_id == restaurant_id)
        .order_by(SlotOverride.date.desc())
        .all()
    )
    return [
        {
            "id": o.id,
            "date": o.date,
            "slot_time": o.slot_time,
            "is_closed": o.is_closed,
            "reason": o.reason,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in overrides
    ]


@app.post("/api/overrides")
def create_override(
    date: str = Body(...),
    slot_time: str = Body(None),
    reason: str = Body(None),
    restaurant_id: int = Body(1),
    db: Session = Depends(get_db)
):
    """
    Create a date-specific override (close specific slot or entire day).
    date: YYYY-MM-DD format
    slot_time: HH:MM format (null = entire day)
    reason: optional reason string (e.g., "wedding", "staff training")
    """
    from db import SlotOverride
    
    # Validate date format
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"error": "Invalid date format. Use YYYY-MM-DD."}
    
    # Validate slot_time format if provided
    if slot_time:
        try:
            datetime.strptime(slot_time, "%H:%M")
        except ValueError:
            return {"error": "Invalid time format. Use HH:MM."}
    
    override = SlotOverride(
        restaurant_id=restaurant_id,
        date=date,
        slot_time=slot_time,
        is_closed=True,
        reason=reason,
    )
    db.add(override)
    db.commit()
    db.refresh(override)
    return {
        "id": override.id,
        "date": override.date,
        "slot_time": override.slot_time,
        "reason": override.reason,
        "created_at": override.created_at.isoformat() if override.created_at else None,
    }


@app.delete("/api/overrides/{override_id}")
def delete_override(override_id: int, restaurant_id: int = 1, db: Session = Depends(get_db)):
    """Remove a date-specific override."""
    from db import SlotOverride
    
    override = (
        db.query(SlotOverride)
        .filter(
            SlotOverride.id == override_id,
            SlotOverride.restaurant_id == restaurant_id,
        )
        .first()
    )
    if not override:
        return {"error": "Override not found"}
    
    db.delete(override)
    db.commit()
    return {"ok": True}


# ── Google Calendar OAuth flow ──────────────────────────────────────────────

@app.get("/auth/google")
def auth_google():
    """Redirect to Google's OAuth consent screen."""
    try:
        from calendar_service import get_oauth_flow
        
        flow = get_oauth_flow()
        # Set redirect_uri to our callback endpoint
        flow.redirect_uri = "http://localhost:8000/auth/google/callback"
        
        auth_uri, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true"
        )
        
        # Store state in session (simplified: store in memory)
        # In production, use a proper session store
        return RedirectResponse(url=auth_uri)
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"OAuth initialization failed: {e}"}


@app.get("/auth/google/callback")
def auth_google_callback(code: str):
    """Handle OAuth callback from Google."""
    try:
        from calendar_service import get_oauth_flow
        from pathlib import Path
        
        flow = get_oauth_flow()
        flow.redirect_uri = "http://localhost:8000/auth/google/callback"
        
        # Exchange code for credentials
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        # Save credentials to token.json
        import json
        token_data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "id_token": credentials.id_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        }
        with open("token.json", "w") as token_file:
            json.dump(token_data, token_file)
        
        print("[CALENDAR] OAuth flow completed, token saved")
        return RedirectResponse(url="/dashboard")
    except Exception as e:
        print(f"[CALENDAR] OAuth callback failed: {e}")
        return {"error": f"OAuth failed: {e}"}


@app.get("/api/calendar/status")
def calendar_status():
    """Check if Google Calendar is connected."""
    try:
        from calendar_service import is_calendar_connected
        connected = is_calendar_connected()
        return {"connected": connected}
    except Exception as e:
        return {"connected": False, "error": str(e)}


# ── Preview Mode ────────────────────────────────────────────────────────────

@app.post("/api/restaurant/preview/on")
def preview_mode_on(restaurant_id: int = 1, db: Session = Depends(get_db)):
    """Enable preview mode for restaurant (skips all database writes)."""
    r = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not r:
        return {"error": "Restaurant not found"}
    r.preview_mode = True
    db.commit()
    print(f"[PREVIEW] Preview mode ENABLED for restaurant {restaurant_id}")
    return {"ok": True, "preview_mode": True}


@app.post("/api/restaurant/preview/off")
def preview_mode_off(restaurant_id: int = 1, db: Session = Depends(get_db)):
    """Disable preview mode for restaurant."""
    r = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not r:
        return {"error": "Restaurant not found"}
    r.preview_mode = False
    db.commit()
    print(f"[PREVIEW] Preview mode DISABLED for restaurant {restaurant_id}")
    return {"ok": True, "preview_mode": False}


@app.get("/api/restaurant/preview/status")
def preview_status(restaurant_id: int = 1, db: Session = Depends(get_db)):
    """Check if preview mode is enabled."""
    r = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not r:
        return {"error": "Restaurant not found"}
    return {"preview_mode": r.preview_mode}


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
    
    # Check if restaurant is in preview mode
    is_preview = False
    try:
        db = SessionLocal()
        try:
            restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
            is_preview = restaurant.preview_mode if restaurant else False
        finally:
            db.close()
    except Exception as e:
        print(f"[WARNING] Could not check preview mode: {e}")
    
    if is_preview:
        print(f"[PREVIEW] Session {session_id} is in PREVIEW MODE")

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
                    # Get LLM response
                    reply, manager_requested = await asyncio.get_event_loop().run_in_executor(
                        None, agent.chat, session_id, user_text, restaurant_id, is_preview
                    )
                    print(f"[AGENT] {reply}")
                    transcript_parts.append(f"Agent: {reply}")

                    if manager_requested:
                        escalated = True
                        await websocket.send_json({
                            "type": "transfer",
                            "manager_phone": "+33 758087825",
                        })

                    # Start TTS synthesis immediately (async, non-blocking)
                    # This allows booking check to happen in parallel
                    tts_task = tts.synthesize(reply)

                    # Check for fresh booking (created in last 10s) - SAFE: filtered by session_id
                    booking_confirmation = None
                    try:
                        db = SessionLocal()
                        try:
                            latest = (
                                db.query(Booking)
                                .filter(Booking.session_id == session_id)
                                .order_by(Booking.id.desc())
                                .first()
                            )
                            if latest and latest.created_at:
                                age = datetime.now() - latest.created_at
                                if age < timedelta(seconds=10):
                                    # Detach from session before closing to avoid "closed transaction" error
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
                    except Exception as e:
                        print(f"[WARNING] Could not fetch booking confirmation: {e}")

                    # Wait for TTS to complete
                    audio_bytes = await tts_task
                    audio_b64 = base64.b64encode(audio_bytes).decode()
                    print(f"[AUDIO] Generated audio: {len(audio_bytes)} bytes → {len(audio_b64)} b64 chars")

                    response_msg = {
                        "type": "reply",
                        "text": reply,
                        "audio_b64": audio_b64,
                    }
                    if booking_confirmation:
                        response_msg["booking_confirmed"] = booking_confirmation
                        
                        # Sync to Google Calendar (non-critical)
                        try:
                            from calendar_service import create_booking_event, is_calendar_connected
                            if is_calendar_connected():
                                db = SessionLocal()
                                try:
                                    booking_obj = db.query(Booking).filter(
                                        Booking.id == booking_confirmation["id"]
                                    ).first()
                                    restaurant_obj = db.query(Restaurant).filter(
                                        Restaurant.id == restaurant_id
                                    ).first()
                                    if booking_obj and restaurant_obj:
                                        # Extract booking data to plain dict (avoid session closure issues)
                                        booking_data = {
                                            "id": booking_obj.id,
                                            "name": booking_obj.name,
                                            "phone": booking_obj.phone,
                                            "party_size": booking_obj.party_size,
                                            "date": booking_obj.date,
                                            "time": booking_obj.time,
                                            "special_requests": booking_obj.special_requests,
                                            "has_allergy": booking_obj.has_allergy,
                                        }
                                        restaurant_data = {
                                            "id": restaurant_obj.id,
                                            "name": restaurant_obj.name,
                                        }
                                        
                                        event_link = await asyncio.get_event_loop().run_in_executor(
                                            None, create_booking_event, booking_data, restaurant_data
                                        )
                                        booking_obj.calendar_event_id = event_link
                                        booking_obj.calendar_synced = True
                                        db.commit()
                                        print(f"[CALENDAR] Synced booking #{booking_obj.id}")
                                finally:
                                    db.close()
                        except Exception as e:
                            print(f"[CALENDAR] Sync failed (non-critical): {e}")

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
        # agent.py owns the call log lifecycle.
        # We just tell it to finalise and clean up.
        agent.clear_session(session_id)
        print(f"[SESSION END] {session_id} | {duration}s | {len(transcript_parts)//2} turns")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)