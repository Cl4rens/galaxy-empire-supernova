FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SUPERNOVA_DATA=/data \
    SUPERNOVA_APK_DIR=/apk

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY supernova ./supernova

RUN useradd --create-home --uid 1000 supernova && mkdir /data && chown supernova /data
USER supernova
VOLUME /data

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "supernova.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
