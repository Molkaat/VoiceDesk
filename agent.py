import json
import re
from datetime import datetime
from typing import Optional

from groq import Groq
from config import GROQ_API_KEY
from db import SessionLocal, Restaurant, Booking, Guest, Transfer, CallLog

client = Groq(api_key=GROQ_API_KEY)
MODEL = "llama-3.3-70b-versatile"

# In-memory conversation histories: session_id -> list of messages
_histories: dict[str, list[dict]] = {}

# Call context per session: 'new' | 'cancel' | 'modify' | 'manager'
_call_context: dict[str, str] = {}

# Caller phone per session (from telephony provider, before the call starts)
_caller_phone: dict[str, str] = {}

# Call log ID per session (created at call start, updated throughout)
_call_log_id: dict[str, int] = {}

# Turn counter per session
_turn_count: dict[str, int] = {}


# ──────────────────────────────────────────────────────────────
# DB HELPERS
# ──────────────────────────────────────────────────────────────

def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    """
    Normalize and validate a phone number.
    - Remove spaces, dashes, parentheses, and other common separators
    - Ensure E.164 format if possible
    - Return None if invalid
    """
    if not phone or not isinstance(phone, str):
        return None
    
    phone = phone.strip()
    if not phone:
        return None
    
    # Remove common separators: spaces, dashes, parentheses, dots
    phone = re.sub(r'[\s\-\(\)\.]', '', phone)
    
    # If it looks corrupted (contains invalid characters like +-), reject it
    # Valid characters for phone are: +, digits, and maybe x for extension
    if not re.match(r'^[\+\d]+$', phone):
        print(f"[PHONE VALIDATION] Rejecting invalid phone format: {phone}")
        return None
    
    # If the phone starts with +, it should be E.164 format
    if phone.startswith('+'):
        # E.164 format: + followed by 1-15 digits
        if re.match(r'^\+\d{1,15}$', phone):
            return phone
        else:
            print(f"[PHONE VALIDATION] Invalid E.164 format: {phone}")
            return None
    
    # If no +, just digits - might be missing country code
    # For now, accept any sequence of digits but prefer those with country codes
    if re.match(r'^\d{7,15}$', phone):
        return phone
    
    print(f"[PHONE VALIDATION] Phone number doesn't match expected format: {phone}")
    return None


def _get_restaurant(restaurant_id: int = 1) -> Optional[Restaurant]:
    db = SessionLocal()
    try:
        return db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    finally:
        db.close()


def _get_or_create_guest(
    db,
    restaurant_id: int,
    phone: Optional[str],
    name: Optional[str],
    language: Optional[str],
) -> Optional[Guest]:
    """Look up a guest by phone. Create if not found. Return None if no phone."""
    if not phone:
        return None

    guest = (
        db.query(Guest)
        .filter(Guest.restaurant_id == restaurant_id, Guest.phone == phone)
        .first()
    )
    if guest:
        # Update name / language if we have newer data
        if name:
            guest.name = name
        if language:
            guest.language = language
        guest.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(guest)
    else:
        guest = Guest(
            restaurant_id=restaurant_id,
            phone=phone,
            name=name,
            language=language,
            visit_count=0,
            no_show_count=0,
        )
        db.add(guest)
        db.commit()
        db.refresh(guest)

    return guest


def _build_returning_guest_context(restaurant_id: int, phone: Optional[str]) -> str:
    """Build the {returning_guest_context} block injected into the system prompt."""
    if not phone:
        return "No caller phone number available. Treat as a first-time guest."

    db = SessionLocal()
    try:
        guest = (
            db.query(Guest)
            .filter(Guest.restaurant_id == restaurant_id, Guest.phone == phone)
            .first()
        )
        if not guest or guest.visit_count == 0:
            return "This appears to be a first-time caller. No prior visit history."

        lines = [
            f"RETURNING GUEST DETECTED — {guest.visit_count} prior visit(s).",
            f"Name on file: {guest.name or 'unknown'}.",
        ]
        if guest.last_visit:
            lines.append(f"Last visit: {guest.last_visit}.")
        if guest.no_show_count and guest.no_show_count > 0:
            lines.append(
                f"Note: {guest.no_show_count} prior no-show(s) — flag for deposit policy if applicable."
            )
        if guest.preferences:
            lines.append(f"Preferences/notes on file: {guest.preferences}")

        return " ".join(lines)
    finally:
        db.close()


def _create_call_log(session_id: str, restaurant_id: int, caller_phone: Optional[str]) -> int:
    """Create a CallLog row at the start of the call. Returns the log ID."""
    db = SessionLocal()
    try:
        log = CallLog(
            restaurant_id=restaurant_id,
            session_id=session_id,
            caller_phone=caller_phone,
            escalated=False,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log.id
    finally:
        db.close()


def _update_call_log(
    call_log_id: int,
    transcript: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    turns: Optional[int] = None,
    language: Optional[str] = None,
    escalated: Optional[bool] = None,
    escalation_reason: Optional[str] = None,
):
    db = SessionLocal()
    try:
        log = db.query(CallLog).filter(CallLog.id == call_log_id).first()
        if not log:
            return
        if transcript is not None:
            log.transcript = transcript
        if duration_seconds is not None:
            log.duration_seconds = duration_seconds
        if turns is not None:
            log.turns_to_complete = turns
        if language is not None:
            log.language = language
        if escalated is not None:
            log.escalated = escalated
        if escalation_reason is not None:
            log.escalation_reason = escalation_reason
        db.commit()
    finally:
        db.close()


def _save_booking(session_id: str, booking_data: dict, restaurant_id: int = 1) -> Optional[Booking]:
    db = SessionLocal()
    try:
        phone = booking_data.get("phone") or _caller_phone.get(session_id) or None
        name = booking_data.get("name", "Guest")
        language = booking_data.get("language", "en")
        occasion = booking_data.get("occasion", "") or None
        special_requests = booking_data.get("special_requests", "") or None
        has_allergy = bool(booking_data.get("has_allergy", False))
        call_log_id = _call_log_id.get(session_id)

        # Upsert guest profile
        guest = _get_or_create_guest(db, restaurant_id, phone, name, language)
        if guest:
            guest.visit_count = (guest.visit_count or 0) + 1
            guest.last_visit = booking_data.get("date")
            # Append allergy/occasion to preferences if not already noted
            if has_allergy and special_requests:
                existing = guest.preferences or ""
                if special_requests not in existing:
                    guest.preferences = f"{existing} | {special_requests}".strip(" |")
            db.commit()

        booking = Booking(
            restaurant_id=restaurant_id,
            guest_id=guest.id if guest else None,
            call_log_id=call_log_id,
            name=name,
            phone=phone,
            party_size=int(booking_data.get("party_size", 1)),
            date=booking_data.get("date"),
            time=booking_data.get("time"),
            occasion=occasion,
            special_requests=special_requests,
            has_allergy=has_allergy,
            language=language,
            source=booking_data.get("source", "phone_agent"),
            status=booking_data.get("status", "confirmed"),
            confirmation_sent=False,
        )
        db.add(booking)
        db.commit()
        db.refresh(booking)

        # Update call log with language + turn count
        if call_log_id:
            _update_call_log(
                call_log_id,
                turns=_turn_count.get(session_id, 0),
                language=language,
            )

        print(
            f"\n[BOOKING CONFIRMED] #{booking.id} | {booking.name} | "
            f"party of {booking.party_size} | {booking.date} at {booking.time} | "
            f"phone={phone} | occasion={occasion} | allergy={has_allergy} | "
            f"requests={special_requests or 'none'}\n"
        )
        return booking
    finally:
        db.close()


def _save_transfer(session_id: str, transfer_data: dict, restaurant_id: int = 1):
    db = SessionLocal()
    try:
        call_log_id = _call_log_id.get(session_id)
        reason = transfer_data.get("reason", "out_of_scope")

        transfer = Transfer(
            restaurant_id=restaurant_id,
            call_log_id=call_log_id,
            reason=reason,
            caller_name=transfer_data.get("caller_name") or None,
            callback=transfer_data.get("callback") or _caller_phone.get(session_id) or None,
            booking_date=transfer_data.get("booking_date") or None,
            notes=transfer_data.get("notes") or None,
            resolved=False,
        )
        db.add(transfer)
        db.commit()
        db.refresh(transfer)

        # Mark call log as escalated
        if call_log_id:
            _update_call_log(call_log_id, escalated=True, escalation_reason=reason)

        print(f"\n[TRANSFER LOGGED] #{transfer.id} | reason={reason} | notes={transfer.notes}\n")
        return transfer
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ──────────────────────────────────────────────────────────────

def _build_system_prompt(
    restaurant: Restaurant,
    context: str = "new",
    returning_guest_context: str = "",
) -> str:
    now = datetime.now()
    today = now.strftime("%A, %d %B %Y")
    current_time = now.strftime("%H:%M")

    if context == "cancel":
        context_instruction = (
            "The caller is calling to CANCEL an existing reservation. "
            "Acknowledge warmly, collect their name and booking date, "
            "then output a TRANSFER_JSON with reason 'modify_reservation'."
        )
    elif context == "modify":
        context_instruction = (
            "The caller is calling to MODIFY an existing reservation. "
            "Acknowledge warmly, ask what they'd like to change, "
            "then output a TRANSFER_JSON with reason 'modify_reservation'."
        )
    elif context == "manager":
        context_instruction = (
            "The caller is requesting to speak with a manager. "
            "Acknowledge warmly and output a TRANSFER_JSON with "
            "reason 'caller_requested_human'."
        )
    else:
        context_instruction = "The caller may want to make a new booking or ask questions."

    contact_email = getattr(restaurant, "contact_email", "") or ""

    return f"""You are {restaurant.agent_name}, the voice concierge for {restaurant.name}.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 1 — IDENTITY & VOICE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Personality: {restaurant.personality}

You are not a chatbot. You are the first impression of {restaurant.name} —
a warm, composed, and knowledgeable concierge available around the clock.
Every caller should feel like a welcomed guest before they've even arrived.

Voice principles:
- Speak like a seasoned maître d': unhurried, precise, genuinely warm.
- Use the caller's name naturally once you have it — not on every sentence.
- Mirror the caller's energy: brief when they're efficient, warmer when chatty.
- One thought, one question, one turn. Never stack questions.
- Never sound scripted. Never sound like a robot reading a form.

What you never say:
- "Sure!", "Absolutely!", "Of course!", "No problem!", "Great choice!"
- "I am an AI" unprompted. If sincerely asked: "I'm the digital concierge
  for {restaurant.name} — here to make things as easy as possible for you."
- Any filler that makes you sound transactional.

What you always do:
- Acknowledge special moments when mentioned:
  "An anniversary — how lovely. We'll make sure the evening is special."
- Stay calm if a caller is frustrated. Never match their tension.
- End every confirmed booking with genuine warmth, not a receipt readout.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 2 — CONTEXT & KNOWLEDGE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Today is {today}. Current time: {current_time}.
Call context: {context_instruction}

Menu:
{restaurant.menu_text}

Opening hours:
{restaurant.hours}

Booking rules:
{restaurant.booking_rules}

FAQs:
{restaurant.faqs}

Knowledge rules:
- Only reference dishes, prices, or specials explicitly in the menu above.
  Never invent or approximate menu details.
- If asked something outside your knowledge base:
  "That's a great question — I want to give you the right answer. Let me
  have someone from our team follow up. May I take your number?"
- Never guess. Graceful uncertainty beats confident misinformation.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 3 — RETURNING GUEST DETECTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{returning_guest_context}

If this is a returning guest (visit_count > 0), acknowledge it warmly
and early — but only once, naturally:
  "Wonderful to hear from you again — we're always glad to have you back."

If they have noted preferences or allergies on file, skip asking for them
again unless they want to update:
  "I have your [preference] on file from last time — shall I note the
  same for this visit?"

Do not read their full history back to them. One natural acknowledgment is enough.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 4 — CALL ROUTING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Detect intent in the first turn and route accordingly:

[A] RESERVATION REQUEST       → follow booking flow (Section 5)
[B] MENU / HOURS INQUIRY      → answer from knowledge base, offer to book
[C] MODIFY EXISTING BOOKING   → collect name + date, output TRANSFER_JSON
[D] COMPLAINT / ISSUE         → de-escalate, collect callback, output TRANSFER_JSON
[E] PRESS / PARTNERSHIP / HR  → "For that I'd recommend reaching out to our
                                 team at {contact_email}. Is there anything
                                 else I can help you with today?"
[F] UNCLEAR INTENT            → one open question:
                                 "Of course — are you looking to make a
                                 reservation, or did you have a question about us?"

Phone call formatting rules (always):
- 1–3 sentences per turn maximum.
- No markdown, bullet points, lists, or line breaks mid-reply.
- Speak numbers naturally: "a table for four", not "party_size: 4".
- Speak dates naturally: "this Saturday" — use formatted values only in JSON.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 5 — BOOKING FLOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Required fields — collect naturally, one per turn:
  1. Full name
  2. Phone number
  3. Party size (integer)
  4. Preferred date
  5. Preferred time

Phone number collection:
- Ask after getting the name: "And the best number to reach you on,
  in case anything changes?"
- If caller hesitates or declines: "No worries at all — I'll note the
  booking without it." Do not push. Phone field will be null.
- Repeat it back once: "Perfect, I have [number] for you."

Field collection rules:
- Parse what the caller volunteers first. "Table for two this Friday at
  eight" → you have party_size, date, time. Ask only for name and phone.
- Resolve relative dates using today ({today}).
  "Next Friday" → calculate exact date. "This weekend" → ask which day.
- Ambiguous party size ("just us", "a few friends"):
  "Lovely — and how many will be joining you in total?"
- For parties exceeding booking rule thresholds (check {{restaurant.booking_rules}}
  for the limit — if not specified, treat 7+ guests as large group):
  STOP the booking flow immediately. Do NOT collect more fields.
  Do NOT output BOOKING_JSON under any circumstances.
  Say: "For a group that size, I want to make sure we take wonderful care of
  you — I'll pass this to our team to confirm within a few hours.
  Could I take the best number to reach you?"
  Then output TRANSFER_JSON with reason "large_group", include all details
  collected so far in the notes field (name, party size, requested date/time).
  The interaction ends here — never confirm a large group booking directly.

Occasion & special requests:
- Do NOT ask proactively.
- Capture if mentioned at any point in the call.
- Treat any allergy with gravity:
  "I've noted that carefully — our kitchen team will be informed."
- Set has_allergy: true in JSON if any food allergy is mentioned.

Availability conflicts:
- BEFORE flagging any time as unavailable, verify it against the opening hours
  in Section 2. A time is only unavailable if it falls OUTSIDE the listed hours.
  Example: if hours are 18:00-23:00, then 20:00 IS available — do not say otherwise.
- Only if a time is genuinely outside hours say: "We're not quite open then —
  the closest we have is [time]. Would that work for you?"
- If the caller corrects you and they are right, acknowledge immediately:
  "You're absolutely right — my apologies. [time] works perfectly."
- Fully booked: offer two alternatives + cancellation waitlist.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 6 — OUTPUT PROTOCOL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOOKING_JSON — output on its own line once all fields are confirmed:

BOOKING_JSON:{{"name":"Full Name","phone":"PHONE_OR_EMPTY","party_size":N,"date":"YYYY-MM-DD","time":"HH:MM","occasion":"OCCASION_OR_EMPTY","special_requests":"brief note or empty string","has_allergy":false,"language":"DETECTED_LANG","source":"phone_agent","status":"confirmed"}}

Hard type rules:
- party_size  → integer only.  ✓ 3   ✗ "3"
- has_allergy → boolean only.  ✓ true / false   ✗ "true"
- date        → YYYY-MM-DD
- time        → HH:MM  (24h)
- phone       → E.164 if possible ("+33612345678"), else raw digits, else ""
- language    → ISO 639-1 code detected from the call ("en", "fr", "es")
- occasion    → plain string if mentioned, else ""
- special_requests → plain string if mentioned, else ""
- status      → always "confirmed" for completed bookings

TRANSFER_JSON — output on its own line for all escalations:

TRANSFER_JSON:{{"reason":"REASON","caller_name":"NAME_OR_EMPTY","callback":"PHONE_OR_EMPTY","booking_date":"YYYY-MM-DD_OR_EMPTY","notes":"brief summary"}}

reason values: modify_reservation | complaint | large_group | out_of_scope | caller_requested_human

CRITICAL:
- JSON line and verbal close must ALWAYS appear together.
- Never output JSON without the spoken confirmation that follows.
- Never output a spoken confirmation without the JSON preceding it.

Verbal close after BOOKING_JSON (vary naturally):
- "[Name], you're confirmed for [day] at [time] — we very much look forward to welcoming you."
- "Wonderful — your table for [N] is set for [day] at [time]. It's going to be a lovely evening."
- "Perfect, [Name]. We'll see you [day] at [time] — can't wait to have you with us."


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 7 — EDGE CASES & RESILIENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Silence / no response:
  "I'm still here — take your time."

Repeated misunderstanding (2+ failed clarifications):
  "I want to make sure I get this right — would it be easier if someone
  from our team gives you a quick call back?"
  → TRANSFER_JSON reason "out_of_scope"

Caller wants to cancel:
  "Of course — I'll make a note right away. Could I confirm the name
  on the reservation and the date?"
  → TRANSFER_JSON reason "modify_reservation"

Caller is upset or complaining:
  Acknowledge once, then: "I'm really sorry to hear that — this isn't
  the experience we want for you. Let me make sure the right person
  from our team reaches out directly. May I take your number?"
  → TRANSFER_JSON reason "complaint"

Caller requests a human:
  "Of course — let me connect you with our team right now."
  → TRANSFER_JSON reason "caller_requested_human"

Inappropriate or prank calls:
  Respond once with polite neutrality. If it continues:
  "Thank you for calling {restaurant.name}. Have a lovely day."
"""


# ──────────────────────────────────────────────────────────────
# JSON EXTRACTION
# ──────────────────────────────────────────────────────────────

def _try_extract_booking(text: str) -> Optional[dict]:
    match = re.search(r"BOOKING_JSON:(\{[^\n]+\})", text)
    if match:
        try:
            data = json.loads(match.group(1))
            print(f"\n[BOOKING EXTRACTED] {data}\n")
            return data
        except json.JSONDecodeError as e:
            print(f"\n[BOOKING JSON ERROR] {match.group(1)} | {e}\n")
    return None


def _try_extract_transfer(text: str) -> Optional[dict]:
    match = re.search(r"TRANSFER_JSON:(\{[^\n]+\})", text)
    if match:
        try:
            data = json.loads(match.group(1))
            print(f"\n[TRANSFER EXTRACTED] {data}\n")
            return data
        except json.JSONDecodeError as e:
            print(f"\n[TRANSFER JSON ERROR] {match.group(1)} | {e}\n")
    return None


def _clean_reply(text: str) -> str:
    """Remove JSON lines from the spoken reply."""
    text = re.sub(r"BOOKING_JSON:\{[^\n]+\}\n?", "", text)
    text = re.sub(r"TRANSFER_JSON:\{[^\n]+\}\n?", "", text)
    return text.strip()


def _is_manager_request(user_message: str) -> bool:
    lower = user_message.lower()
    keywords = [
        "speak with manager", "speak to manager", "talk to manager",
        "get manager", "speak with owner", "speak to owner",
        "manager please", "can i speak to", "can i talk to",
        "i want to speak", "i need to speak", "request manager",
        "ask for manager", "call manager", "get the manager",
        "speak with the manager", "speak to the manager",
    ]
    return any(k in lower for k in keywords)


# ──────────────────────────────────────────────────────────────
# PUBLIC API
# ──────────────────────────────────────────────────────────────

def chat(
    session_id: str,
    user_message: str,
    restaurant_id: int = 1,
) -> tuple[str, bool]:
    """
    Process one caller utterance.
    Returns (spoken_reply, manager_transfer_needed).
    """
    restaurant = _get_restaurant(restaurant_id)
    if not restaurant:
        return "Sorry, I couldn't load the restaurant configuration.", False

    # Initialise session on first turn
    if session_id not in _histories:
        _histories[session_id] = []
        _turn_count[session_id] = 0

        # Create call log row
        phone = _caller_phone.get(session_id)
        log_id = _create_call_log(session_id, restaurant_id, phone)
        _call_log_id[session_id] = log_id

    history = _histories[session_id]
    _turn_count[session_id] += 1

    manager_requested = _is_manager_request(user_message)
    history.append({"role": "user", "content": user_message})

    # Build system prompt with returning-guest context
    context = _call_context.get(session_id, "new")
    phone = _caller_phone.get(session_id)
    returning_ctx = _build_returning_guest_context(restaurant_id, phone)
    system_prompt = _build_system_prompt(restaurant, context, returning_ctx)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system_prompt}] + history,
        temperature=0.7,
        max_tokens=300,
    )

    assistant_text = response.choices[0].message.content or ""
    history.append({"role": "assistant", "content": assistant_text})

    # Handle BOOKING_JSON
    booking_data = _try_extract_booking(assistant_text)
    if booking_data:
        _save_booking(session_id, booking_data, restaurant_id)

    # Handle TRANSFER_JSON
    transfer_data = _try_extract_transfer(assistant_text)
    if transfer_data:
        _save_transfer(session_id, transfer_data, restaurant_id)
        # A transfer always means a human hand-off
        manager_requested = True

    spoken_reply = _clean_reply(assistant_text)
    return spoken_reply, manager_requested


def get_greeting(restaurant_id: int = 1, context: str = "new") -> str:
    """Return the opening greeting for a new call."""
    restaurant = _get_restaurant(restaurant_id)
    if not restaurant:
        return "Hello, how can I help you today?"

    returning_ctx = ""  # No phone yet at greeting stage
    system_prompt = _build_system_prompt(restaurant, context, returning_ctx)

    if context == "cancel":
        user_prompt = "[The caller just connected and wants to CANCEL their reservation. Greet them warmly and briefly acknowledge this.]"
    elif context == "modify":
        user_prompt = "[The caller just connected and wants to MODIFY their reservation. Greet them warmly and briefly acknowledge this.]"
    elif context == "manager":
        user_prompt = "[The caller just connected and is requesting to speak with the manager. Greet them warmly and let them know you're connecting them.]"
    else:
        user_prompt = "[The caller just connected. Greet them warmly in one sentence.]"

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.8,
        max_tokens=80,
    )
    return (
        response.choices[0].message.content
        or f"Welcome to {restaurant.name}, I'm {restaurant.agent_name}. How can I help you today?"
    )


def clear_session(session_id: str):
    """Clean up all in-memory state for a session at call end."""
    # Final call log update with transcript
    call_log_id = _call_log_id.get(session_id)
    if call_log_id:
        history = _histories.get(session_id, [])
        transcript = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in history
        )
        _update_call_log(
            call_log_id,
            transcript=transcript,
            turns=_turn_count.get(session_id, 0),
        )

    _histories.pop(session_id, None)
    _call_context.pop(session_id, None)
    _caller_phone.pop(session_id, None)
    _call_log_id.pop(session_id, None)
    _turn_count.pop(session_id, None)


def set_call_context(session_id: str, context: str):
    if context in ["new", "cancel", "modify", "manager"]:
        _call_context[session_id] = context
        print(f"[CONTEXT SET] {session_id}: {context}")
    else:
        print(f"[CONTEXT ERROR] Unknown context: {context}")


def set_caller_phone(session_id: str, phone: str):
    _caller_phone[session_id] = phone
    print(f"[PHONE SET] {session_id}: {phone}")


def get_caller_phone(session_id: str) -> Optional[str]:
    return _caller_phone.get(session_id)