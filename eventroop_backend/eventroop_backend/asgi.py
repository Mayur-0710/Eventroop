import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eventroop_backend.settings')

from django.core.asgi import get_asgi_application

# Step 1: initialize Django FIRST
django_asgi_app = get_asgi_application()

# Step 2: import AFTER Django is ready
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from notification.routing import websocket_urlpatterns

# Step 3: define application
application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})