from datetime import timedelta
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-key-change-in-production")
DEBUG = os.getenv("DJANGO_DEBUG", "True") == "True"

_raw_hosts = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1")
ALLOWED_HOSTS = [h.strip() for h in _raw_hosts.split(",") if h.strip()]

# Docker 내부 IP 자동 허용 (헬스체크 등)
import socket
try:
    _container_ip = socket.gethostbyname(socket.gethostname())
    if _container_ip not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_container_ip)
except Exception:
    pass

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 프로젝트 앱
    "books.apps.BooksConfig",
    "chat.apps.ChatConfig",
    "data_pipeline.apps.DataPipelineConfig",
    "accounts.apps.AccountsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.JWTAuthMiddleware",  # JWT 쿠키 → request.user (세션 인증 후)
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.jwt_settings",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ── Database (PostgreSQL) ─────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "")

if DATABASE_URL:
    import urllib.parse as urlparse

    url = urlparse.urlparse(DATABASE_URL)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": url.path.lstrip("/"),
            "USER": url.username,
            "PASSWORD": url.password,
            "HOST": url.hostname,
            "PORT": url.port or 5432,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

LOGIN_URL = "/accounts/login/"

# ── Password validation ───────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── Internationalisation ──────────────────────────────────────
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

# ── Static files ──────────────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# ── Media / Storage ───────────────────────────────────────────
# 로컬: FileSystemStorage (Docker 볼륨)
# 배포: R2_BUCKET_NAME 환경변수 설정 시 Cloudflare R2 (S3 호환) 사용
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

_R2_BUCKET = os.getenv("R2_BUCKET_NAME", "")

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
}

if _R2_BUCKET:
    _r2_custom_domain = os.getenv("R2_CUSTOM_DOMAIN", "")
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {
            "bucket_name": _R2_BUCKET,
            "endpoint_url": os.getenv("R2_ENDPOINT_URL", ""),
            "access_key": os.getenv("R2_ACCESS_KEY_ID", ""),
            "secret_key": os.getenv("R2_SECRET_ACCESS_KEY", ""),
            "custom_domain": _r2_custom_domain or None,
            "default_acl": "public-read",
            "file_overwrite": False,
        },
    }
    if _r2_custom_domain:
        MEDIA_URL = f"https://{_r2_custom_domain}/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Cache ──────────────────────────────────────────────────────
# 메모리 캐시: 카테고리 목록, 파이프라인 통계 등 자주 조회되는 데이터에 사용
# Redis 도입 시 BACKEND를 django_redis.cache.RedisCache로 교체하세요.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "readme-cache",
    }
}

# ── JWT ───────────────────────────────────────────────────────
_jwt_secret_env = os.getenv("JWT_SECRET_KEY", "")
if not _jwt_secret_env:
    import warnings
    warnings.warn(
        "JWT_SECRET_KEY 환경변수가 설정되지 않았습니다. "
        "DJANGO_SECRET_KEY를 fallback으로 사용합니다. "
        "프로덕션 환경에서는 별도의 JWT_SECRET_KEY를 반드시 설정하세요.",
        stacklevel=2,
    )
JWT_SECRET_KEY = _jwt_secret_env or SECRET_KEY
# Access token: 요청마다 검증, 짧게 유지
JWT_ACCESS_TOKEN_LIFETIME = timedelta(
    minutes=int(os.getenv("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", "60"))
)
# Refresh token: Access token 만료 시 재발급용, 길게 유지
JWT_REFRESH_TOKEN_LIFETIME = timedelta(
    days=int(os.getenv("JWT_REFRESH_TOKEN_LIFETIME_DAYS", "7"))
)
# 유휴 타임아웃(분): JS 측에서 이 시간 동안 활동 없으면 자동 로그아웃
JWT_IDLE_TIMEOUT_MINUTES = int(os.getenv("JWT_IDLE_TIMEOUT_MINUTES", "30"))

# ── External services ─────────────────────────────────────────
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
ALADIN_TTB_KEY = os.getenv("ALADIN_TTB_KEY", "")
MODEL_SERVER_URL = os.getenv("MODEL_SERVER_URL", "http://localhost:8001")

