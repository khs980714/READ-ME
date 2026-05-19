FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY .src/django_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY .src/django_server/ .

# Windows CRLF → LF 변환 후 실행 권한 부여
RUN sed -i 's/\r//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
