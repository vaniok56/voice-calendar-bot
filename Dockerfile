FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt

COPY bot/ ./bot/

RUN mkdir /data /logs && chown 1000:1000 /data /logs
USER 1000:1000

CMD ["python", "-m", "bot.main"]
