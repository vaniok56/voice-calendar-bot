FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot/ ./bot/

RUN mkdir /data /logs && chown 1000:1000 /data /logs
USER 1000:1000

CMD ["python", "-m", "bot.main"]
