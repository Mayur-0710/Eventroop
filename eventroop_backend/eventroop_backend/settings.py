import os
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qsl
from datetime import timedelta

# Load environment variables
load_dotenv()

# ----------------- BASE -----------------
BASE_DIR = Path(__file__).resolve().parent.parent

# ----------------- SECURITY -----------------
SECRET_KEY = os.getenv("SECRET_KEY")
DEBUG = os.getenv("DEBUG", "False") == "True"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "*").split(",")


# ----------------- APPLICATIONS -----------------
INSTALLED_APPS = [
    # Django apps
    # 'simpleui', 
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party apps
    'corsheaders',
    'django_filters',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'django_celery_beat',
    'channels',
    'push_notifications',

    # Cloudinary Apps only when enabled
    'cloudinary',
    'cloudinary_storage',
    
    # Project apps
    'accounts',
    'venue_manager',
    'booking',
    "attendance",
    "payroll",
    "notification",
    "analysis",
]

# ----------------- MIDDLEWARE -----------------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# ----------------- CORS & CSRF -----------------

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = True
CSRF_TRUSTED_ORIGINS = [
    "redis://127.0.0.1:6379",
    "http://localhost",
    "http://13.127.228.154",
    "http://127.0.0.1",
    "http://localhost:5173",
    "https://*.vercel.app",
    "https://eventroop.vercel.app",
    "https://eventroop-dev.vercel.app",
    "https://vaishnavimedicare.com",
    "https://valueoccasions.com",
]
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SAMESITE = 'None'
CSRF_COOKIE_SAMESITE = 'None'

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False

# ----------------- WSGI, ASGI & URLS -----------------
ROOT_URLCONF = 'eventroop_backend.urls'
WSGI_APPLICATION = 'eventroop_backend.wsgi.application'
ASGI_APPLICATION = "eventroop_backend.asgi.application"

# ----------------- Notification, Redis, Celery and channel -----------------
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    },
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL

# TODO: currently working on 1 cpu because db is free version, Update later
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_WORKER_POOL = "solo"
CELERY_WORKER_CONCURRENCY = 1

PUSH_NOTIFICATIONS_SETTINGS = {
    'FCM_API_KEY': 'your-firebase-key',   # Android
    'APNS_CERTIFICATE': '/path/to/cert',  # iOS
}

# ----------------- TEMPLATES -----------------
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# ----------------- AUTH -----------------
AUTH_USER_MODEL = 'accounts.CustomUser'

AUTHENTICATION_BACKENDS = [
    'accounts.authentication.EmailMobileAuthBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# ----------------- PASSWORD VALIDATION -----------------
if not DEBUG:
    AUTH_PASSWORD_VALIDATORS = [
        {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
        {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
        {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
        {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
    ]

# ----------------- INTERNATIONALIZATION -----------------
LANGUAGE_CODE = 'en-in'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# ----------------- REST FRAMEWORK & JWT -----------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",

    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS':(
        'eventroop_backend.pagination.StandardResultsSetPagination'
    ),
    'DEFAULT_FILTER_BACKENDS': (
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    )

}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(os.getenv("ACCESS_TOKEN_LIFETIME_MINUTES", 5))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(os.getenv("REFRESH_TOKEN_LIFETIME_DAYS", 1))),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# ----------------- DEFAULT PRIMARY KEY -----------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ----------------- FILE STORAGE (Cloudinary) -----------------

CLOUDINARY_STORAGE = {
    "CLOUD_NAME": os.getenv("CLOUDINARY_CLOUD_NAME"),
    "API_KEY": os.getenv("CLOUDINARY_API_KEY"),
    "API_SECRET": os.getenv("CLOUDINARY_API_SECRET"),
}
STORAGES = {
    "default": {
        "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
    

# ----------------- DATABASE -----------------

tmpPostgres = urlparse(os.getenv("DATABASE_URL"))
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': tmpPostgres.path.replace('/', ''),
        'USER': tmpPostgres.username,
        'PASSWORD': tmpPostgres.password,
        'HOST': tmpPostgres.hostname,
        'PORT': 5432,
        'OPTIONS': dict(parse_qsl(tmpPostgres.query)),
    }
}


# ----------------- STATIC FILES -----------------
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'