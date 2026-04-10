# notifications/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Notification

class NotificationConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        user = self.scope['user']
        self.group_name = f'notifications_{user.id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        # Send unread count on connect
        count = await self.get_unread_count(user)
        await self.send(json.dumps({'type': 'unread_count', 'count': count}))

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # Receives push from channel layer → sends to browser
    async def send_notification(self, event):
        await self.send(json.dumps(event['data']))

    # Handle messages from browser (e.g. mark as read)
    async def receive(self, text_data):
        data = json.loads(text_data)
        if data.get('action') == 'mark_read':
            await self.mark_read(data['notification_id'])
            await self.send(json.dumps({'type': 'marked_read', 'id': data['notification_id']}))

    @database_sync_to_async
    def get_unread_count(self, user):
        return Notification.objects.filter(recipient=user, is_read=False).count()

    @database_sync_to_async
    def mark_read(self, notif_id):
        Notification.objects.filter(id=notif_id).update(is_read=True)