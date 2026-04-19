"""
agent.py — VoiceDesk conversation engine

Key changes from previous version:
- Confirmation loop: Sofia reads the booking back before committing
- Backend validation: party size + availability enforced in Python, not prompt
- Correction handling: explicit mid-flow update logic
- Lighter prompt: logic moved to backend, prompt focuses on conversation
- Removed double call log creation (main.py handles its own log separately)
- Added get_available_slots() for real availability checking
"""

import json
import re
from datetime import datetime, date, timedelta
from typing import Optional

from groq import Groq
from config import GROQ_API_KEY
from db import SessionLocal, Restaurant, Booking, Guest, Transfer, CallLog

client = Groq(api_key=GROQ_API_KEY)
MODEL = "llama-3.3-70b-versatile"

# ── In-memory session state ────────────────────────────────────────────────
_histories:     dict[str, list[dict]] = {}
_call_context:  dict[str, str]        = {}
_caller_phone:  dict[str, str]        = {}
_call_log_id:   dict[str, int]        = {}
_turn_count:    dict[str, int]        = {}
_validation_errors: dict[str, int]    = {}  # count consecutive validation errors per session
_call_language: dict[str, str]        = {}  # detected/active language per session

# Pending booking awaiting caller confirmation before we write to DB
# structure: { session_id: { ...booking fields... } }
_pending_booking: dict[str, dict] = {}
_pending_confirmation: dict[str, bool] = {}  # True if awaiting user confirmation on pending booking


# ── DB helpers ─────────────────────────────────────────────────────────────

def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone or not isinstance(phone, str):
        return None
    phone = phone.strip()
    phone = re.sub(r'[\s\-\(\)\.]', '', phone)
    if not re.match(r'^[\+\d]+$', phone):
        return None
    if phone.startswith('+'):
        return phone if re.match(r'^\+\d{1,15}$', phone) else None
    return phone if re.match(r'^\d{7,15}$', phone) else None


def _detect_language(text: str, default: str = "en") -> str:
    """
    Detect language from caller's first message.
    Returns ISO 639-1 code: "en", "fr", "ar", etc.
    Falls back to default if uncertain or message is too short.
    """
    text_lower = text.lower().strip()
    
    # If message is very short (< 4 words), use restaurant default
    word_count = len(text_lower.split())
    if word_count < 4:
        return default
    
    # French indicators (very common words/phrases)
    fr_indicators = ["bonjour", "bonsoir", "oui", "non", "merci", "s'il vous plaît", 
                     "je veux", "je souhaite", "une réservation", "table", "ce soir",
                     "demain", "personnes", "avec", "pour"]
    
    # Arabic indicators (common greetings and words)
    ar_indicators = ["السلام", "مرحبا", "صباح", "مساء", "نعم", "لا", "شكرا",
                     "تحفظ", "طاولة", "حجز"]
    
    # Count French matches
    fr_matches = sum(1 for indicator in fr_indicators if indicator in text_lower)
    
    # Count Arabic matches
    ar_matches = sum(1 for indicator in ar_indicators if indicator in text_lower)
    
    # Check for Arabic script (basic Unicode range check)
    arabic_count = sum(1 for char in text if '\u0600' <= char <= '\u06FF')
    
    # Decide based on indicators and script detection
    if arabic_count > len(text) * 0.3:  # More than 30% Arabic script
        return "ar"
    elif fr_matches >= 2:  # At least 2 French indicators
        return "fr"
    elif ar_matches >= 2:  # At least 2 Arabic indicators
        return "ar"
    
    # Fall back to restaurant default if no strong indicators detected
    return default


def _get_restaurant(restaurant_id: int = 1) -> Optional[Restaurant]:
    db = SessionLocal()
    try:
        return db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    finally:
        db.close()


def _get_max_party_size(restaurant: Restaurant) -> int:
    """Parse max party size from booking rules. Default 8."""
    text = (restaurant.booking_rules or "").lower()
    match = re.search(r'maximum\s+(?:party\s+size\s+(?:is\s+)?)?(\d+)', text)
    if match:
        return int(match.group(1))
    match = re.search(r'max(?:imum)?\s+(\d+)', text)
    if match:
        return int(match.group(1))
    return 8


def _get_available_slots(restaurant_id: int, date_str: str) -> list[str]:
    """
    Return list of time slots that still have capacity on a given date.
    Queries SlotConfig table first. Falls back to text parsing if no SlotConfig exists.
    Checks SlotOverride for date-specific closures (weddings, private events, etc).
    Returns empty list if the date is outside opening hours or has an override closure.
    """
    restaurant = _get_restaurant(restaurant_id)
    if not restaurant:
        return []

    # Determine day of week
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        day_name = d.strftime("%A").lower()
    except ValueError:
        return []

    db = SessionLocal()
    try:
        from db import SlotConfig, SlotOverride
        
        # Check for full-day override first
        full_day_override = (
            db.query(SlotOverride)
            .filter(
                SlotOverride.restaurant_id == restaurant_id,
                SlotOverride.date == date_str,
                SlotOverride.slot_time == None,  # null = entire day
                SlotOverride.is_closed == True,
            )
            .first()
        )
        if full_day_override:
            return []  # Entire day closed by override
        
        # Try to get SlotConfig rows for this day
        slot_configs = (
            db.query(SlotConfig)
            .filter(
                SlotConfig.restaurant_id == restaurant_id,
                SlotConfig.day_of_week == day_name,
            )
            .all()
        )
        
        # If SlotConfig exists, use it; otherwise fall back to text parsing
        if slot_configs:
            # Filter to open slots only
            open_slots = [s.slot_time for s in slot_configs if not s.is_closed]
            
            if not open_slots:  # All slots closed for this day
                return []
            
            # Get slot-specific overrides for this date
            slot_overrides = (
                db.query(SlotOverride)
                .filter(
                    SlotOverride.restaurant_id == restaurant_id,
                    SlotOverride.date == date_str,
                    SlotOverride.slot_time != None,  # specific slot
                    SlotOverride.is_closed == True,
                )
                .all()
            )
            override_times = {o.slot_time for o in slot_overrides}
            
            # Remove override-closed slots
            open_slots = [s for s in open_slots if s not in override_times]
            
            if not open_slots:
                return []
            
            # Count existing bookings per slot for this date
            bookings = (
                db.query(Booking)
                .filter(
                    Booking.restaurant_id == restaurant_id,
                    Booking.date == date_str,
                    Booking.status.in_(["confirmed", "pending"]),
                )
                .all()
            )
            slot_covers: dict[str, int] = {}
            for b in bookings:
                slot_covers[b.time] = slot_covers.get(b.time, 0) + b.party_size
            
            # Filter slots that still have capacity
            available = []
            for slot_config in slot_configs:
                if not slot_config.is_closed and slot_config.slot_time not in override_times:
                    current_covers = slot_covers.get(slot_config.slot_time, 0)
                    if current_covers < slot_config.max_covers:
                        available.append(slot_config.slot_time)
            return available
        
        # Fallback: use text parsing if no SlotConfig found
        open_slots = []
        if restaurant.hours_structured:
            try:
                hours_dict = json.loads(restaurant.hours_structured)
                day_hours = hours_dict.get(day_name)
                if day_hours is None:  # Closed that day
                    return []
                
                # Generate slots from all periods (lunch, dinner, etc)
                for period_name, (open_time, close_time) in day_hours.items():
                    open_h, open_m = map(int, open_time.split(":"))
                    close_h, close_m = map(int, close_time.split(":"))
                    
                    current_h, current_m = open_h, open_m
                    while (current_h, current_m) < (close_h, close_m):
                        slot = f"{current_h:02d}:{current_m:02d}"
                        open_slots.append(slot)
                        current_m += 30
                        if current_m >= 60:
                            current_m -= 60
                            current_h += 1
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        
        # Further fallback to hardcoded slots if structured hours unavailable
        if not open_slots:
            hours_text = (restaurant.hours or "").lower()
            lunch_slots = ["12:00", "12:30", "13:00", "13:30", "14:00", "14:30"]
            dinner_slots = ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30", "22:00", "22:30"]

            if "12:" in hours_text or "lunch" in hours_text:
                open_slots += lunch_slots
            if "18:" in hours_text or "dinner" in hours_text or "19:" in hours_text:
                open_slots += dinner_slots
            if not open_slots:
                open_slots = lunch_slots + dinner_slots

            if re.search(rf'{day_name}.*closed|closed.*{day_name}', hours_text):
                return []
        
        # Check for slot-specific overrides in fallback path too
        slot_overrides = (
            db.query(SlotOverride)
            .filter(
                SlotOverride.restaurant_id == restaurant_id,
                SlotOverride.date == date_str,
                SlotOverride.slot_time != None,
                SlotOverride.is_closed == True,
            )
            .all()
        )
        override_times = {o.slot_time for o in slot_overrides}
        open_slots = [s for s in open_slots if s not in override_times]
        
        # Count bookings and filter available slots
        max_covers = 30  # Default
        bookings = (
            db.query(Booking)
            .filter(
                Booking.restaurant_id == restaurant_id,
                Booking.date == date_str,
                Booking.status.in_(["confirmed", "pending"]),
            )
            .all()
        )
        slot_covers: dict[str, int] = {}
        for b in bookings:
            slot_covers[b.time] = slot_covers.get(b.time, 0) + b.party_size

        available = [
            slot for slot in open_slots
            if slot_covers.get(slot, 0) < max_covers
        ]
        return available
    finally:
        db.close()


def _check_slot_available(restaurant_id: int, date_str: str, time_str: str, party_size: int) -> bool:
    """Return True if the requested slot can accommodate the party."""
    restaurant = _get_restaurant(restaurant_id)
    if not restaurant:
        return False

    covers_match = re.search(
        r'max(?:imum)?\s+(?:covers?|seats?|tables?)\s+(?:per\s+slot\s+)?(?:is\s+)?(\d+)',
        (restaurant.booking_rules or "").lower()
    )
    max_covers = int(covers_match.group(1)) if covers_match else 30

    db = SessionLocal()
    try:
        existing = (
            db.query(Booking)
            .filter(
                Booking.restaurant_id == restaurant_id,
                Booking.date == date_str,
                Booking.time == time_str,
                Booking.status.in_(["confirmed", "pending"]),
            )
            .all()
        )
        current_covers = sum(b.party_size for b in existing)
        return (current_covers + party_size) <= max_covers
    finally:
        db.close()


def _build_returning_guest_context(restaurant_id: int, phone: Optional[str]) -> str:
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
            return "First-time caller. No prior visit history."
        lines = [f"RETURNING GUEST — {guest.visit_count} prior visit(s). Name on file: {guest.name or 'unknown'}."]
        if guest.last_visit:
            lines.append(f"Last visit: {guest.last_visit}.")
        if guest.no_show_count and guest.no_show_count > 0:
            lines.append(f"Note: {guest.no_show_count} prior no-show(s).")
        if guest.preferences:
            lines.append(f"Preferences on file: {guest.preferences}")
        return " ".join(lines)
    finally:
        db.close()


def _create_call_log(session_id: str, restaurant_id: int, caller_phone: Optional[str]) -> int:
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


def _get_or_create_guest(db, restaurant_id, phone, name, language):
    if not phone:
        return None
    guest = (
        db.query(Guest)
        .filter(Guest.restaurant_id == restaurant_id, Guest.phone == phone)
        .first()
    )
    if guest:
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


def _save_booking(session_id: str, booking_data: dict, restaurant_id: int = 1, is_preview: bool = False) -> Optional[Booking]:
    # If in preview mode, just log and return without database writes
    if is_preview:
        print(f"[PREVIEW] Booking would have been saved: {booking_data}")
        return None
    
    db = SessionLocal()
    try:
        phone = booking_data.get("phone") or _caller_phone.get(session_id) or None
        name = booking_data.get("name", "Guest")
        language = booking_data.get("language", "en")
        occasion = booking_data.get("occasion") or None
        special_requests = booking_data.get("special_requests") or None
        has_allergy = bool(booking_data.get("has_allergy", False))
        call_log_id = _call_log_id.get(session_id)
        party_size = int(booking_data.get("party_size", 1))
        date_str = booking_data.get("date")
        time_str = booking_data.get("time")

        # Use transaction for race condition protection:
        # Re-check availability inside transaction before insertion
        with db.begin():
            # Double-check availability is still valid inside transaction
            if not _check_slot_available(restaurant_id, date_str, time_str, party_size):
                raise BookingValidationError(
                    "The slot was just booked by another guest. Please choose another time.",
                    "race_condition_slot_full"
                )

            guest = _get_or_create_guest(db, restaurant_id, phone, name, language)
            if guest:
                guest.visit_count = (guest.visit_count or 0) + 1
                guest.last_visit = date_str
                if has_allergy and special_requests:
                    existing = guest.preferences or ""
                    if special_requests not in existing:
                        guest.preferences = f"{existing} | {special_requests}".strip(" |")
                db.flush()  # flush within transaction, don't commit yet

            booking = Booking(
                restaurant_id=restaurant_id,
                guest_id=guest.id if guest else None,
                call_log_id=call_log_id,
                session_id=session_id,
                name=name,
                phone=phone,
                party_size=party_size,
                date=date_str,
                time=time_str,
                occasion=occasion,
                special_requests=special_requests,
                has_allergy=has_allergy,
                language=language,
                source=booking_data.get("source", "phone_agent"),
                status="confirmed",
                confirmation_sent=False,
            )
            db.add(booking)
            db.flush()  # flush to get booking.id
            db.refresh(booking)
        # Transaction commits here

        if call_log_id:
            _update_call_log(
                call_log_id,
                turns=_turn_count.get(session_id, 0),
                language=language,
            )

        print(
            f"\n[BOOKING CONFIRMED] #{booking.id} | {booking.name} | "
            f"party of {booking.party_size} | {booking.date} at {booking.time} | "
            f"allergy={has_allergy} | requests={special_requests or 'none'}\n"
        )
        return booking
        return booking
    finally:
        db.close()


def _save_transfer(session_id: str, transfer_data: dict, restaurant_id: int = 1):
    db = SessionLocal()
    try:
        call_log_id = _call_log_id.get(session_id)
        reason = transfer_data.get("reason", "out_of_scope")
        from db import Transfer
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
        if call_log_id:
            _update_call_log(call_log_id, escalated=True, escalation_reason=reason)
        print(f"\n[TRANSFER LOGGED] #{transfer.id} | reason={reason}\n")
        return transfer
    finally:
        db.close()


# ── Availability context builder ───────────────────────────────────────────

def _build_availability_context(restaurant_id: int, date_str: Optional[str] = None) -> str:
    """
    Build a short availability note to inject into the system prompt.
    If a specific date is given, show slots for that date.
    Otherwise show slots for today and tomorrow.
    """
    lines = []
    dates_to_check = []

    if date_str:
        dates_to_check = [date_str]
    else:
        today = date.today()
        dates_to_check = [
            today.strftime("%Y-%m-%d"),
            (today + timedelta(days=1)).strftime("%Y-%m-%d"),
        ]

    for d in dates_to_check:
        slots = _get_available_slots(restaurant_id, d)
        try:
            label = datetime.strptime(d, "%Y-%m-%d").strftime("%A %d %B")
        except ValueError:
            label = d
        if slots:
            lines.append(f"{label}: available at {', '.join(slots)}")
        else:
            lines.append(f"{label}: fully booked or closed")

    return "\n".join(lines) if lines else "Availability data unavailable."


# ── System prompt ──────────────────────────────────────────────────────────

def _build_system_prompt(
    restaurant: Restaurant,
    context: str = "new",
    returning_guest_context: str = "",
    availability_context: str = "",
    restaurant_id: int = 1,
) -> str:
    now = datetime.now()
    today = now.strftime("%A, %d %B %Y")
    current_time = now.strftime("%H:%M")
    max_party = _get_max_party_size(restaurant)
    contact_email = getattr(restaurant, "contact_email", "") or ""

    if context == "cancel":
        context_instruction = "The caller wants to CANCEL a reservation. Acknowledge warmly, collect name and date, then output TRANSFER_JSON with reason 'modify_reservation'."
    elif context == "modify":
        context_instruction = "The caller wants to MODIFY a reservation. Acknowledge warmly, ask what they'd like to change, then output TRANSFER_JSON with reason 'modify_reservation'."
    elif context == "manager":
        context_instruction = "The caller wants to speak with a manager. Output TRANSFER_JSON with reason 'caller_requested_human' immediately after greeting."
    else:
        context_instruction = "The caller may want to make a new booking or ask a question."

    return f"""You are {restaurant.agent_name}, the voice concierge for {restaurant.name}.

PERSONALITY: {restaurant.personality}

You are a warm, composed, and knowledgeable host — not a chatbot.
Every caller should feel welcomed before they've even arrived.

━━━ LANGUAGE ━━━
CRITICAL: Detect the caller's language from their first message.
- If they speak French (detect French words, phrases, or context), respond entirely in French for the entire call.
- If they speak Arabic (detect Arabic words, phrases, script, or context), respond entirely in Arabic for the entire call.
- Otherwise, use the restaurant's default language: {restaurant.default_language.upper()}.
- Never switch languages mid-call unless the caller explicitly switches first.
- The JSON output fields (BOOKING_JSON, TRANSFER_JSON) always stay in English regardless of conversation language.

━━━ VOICE RULES ━━━
- 1 to 3 sentences per turn. Never stack multiple questions.
- No markdown, bullet points, or lists. Speak naturally.
- Use the caller's name once you have it, but not on every sentence.
- Mirror the caller's energy: brief when they're efficient, warmer when chatty.
- Avoid overused phrases like "Sure!", "Absolutely!", "Great choice!" — favor natural, refined language instead.
- Stay calm with frustrated callers. Never match their tension.
- If sincerely asked whether you're an AI: "I'm the digital concierge for {restaurant.name} — here to make things as easy as possible."

━━━ TODAY ━━━
Date: {today}
Time: {current_time}
Call context: {context_instruction}

━━━ KNOWLEDGE BASE ━━━
Menu:
{restaurant.menu_text}

Opening hours:
{restaurant.hours}

Booking rules:
{restaurant.booking_rules}

FAQs:
{restaurant.faqs}

Only reference items explicitly in the menu above. Never invent details.
For anything outside your knowledge: "That's a great question — let me have someone from our team follow up. May I take your number?"

━━━ RETURNING GUEST ━━━
{returning_guest_context}

If returning (visit_count > 0), acknowledge warmly once:
"Wonderful to hear from you again — we're always glad to have you back."
If preferences are on file, offer to reuse them rather than asking again.

━━━ CALL ROUTING ━━━
Detect intent in the first turn:
- Reservation request → booking flow below
- Menu / hours question → answer from knowledge base, then offer to book
- Modify / cancel existing booking → collect name + date, output TRANSFER_JSON reason 'modify_reservation'
- Complaint → de-escalate, collect callback, output TRANSFER_JSON reason 'complaint'
- Wants human → output TRANSFER_JSON reason 'caller_requested_human'
- Press / partnership / HR → "For that, reach out to our team at {contact_email}."
- Unclear → ask one open question: "Are you looking to make a reservation, or did you have a question about us?"

━━━ AVAILABILITY ━━━
Current availability (checked from system — trust this data):
{availability_context}

IMPORTANT:
- Only offer time slots listed as available above.
- If the caller's requested slot is not listed, say it is not available and offer the nearest alternatives from the list.
- Never confirm a time slot that is not in the availability list.
- If no slots are available on a date, say we are fully booked and offer adjacent dates.

━━━ BOOKING FLOW ━━━
Collect these fields naturally, one per turn:
1. Full name
2. Phone number — ask after name: "And the best number to reach you on, in case anything changes?" If they decline: "No worries — I'll note the booking without it."
   **CRITICAL PHONE VALIDATION:** After the caller gives a phone number, you MUST read it back digit by digit to confirm before moving on.
   - Extract only digits from what they said (e.g., if they say "zero-six-one-two-three-four-five-six-seven-eight", read back as "0-6-1-2-3-4-5-6-7-8")
   - Say: "I have [DIGITS SEPARATED BY HYPHENS] — is that right?"
   - Wait for explicit confirmation (yes/correct/that's right/etc.)
   - If the number is incomplete (fewer than 7 digits), contains non-numeric words like "last", "the", "extension", "code", etc., or doesn't look like a valid phone number:
     Say: "I want to make sure I have that right — could you give me the full number?" and ask again.
   - Never accept or store a phone number that contains non-numeric words or is too short.
   - Only proceed to next field (party size) after explicit confirmation of a valid phone number.
3. Party size
4. Date
5. Time (must be from availability list above)

Party size rules (enforced by system — do not override):
- Maximum party size: {max_party}
- If caller requests more than {max_party}: STOP immediately. Do not collect more fields. Do not output BOOKING_JSON.
  Say: "For a group that size, I want to make sure we take wonderful care of you — I'll pass this to our team to confirm. Could I take the best number to reach you?"
  Then output: TRANSFER_JSON with reason "large_group" and all details collected in notes.

Mid-flow corrections:
- If the caller changes any detail already given (name, date, time, party size), silently update it and confirm the change naturally: "Of course — I've updated that."
- Do not restart the flow. Continue collecting remaining fields.

Special requests and allergies:
- Do not ask proactively.
- If mentioned at any point, capture them. Treat allergies with gravity: "I've noted that carefully — our kitchen team will be informed."
- Set has_allergy: true in JSON if any food allergy is mentioned.

━━━ CONFIRMATION LOOP (MANDATORY) ━━━
Before outputting BOOKING_JSON, you MUST read the full booking back to the caller:
"Let me just confirm — table for [party size] on [date] at [time], under [name]. Is that all correct?"

Wait for confirmation. If they say yes or confirm → output BOOKING_JSON.
If they correct anything → update the relevant field, repeat the confirmation with the corrected details, then output BOOKING_JSON only after they confirm again.
Never skip this step. This is the most important part of the booking flow.

━━━ OUTPUT PROTOCOL ━━━
BOOKING_JSON on its own line after caller confirms:
BOOKING_JSON:{{"name":"Full Name","phone":"E164_OR_EMPTY","party_size":N,"date":"YYYY-MM-DD","time":"HH:MM","occasion":"OR_EMPTY","special_requests":"OR_EMPTY","has_allergy":false,"language":"ISO_CODE","source":"phone_agent","status":"confirmed"}}

Type rules:
- party_size → integer. date → YYYY-MM-DD. time → HH:MM (24h).
- has_allergy → boolean. language → ISO 639-1 ("en", "fr", "es").
- phone → E.164 if possible, else raw digits, else "".
- status → always "confirmed".

TRANSFER_JSON on its own line for all escalations:
TRANSFER_JSON:{{"reason":"REASON","caller_name":"NAME_OR_EMPTY","callback":"PHONE_OR_EMPTY","booking_date":"YYYY-MM-DD_OR_EMPTY","notes":"brief summary"}}

reason values: modify_reservation | complaint | large_group | out_of_scope | caller_requested_human

CRITICAL: JSON and verbal close must always appear together in the same response.
Never output JSON without the spoken confirmation. Never output spoken confirmation without the JSON.

Verbal close variations (use naturally, never robotically):
- "[Name], you're confirmed for [day] at [time] — we very much look forward to welcoming you."
- "Wonderful — your table for [N] is set for [day] at [time]. It's going to be a lovely evening."
- "Perfect, [Name]. We'll see you [day] at [time]."

━━━ EDGE CASES ━━━
Silence: "I'm still here — take your time."
Repeated misunderstanding after 2 tries: "I want to make sure I get this right — would it help if someone from our team calls you back?" → TRANSFER_JSON reason "out_of_scope"
Upset caller: Acknowledge once warmly, collect callback, output TRANSFER_JSON reason "complaint".
Prank / inappropriate: One polite neutral response. If it continues: "Thank you for calling {restaurant.name}. Have a lovely day." End the call.
Booking system error: "I'm having a little trouble on my end — let me make sure someone from our team follows up with you directly." → TRANSFER_JSON reason "out_of_scope".
"""


# ── JSON extraction ────────────────────────────────────────────────────────

def _validate_booking_schema(data: dict) -> bool:
    """
    Validate that booking_data contains all required fields with correct types.
    Returns True if valid, False otherwise.
    """
    if not isinstance(data, dict):
        return False
    
    required_fields = {
        "name": str,
        "party_size": (int, float),  # allow float from LLM, we'll convert
        "date": str,
        "time": str,
    }
    
    for field, expected_type in required_fields.items():
        if field not in data:
            print(f"[SCHEMA ERROR] Missing required field: {field}")
            return False
        value = data[field]
        if not isinstance(value, expected_type):
            print(f"[SCHEMA ERROR] Field {field} has type {type(value).__name__}, expected {expected_type}")
            return False
    
    return True


def _try_extract_booking(text: str) -> Optional[dict]:
    """
    Extract BOOKING_JSON from text, validate schema, and return parsed dict.
    If extraction fails, return None (will trigger retry in chat loop).
    """
    match = re.search(r"BOOKING_JSON:(\{[^\n]+\})", text)
    if match:
        try:
            data = json.loads(match.group(1))
            
            # Validate schema
            if not _validate_booking_schema(data):
                print(f"\n[BOOKING SCHEMA VALIDATION FAILED] {data}\n")
                return None
            
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
        "ask for manager", "get the manager",
    ]
    return any(k in lower for k in keywords)


def _is_confirmation_intent(user_message: str) -> bool:
    """
    Detect if user is confirming a booking.
    Used to only save booking when user explicitly confirms.
    """
    lower = user_message.lower().strip()
    
    # Affirmative keywords
    affirmative = [
        "yes", "yeah", "yep", "yup", "sure", "sounds good",
        "that's right", "that sounds good", "that sounds great",
        "perfect", "great", "excellent", "wonderful", "ok", "okay", "alright",
        "confirm", "confirmed", "confirmed it", "confirmed the booking",
        "proceed", "let's proceed", "go ahead", "book it", "let's do it",
    ]
    
    # Reject if user is correcting
    if "wait" in lower or "actually" in lower or "no " in lower or "let me" in lower:
        return False
    
    return any(k in lower for k in affirmative)


# ── Backend validation ─────────────────────────────────────────────────────

class BookingValidationError(Exception):
    """Raised when a booking fails backend validation."""
    def __init__(self, message: str, error_type: str):
        super().__init__(message)
        self.error_type = error_type
        self.user_message = message


def _validate_booking(
    booking_data: dict,
    restaurant: Restaurant,
    restaurant_id: int,
) -> None:
    """
    Validate booking against business rules.
    Raises BookingValidationError with a user-friendly message if invalid.
    This is the authoritative check — the prompt is a guide, this is the enforcer.
    """
    party_size = booking_data.get("party_size")
    date_str = booking_data.get("date")
    time_str = booking_data.get("time")

    # 1. Party size must be a positive integer
    try:
        party_size = int(party_size)
    except (TypeError, ValueError):
        raise BookingValidationError(
            "I didn't quite catch the party size — could you confirm how many guests will be joining?",
            "invalid_party_size"
        )

    if party_size < 1:
        raise BookingValidationError(
            "I need at least one guest to make a reservation.",
            "invalid_party_size"
        )

    # 2. Party size must not exceed maximum
    max_party = _get_max_party_size(restaurant)
    if party_size > max_party:
        raise BookingValidationError(
            f"For a group of {party_size}, I'll need to pass this to our team to make sure we arrange everything properly. Could I take the best number to reach you?",
            "large_group"
        )

    # 3. Date must be valid and not in the past
    try:
        booking_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        if booking_date < date.today():
            raise BookingValidationError(
                "That date has already passed — shall we find a date coming up?",
                "past_date"
            )
    except (ValueError, TypeError):
        raise BookingValidationError(
            "I didn't catch the date clearly — could you give me the day and month again?",
            "invalid_date"
        )

    # 4. Time must be valid format
    if not re.match(r'^\d{2}:\d{2}$', str(time_str or "")):
        raise BookingValidationError(
            "I want to make sure I have the right time — could you confirm when you'd like to arrive?",
            "invalid_time"
        )

    # 5. Slot must be available
    if not _check_slot_available(restaurant_id, date_str, time_str, party_size):
        available = _get_available_slots(restaurant_id, date_str)
        if available:
            alt = available[:2]
            alts = " or ".join(alt)
            raise BookingValidationError(
                f"Unfortunately that slot is now fully booked. We do have availability at {alts} — would either of those work for you?",
                "no_availability"
            )
        else:
            raise BookingValidationError(
                "I'm afraid we're fully booked on that date. Would a different evening work — perhaps the day before or after?",
                "fully_booked"
            )


# ── Public API ─────────────────────────────────────────────────────────────

def chat(
    session_id: str,
    user_message: str,
    restaurant_id: int = 1,
    is_preview: bool = False,
) -> tuple[str, bool]:
    """
    Process one caller utterance.
    Returns (spoken_reply, transfer_needed).
    """
    restaurant = _get_restaurant(restaurant_id)
    if not restaurant:
        return "Sorry, I couldn't load the restaurant configuration.", False

    # Initialise session
    if session_id not in _histories:
        _histories[session_id] = []
        _turn_count[session_id] = 0
        _validation_errors[session_id] = 0
        # Initialize language to restaurant default, will be updated on first message
        _call_language[session_id] = restaurant.default_language or "en"
        phone = _caller_phone.get(session_id)
        log_id = _create_call_log(session_id, restaurant_id, phone)
        _call_log_id[session_id] = log_id
    
    # Detect language on first user message
    if _turn_count[session_id] == 0:
        detected = _detect_language(user_message, restaurant.default_language or "en")
        _call_language[session_id] = detected
        print(f"[LANGUAGE DETECTED] {session_id}: {detected}")

    history = _histories[session_id]
    _turn_count[session_id] += 1

    # Priority 8: Check escalation conditions
    # Force transfer if > 8 turns without booking
    if _turn_count[session_id] > 8 and session_id not in _pending_booking:
        print(f"\n[ESCALATION] Turn count > 8 without booking. Forcing transfer.\n")
        _save_transfer(
            session_id,
            {
                "reason": "out_of_scope",
                "caller_name": "Unknown",
                "notes": f"Call lasted {_turn_count[session_id]} turns without completing booking. Complex request.",
            },
            restaurant_id,
        )
        return "I want to make sure we get this right for you. Let me connect you with our team who can help.", True

    # Force transfer if 2 validation errors in a row
    if _validation_errors[session_id] >= 2:
        print(f"\n[ESCALATION] {_validation_errors[session_id]} validation errors in a row. Forcing transfer.\n")
        _save_transfer(
            session_id,
            {
                "reason": "out_of_scope",
                "caller_name": "Unknown",
                "notes": f"{_validation_errors[session_id]} consecutive booking validation failures.",
            },
            restaurant_id,
        )
        return "I want to make sure I get your details exactly right. Let me connect you with someone who can help.", True

    transfer_needed = _is_manager_request(user_message)
    history.append({"role": "user", "content": user_message})

    # Build availability context for this turn
    # Try to detect if a specific date has been mentioned in recent history
    recent_text = " ".join(m["content"] for m in history[-4:])
    date_hint = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', recent_text)
    avail_context = _build_availability_context(
        restaurant_id,
        date_hint.group(1) if date_hint else None
    )

    context = _call_context.get(session_id, "new")
    phone = _caller_phone.get(session_id)
    returning_ctx = _build_returning_guest_context(restaurant_id, phone)

    system_prompt = _build_system_prompt(
        restaurant,
        context,
        returning_ctx,
        avail_context,
        restaurant_id,
    )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system_prompt}] + history,
            temperature=0.65,
            max_tokens=280,
        )
    except Exception as e:
        # Handle rate limits and other API errors gracefully
        error_str = str(e).lower()
        if "rate_limit" in error_str or "429" in error_str:
            print(f"\n[GROQ RATE LIMIT] {e}\n")
            return "I'm experiencing high demand right now — could you try again in a moment?", True
        elif "timeout" in error_str:
            print(f"\n[GROQ TIMEOUT] {e}\n")
            return "I'm having a little trouble connecting — let me pass you to our team.", True
        else:
            print(f"\n[GROQ ERROR] {e}\n")
            raise

    assistant_text = response.choices[0].message.content or ""
    history.append({"role": "assistant", "content": assistant_text})

    # Handle BOOKING_JSON with backend validation
    booking_data = _try_extract_booking(assistant_text)
    if booking_data:
        try:
            _validate_booking(booking_data, restaurant, restaurant_id)
            # Reset error counter on successful validation
            _validation_errors[session_id] = 0
            
            # Check if user is confirming (explicit confirmation intent)
            if _is_confirmation_intent(user_message):
                _save_booking(session_id, booking_data, restaurant_id, is_preview=is_preview)
                # Clear any pending state
                _pending_booking.pop(session_id, None)
                _pending_confirmation.pop(session_id, None)
                print(f"\n[CONFIRMATION CONFIRMED] User explicitly confirmed booking.\n")
            else:
                # Store as pending, wait for confirmation
                _pending_booking[session_id] = booking_data
                _pending_confirmation[session_id] = True
                print(f"\n[BOOKING PENDING CONFIRMATION] Awaiting user confirmation.\n")
                
        except BookingValidationError as e:
            print(f"\n[BOOKING VALIDATION FAILED] {e.error_type}: {e}\n")
            _validation_errors[session_id] += 1

            # If it's a large group, trigger transfer
            if e.error_type == "large_group":
                transfer_needed = True
                _save_transfer(
                    session_id,
                    {
                        "reason": "large_group",
                        "caller_name": booking_data.get("name", ""),
                        "callback": booking_data.get("phone", ""),
                        "notes": f"Party of {booking_data.get('party_size')} on {booking_data.get('date')} at {booking_data.get('time')}",
                    },
                    restaurant_id,
                )

            # Override the assistant's spoken reply with the validation error message
            # Remove the JSON and append the error message instead
            spoken_reply = _clean_reply(assistant_text)
            # Replace whatever Sofia said at the end with the corrective message
            spoken_reply = e.user_message
            return spoken_reply, transfer_needed

    # Handle TRANSFER_JSON
    transfer_data = _try_extract_transfer(assistant_text)
    if transfer_data:
        _save_transfer(session_id, transfer_data, restaurant_id)
        transfer_needed = True

    spoken_reply = _clean_reply(assistant_text)
    return spoken_reply, transfer_needed


def get_greeting(restaurant_id: int = 1, context: str = "new") -> str:
    """Return the opening greeting for a new call."""
    restaurant = _get_restaurant(restaurant_id)
    if not restaurant:
        return "Hello, how can I help you today?"

    avail_context = _build_availability_context(restaurant_id)

    system_prompt = _build_system_prompt(
        restaurant, context, "", avail_context, restaurant_id
    )

    if context == "cancel":
        user_prompt = "[Caller just connected, wants to cancel a reservation. Greet warmly in one sentence.]"
    elif context == "modify":
        user_prompt = "[Caller just connected, wants to modify a reservation. Greet warmly in one sentence.]"
    elif context == "manager":
        user_prompt = "[Caller just connected, requesting a manager. Greet warmly, let them know you're connecting them.]"
    else:
        user_prompt = "[Caller just connected. Greet warmly in one sentence.]"

    try:
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
    except Exception as e:
        error_str = str(e).lower()
        if "rate_limit" in error_str or "429" in error_str:
            print(f"\n[GROQ RATE LIMIT IN GREETING] {e}\n")
            return f"Welcome to {restaurant.name}. I'm experiencing high demand — thank you for your patience."
        else:
            print(f"\n[GROQ ERROR IN GREETING] {e}\n")
            return f"Welcome to {restaurant.name}, I'm {restaurant.agent_name}. How can I help you today?"


def clear_session(session_id: str):
    """Clean up all in-memory state for a session at call end."""
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
    _call_language.pop(session_id, None)
    _pending_booking.pop(session_id, None)


def set_call_context(session_id: str, context: str):
    if context in ["new", "cancel", "modify", "manager"]:
        _call_context[session_id] = context
        print(f"[CONTEXT SET] {session_id}: {context}")


def set_caller_phone(session_id: str, phone: str):
    normalized = _normalize_phone(phone)
    _caller_phone[session_id] = normalized or phone
    print(f"[PHONE SET] {session_id}: {_caller_phone[session_id]}")


def get_caller_phone(session_id: str) -> Optional[str]:
    return _caller_phone.get(session_id)