from django.core.exceptions import ImproperlyConfigured
from .base import *

DEBUG = False

# Security Headers & Cookies
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'

if SECRET_KEY == 'django-insecure-dummy-key-for-local-dev-only':
    raise ImproperlyConfigured('DJANGO_SECRET_KEY is required in production environment')

if not ALLOWED_HOSTS:
    raise ImproperlyConfigured('ALLOWED_HOSTS is required in production environment')

if not MYFXBOOK_EMAIL or not MYFXBOOK_PASSWORD:
    raise ImproperlyConfigured('MYFXBOOK_EMAIL and MYFXBOOK_PASSWORD are required in production environment')
