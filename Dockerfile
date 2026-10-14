FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HAVEN_DATA_DIR=/data \
    HAVEN_SECURE_COOKIES=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY manage.py ./manage.py
COPY scripts ./scripts

RUN mkdir -p /data
EXPOSE 7860

# Hugging Face uses 7860; Render and most hosts pass the port in $PORT.
CMD ["sh", "-c", "python -m scripts.hf_start && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips='*'"]
