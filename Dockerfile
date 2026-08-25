FROM python:3.12-slim

WORKDIR /app

# Install system dependencies if required (e.g. gcc for certain packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create a non-root user for the runtime process.
# The build steps (pip install, COPY) run as root so they can write to /app;
# we switch to appuser only for the CMD to follow the principle of least privilege.
RUN adduser --disabled-password --no-create-home --gecos "" appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
