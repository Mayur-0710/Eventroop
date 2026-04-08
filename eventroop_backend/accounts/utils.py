from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from django.conf import settings
from django.core.mail import send_mail
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
    send_mail(
        subject="Your Password Reset OTP",
        message=(
            f"Hi {user.first_name or user.email},\n\n"
            f"Your OTP for password reset is: {otp}\n\n"
            f"This code is valid for 10 minutes.\n"
            f"If you didn't request this, please ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


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