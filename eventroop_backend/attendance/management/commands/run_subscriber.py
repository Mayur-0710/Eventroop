import asyncio
import json
import redis.asyncio as redis
from accounts.models import CustomUser
from attendance.models import AttendanceStatus
from notification.views import create_notification
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date
from django.conf import settings

from attendance.models import Attendance

CHANNEL = "attendance_channel"


class Command(BaseCommand):
    help = "Run Redis Pub/Sub subscriber for attendance"

    async def subscriber(self):
        r = redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = r.pubsub()

        await pubsub.subscribe(CHANNEL)

        self.stdout.write(self.style.SUCCESS("📡 Listening for attendance..."))

        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])

                self.stdout.write(f"📥 Received: {data}")

                # Run DB operation safely in thread
                await asyncio.to_thread(self.save_attendance, data)
        
    def save_attendance(self, data):
        phone = data.get("phone")
        status = data.get("status")
        date_val = parse_date(data.get("date"))

        try:
            user = CustomUser.objects.get(phone=phone)
        except CustomUser.DoesNotExist:
            print("❌ User not found")
            return
        try:
            status = AttendanceStatus.objects.get(code=status)
        except CustomUser.DoesNotExist:
            print("❌ status not found")
            return

        # check duplicate
        if Attendance.objects.filter(user=user, date=date_val).exists():
            print("⚠️ Already marked")
            return

        attendance = Attendance.objects.create(
            user=user,
            status=status,
            date=date_val
        )

        print("✅ Attendance Saved")

        # 🎉 CREATE NOTIFICATION HERE
        create_notification(
            recipient=user,
            title="Attendance Marked ✅",
            message=f"Your attendance for {date_val} is marked as {status}",
            notif_type="system",
            data={
                "attendance_id": attendance.id,
                "status": status,
                "date": str(date_val)
            }
        )

    def handle(self, *args, **options):
        asyncio.run(self.subscriber())