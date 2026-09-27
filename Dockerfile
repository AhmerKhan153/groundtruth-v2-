# One image, two entry points:
#   default          the webhook service (Container App, scales to zero)
#   run_source       the scheduled source job (Container Apps Job), via its command
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py .
COPY groundtruth/ groundtruth/
COPY scripts/ scripts/

# Compile once at build time; a cold start then skips it.
RUN python -m compileall -q groundtruth scripts main.py \
    && useradd --create-home --uid 10001 app
USER app

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
