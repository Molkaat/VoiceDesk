"""
Google Calendar integration for VoiceDesk.
Handles OAuth flow, event creation/deletion, and calendar sync status.
"""
import os
from datetime import datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.api_core.exceptions import GoogleAPICallError
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


class CalendarNotConnected(Exception):
    """Raised when calendar service is not available."""
    pass


def _load_credentials():
    """
    Load credentials from token.json if it exists and is valid.
    Returns None if token doesn't exist or is expired and can't be refreshed.
    """
    if not Path(TOKEN_FILE).exists():
        return None
    
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        
        # Refresh if expired
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save the refreshed token
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
        
        return creds
    except Exception:
        return None


def get_calendar_service():
    """
    Get an authenticated Google Calendar API service object.
    Loads credentials from token.json if available.
    Raises CalendarNotConnected if no valid token exists.
    """
    creds = _load_credentials()
    
    if not creds:
        raise CalendarNotConnected(
            "Calendar not connected. Visit /auth/google to authorize."
        )
    
    service = build("calendar", "v3", credentials=creds)
    return service


def create_booking_event(booking: dict, restaurant: dict) -> str:
    """
    Create a Google Calendar event for a booking.
    
    Args:
        booking: dict with keys: name, phone, party_size, date (YYYY-MM-DD), time (HH:MM), 
                 special_requests (optional), has_allergy (optional)
        restaurant: dict with keys: id, name
    
    Returns:
        The htmlLink to the created event
    
    Raises:
        CalendarNotConnected if calendar service unavailable
    """
    service = get_calendar_service()
    
    # Parse booking date and time
    try:
        booking_datetime = datetime.strptime(
            f"{booking['date']} {booking['time']}", "%Y-%m-%d %H:%M"
        )
    except (ValueError, KeyError) as e:
        raise ValueError(f"Invalid booking date/time: {booking.get('date')} {booking.get('time')} - {e}")
    
    # Start time in Europe/Paris timezone
    start_time = booking_datetime.isoformat()
    # End time = start + 2 hours
    end_time = (booking_datetime + timedelta(hours=2)).isoformat()
    
    # Build event description
    description_lines = [
        f"Party: {booking['party_size']}",
        f"Phone: {booking.get('phone') or 'N/A'}",
    ]
    if booking.get('special_requests'):
        description_lines.append(f"Note: {booking['special_requests']}")
    if booking.get('has_allergy'):
        description_lines.append("⚠️ Allergy/dietary restrictions noted")
    description_lines.append("Booked via VoiceDesk")
    
    description = "\n".join(description_lines)
    
    # Build event object
    event = {
        "summary": f"{booking['party_size']} guests — {booking['name']}",
        "description": description,
        "start": {
            "dateTime": start_time,
            "timeZone": "Europe/Paris",
        },
        "end": {
            "dateTime": end_time,
            "timeZone": "Europe/Paris",
        },
        "colorId": "10",  # Green
    }
    
    try:
        created_event = service.events().insert(
            calendarId="primary",
            body=event
        ).execute()
        
        return created_event.get("htmlLink", "")
    except GoogleAPICallError as e:
        raise Exception(f"Failed to create calendar event: {e}")


def delete_booking_event(event_id: str) -> bool:
    """
    Delete a Google Calendar event by ID.
    Silently returns False if event not found (already deleted, etc).
    
    Args:
        event_id: The calendar event ID
    
    Returns:
        True if deleted, False if not found or error
    """
    try:
        service = get_calendar_service()
        service.events().delete(
            calendarId="primary",
            eventId=event_id
        ).execute()
        return True
    except Exception as e:
        # Silently ignore — event might already be deleted
        print(f"[CALENDAR] Failed to delete event {event_id}: {e}")
        return False


def is_calendar_connected() -> bool:
    """
    Check if Google Calendar is connected and authorized.
    Returns True if token.json exists and credentials are valid.
    """
    if not Path(TOKEN_FILE).exists():
        return False
    
    try:
        creds = _load_credentials()
        return creds is not None
    except Exception:
        return False


def get_oauth_flow():
    """
    Get the OAuth 2.0 flow for authorization.
    Used by the /auth/google endpoint.
    Requires credentials.json to exist.
    """
    if not Path(CREDENTIALS_FILE).exists():
        raise FileNotFoundError(
            f"{CREDENTIALS_FILE} not found. "
            "Please follow GOOGLE_CALENDAR_SETUP.md to set up credentials."
        )
    
    flow = InstalledAppFlow.from_client_secrets_file(
        CREDENTIALS_FILE,
        SCOPES
    )
    return flow
