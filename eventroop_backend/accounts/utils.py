from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from rest_framework import status
from rest_framework.response import Response

client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def send_sms(user, otp):
    phone = user.mobile_number
    client.messages.create(
        body=f"Your OTP is {otp}",
        from_=settings.TWILIO_PHONE_NUMBER,
        to=phone
    )


def send_whatsapp(user, otp):
    phone = user.mobile_number
    if not phone.startswith("+91"):
        phone = "+91"+phone

    client.messages.create(
        body=f"Your OTP is {otp}",
        from_=settings.TWILIO_WHATSAPP_NUMBER,
        to=f"whatsapp:{phone}"
    )

def send_email(user, otp):
    subject = f"One Time Password (OTP) {otp} for Reset Password"

    # Convert OTP into list of digits
    otp_digits = str(otp)

    # Render HTML template
    html_content = render_to_string(
        "emails/otp_email.html",
        {
            "user_name": user.first_name or user.email,
            "otp_code": otp_digits,
        }
    )
    
    # Create email
    email = EmailMultiAlternatives(
        subject=subject,
        body= f"""
        Hi {user.first_name or user.email},

        Your OTP for password reset is: {otp}

        This code is valid for 10 minutes.
        If you didn't request this, please ignore this email.
        """,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )

    # Attach HTML
    email.attach_alternative(html_content, "text/html")

    # Send
    email.send()


def send_otp(channel, user, raw_otp):
    try:
        if channel == "sms":
            send_sms(user, raw_otp)
            
        elif channel == "whatsapp":
            send_whatsapp(user, raw_otp)
            
        elif channel == "email":
            send_email(user, raw_otp)
    except TwilioRestException as e:
        raise e