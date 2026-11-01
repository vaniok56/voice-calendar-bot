FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

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
