import os
from pathlib import Path
import secrets

BASE_DIR = Path(__file__).resolve().parent.parent
# Only this application's local .env is read; values are never printed.
if (BASE_DIR / '.env').exists():
    for line in (BASE_DIR / '.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            if key.strip() in {'AI_API_KEY', 'AI_BASE_URL', 'AI_MODEL', 'AI_TIMEOUT', 'AI_PROVIDER', 'CODEX_MODEL', 'CODEX_TIMEOUT', 'CODEX_REASONING_EFFORT', 'LIBRARY_PROVIDER', 'LIBRARY_API_BASE_URL', 'LIBRARY_API_KEY', 'LIBRARY_API_MODEL', 'LIBRARY_API_TIMEOUT'}:
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
DATA_DIR = Path(os.environ.get('WORKBENCH_DATA_DIR', BASE_DIR / 'data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
secret_file = DATA_DIR / '.secret'
if not secret_file.exists():
    try:
        with secret_file.open('x', encoding='ascii') as f:
            f.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
SECRET_KEY = secret_file.read_text(encoding='ascii')
DEBUG = False
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '[::1]']
ROOT_URLCONF = 'config.urls'
INSTALLED_APPS = ['django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'workbench']
MIDDLEWARE = ['workbench.middleware.LocalOnlyMiddleware', 'django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.messages.middleware.MessageMiddleware']
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.messages.context_processors.messages', 'workbench.context.app_context']}}]
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': DATA_DIR / 'workbench.sqlite3', 'OPTIONS': {'timeout': 20, 'transaction_mode': 'IMMEDIATE'}}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'workbench' / 'static'
MEDIA_URL = '/media/'
MEDIA_ROOT = DATA_DIR / 'attachments'
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Strict'
CSRF_COOKIE_SAMESITE = 'Strict'

TEST_RUNNER = 'config.test_runner.LocalTestRunner'
