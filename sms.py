"""
SMS service for sending booking confirmations, reminders, and cancellations via Twilio.
"""

import logging
from typing import Optional
from datetime import datetime

from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, SMS_ENABLED

logger = logging.getLogger(__name__)


def send_booking_confirmation(
    phone: str,
    guest_name: str,
    party_size: int,
    booking_date: str,
    booking_time: str,
    restaurant_name: str = "La Bella Ristorante",
) -> bool:
    """
    Send SMS booking confirmation to customer.
    
    Args:
        phone: Customer phone number (E.164 format or raw digits)
        guest_name: Name on the reservation
        party_size: Number of guests
        booking_date: YYYY-MM-DD format
        booking_time: HH:MM format
        restaurant_name: Restaurant name for the message
    
    Returns:
        True if SMS was sent successfully, False otherwise
    """
    
    if not SMS_ENABLED:
        logger.info("[SMS] SMS_ENABLED is False, skipping confirmation")
        return False
    
    if not phone or not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
        logger.warning("[SMS] Missing SMS configuration or customer phone number")
        return False
    
    try:
        from twilio.rest import Client
        
        # Initialize Twilio client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        # Format the message
        message_body = (
            f"Hi {guest_name}! Your table for {party_size} at {restaurant_name} on "
            f"{_format_date_for_sms(booking_date)} at {booking_time} is confirmed. "
            f"Reply CANCEL to cancel. Thank you!"
        )
        
        # Send SMS
        message = client.messages.create(
            body=message_body,
            from_=TWILIO_PHONE_NUMBER,
            to=phone,
        )
        
        logger.info(f"[SMS] Confirmation sent | SID: {message.sid} | to: {phone}")
        return True
    
    except ImportError:
        logger.error("[SMS] Twilio package not installed. Install with: pip install twilio")
        return False
    
    except Exception as e:
        logger.error(f"[SMS] Failed to send booking confirmation: {e}")
        return False


def send_booking_reminder(
    phone: str,
    guest_name: str,
    party_size: int,
    booking_date: str,
    booking_time: str,
    restaurant_name: str = "La Bella Ristorante",
) -> bool:
    """
    Send SMS reminder 24 hours before the reservation.
    
    Args:
        phone: Customer phone number
        guest_name: Name on the reservation
        party_size: Number of guests
        booking_date: YYYY-MM-DD format
        booking_time: HH:MM format
        restaurant_name: Restaurant name for the message
    
    Returns:
        True if SMS was sent successfully, False otherwise
    """
    
    if not SMS_ENABLED:
        logger.info("[SMS] SMS_ENABLED is False, skipping reminder")
        return False
    
    if not phone or not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
        logger.warning("[SMS] Missing SMS configuration or customer phone number")
        return False
    
    try:
        from twilio.rest import Client
        
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        # Reminder message (tomorrow at booking time)
        message_body = (
            f"Reminder: Your table for {party_size} at {restaurant_name} is "
            f"tomorrow at {booking_time}. We look forward to seeing you! "
            f"Reply CANCEL if you need to cancel."
        )
        
        message = client.messages.create(
            body=message_body,
            from_=TWILIO_PHONE_NUMBER,
            to=phone,
        )
        
        logger.info(f"[SMS] Reminder sent | SID: {message.sid} | to: {phone}")
        return True
    
    except ImportError:
        logger.error("[SMS] Twilio package not installed")
        return False
    
    except Exception as e:
        logger.error(f"[SMS] Failed to send booking reminder: {e}")
        return False


def send_cancellation_confirmation(
    phone: str,
    guest_name: str,
    restaurant_name: str = "La Bella Ristorante",
) -> bool:
    """
    Send SMS confirmation when a booking is cancelled.
    
    Args:
        phone: Customer phone number
        guest_name: Name on the reservation
        restaurant_name: Restaurant name for the message
    
    Returns:
        True if SMS was sent successfully, False otherwise
    """
    
    if not SMS_ENABLED:
        logger.info("[SMS] SMS_ENABLED is False, skipping cancellation confirmation")
        return False
    
    if not phone or not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
        logger.warning("[SMS] Missing SMS configuration or customer phone number")
        return False
    
    try:
        from twilio.rest import Client
        
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        message_body = (
            f"Hi {guest_name}, your reservation at {restaurant_name} has been cancelled. "
            f"If you'd like to rebook, just give us a call. Thank you!"
        )
        
        message = client.messages.create(
            body=message_body,
            from_=TWILIO_PHONE_NUMBER,
            to=phone,
        )
        
        logger.info(f"[SMS] Cancellation confirmation sent | SID: {message.sid} | to: {phone}")
        return True
    
    except ImportError:
        logger.error("[SMS] Twilio package not installed")
        return False
    
    except Exception as e:
        logger.error(f"[SMS] Failed to send cancellation confirmation: {e}")
        return False


def _format_date_for_sms(date_str: str) -> str:
    """
    Convert YYYY-MM-DD to readable format like "April 25".
    
    Args:
        date_str: Date in YYYY-MM-DD format
    
    Returns:
        Formatted date string (e.g., "April 25")
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%B %d").lstrip("0").replace(" 0", " ")
    except (ValueError, AttributeError):
        # Fallback if date format is invalid
        return date_str
