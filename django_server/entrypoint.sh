#!/bin/bash
set -e

echo "=== READ:ME Django Server Startup ==="

# PostgreSQL이 준비될 때까지 대기
echo "[1/4] Waiting for PostgreSQL..."
until python -c "
import psycopg2, os, urllib.parse as up
url = up.urlparse(os.environ.get('DATABASE_URL', ''))
psycopg2.connect(
    dbname=url.path.lstrip('/'),
    user=url.username,
    password=url.password,
    host=url.hostname,
    port=url.port or 5432,
).close()
print('PostgreSQL is ready.')
" 2>/dev/null; do
  echo "  PostgreSQL not ready yet, retrying in 2s..."
  sleep 2
done

# 마이그레이션 실행
echo "[2/4] Running migrations..."
python manage.py migrate --noinput

# 정적 파일 수집
echo "[3/4] Collecting static files..."
python manage.py collectstatic --noinput --clear

# 슈퍼유저 생성 (환경 변수로 지정된 경우)
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  echo "[4/4] Creating superuser (if not exists)..."
  python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
u = '$DJANGO_SUPERUSER_USERNAME'
p = '$DJANGO_SUPERUSER_PASSWORD'
e = '${DJANGO_SUPERUSER_EMAIL:-admin@readme.local}'
if not User.objects.filter(username=u).exists():
    User.objects.create_superuser(u, e, p)
    print(f'Superuser {u!r} created.')
else:
    print(f'Superuser {u!r} already exists.')
"
else
  echo "[4/4] Skipping superuser creation (DJANGO_SUPERUSER_USERNAME not set)"
fi

echo "=== Starting Gunicorn ==="
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 2 \
  --threads 4 \
  --worker-class gthread \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
