# PantryAI headless capture worker (P38 B).
# Playwright's official Python base ships a matching Chromium + system deps,
# keeping the main API image slim (no browser there).
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Railway injects PORT.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
