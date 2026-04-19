# SMS Confirmation Setup Guide

This document explains how to set up SMS booking confirmations via Twilio.

## Prerequisites

1. **Twilio Account**: Sign up for free at https://www.twilio.com
2. **Twilio Phone Number**: Get a phone number in your Twilio console

## Installation

1. Install the Twilio package:
```bash
pip install twilio
```

Or install from requirements:
```bash
pip install -r requirements.txt
```

## Configuration

Add the following environment variables to your `.env` file:

```env
# Twilio SMS Configuration
TWILIO_ACCOUNT_SID=your_account_sid_here
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_PHONE_NUMBER=+1234567890
SMS_ENABLED=true
```

### How to find your credentials:

1. Go to https://console.twilio.com
2. **Account SID**: Copy from "Account SID" in the main dashboard
3. **Auth Token**: Copy from "Auth Token" in the main dashboard (click eye icon to reveal)
4. **Phone Number**: Your assigned Twilio phone number (e.g., +1234567890)

## How It Works

When a booking is confirmed:

1. The customer's phone number is extracted from the booking
2. An SMS is sent with the confirmation message:
   ```
   Hi [Name]! Your table for [X] at [Restaurant] on [Date] at [Time] is confirmed. Reply CANCEL to cancel. Thank you!
   ```
3. If SMS fails, the booking is still saved (SMS is non-critical)
4. The SMS send attempt is logged for debugging

## Testing

### Without SMS (Development)

Set `SMS_ENABLED=false` in your `.env` to disable SMS during development. The system will log that SMS is disabled but won't attempt to send.

### With Mock SMS

For testing without Twilio credentials, you can modify the SMS handler to print messages instead of sending them.

### With Real SMS

Once configured with real Twilio credentials and `SMS_ENABLED=true`, SMS will send to any phone number in E.164 format provided during booking.

## Troubleshooting

**SMS not sending?**
- Check `SMS_ENABLED=true` in `.env`
- Verify all three Twilio credentials are set
- Check phone number is in E.164 format (e.g., +33612345678)
- Check Twilio console for any error logs

**"Twilio package not installed"?**
- Run: `pip install twilio`

**Invalid phone number error?**
- Ensure phone is in E.164 format: +[country code][number]
- Example: +33612345678 (France), +14155552671 (USA)

## Cost

Twilio charges per SMS sent:
- Outbound SMS: ~$0.0075 per SMS (rates vary by country)
- Free trial account has $15 credit

For more pricing details: https://www.twilio.com/sms/pricing

## Security Notes

- Never commit `.env` file with real credentials to version control
- Twilio credentials should only be in environment variables
- Add `.env` to `.gitignore`
