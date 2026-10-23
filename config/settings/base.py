import sys
from pathlib import Path
import environ
from celery.schedules import crontab

# On Windows, trust the OS certificate store so HTTPS works behind antivirus/corporate
# TLS inspection (their root CA is in the OS store but not in certifi).
if sys.platform == 'win32':
    import truststore
    truststore.inject_into_ssl()

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env_file = BASE_DIR / '.env'
if env_file.exists():
    environ.Env.read_env(env_file)

SECRET_KEY = env('DJANGO_SECRET_KEY', default='django-insecure-dummy-key-for-local-dev-only')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env('ALLOWED_HOSTS')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third party apps
    'rest_framework',
    'django_celery_beat',
    'corsheaders',

    # Local apps
    'sentiment',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

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

WSGI_APPLICATION = 'config.wsgi.application'

# Local development falls back to SQLite; set DATABASE_URL to use Postgres.
DATABASES = {
    'default': env.db('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}')
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '2000/day',
    },
}

# Cache: holds the Myfxbook session and the poll lock. Defaults to per-process memory for
# local use; set CACHE_URL=rediscache://localhost:6379/1 when beat and worker run separately.
CACHES = {
    'default': env.cache_url('CACHE_URL', default='locmemcache://retail')
}

# Celery
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

# Myfxbook (free tier: 100 requests per rolling 24 hours)
MYFXBOOK_EMAIL = env('MYFXBOOK_EMAIL', default='')
MYFXBOOK_PASSWORD = env('MYFXBOOK_PASSWORD', default='')
MYFXBOOK_API_BASE_URL = env('MYFXBOOK_API_BASE_URL', default='https://www.myfxbook.com/api')
MYFXBOOK_DAILY_REQUEST_LIMIT = 100
# Hard cap enforced by the budget guard; leaves headroom under the provider limit.
MYFXBOOK_DAILY_REQUEST_CAP = env.int('MYFXBOOK_DAILY_REQUEST_CAP', default=85)
MYFXBOOK_POLL_INTERVAL_MINUTES = env.int('MYFXBOOK_POLL_INTERVAL_MINUTES', default=30)

CELERY_BEAT_SCHEDULE = {
    'poll-myfxbook-outlook': {
        'task': 'sentiment.tasks.poll_community_outlook_task',
        'schedule': crontab(minute=f'*/{MYFXBOOK_POLL_INTERVAL_MINUTES}'),
    },
}

CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[])
CORS_ALLOW_ALL_ORIGINS = env.bool('CORS_ALLOW_ALL_ORIGINS', default=False)
