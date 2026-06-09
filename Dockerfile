# Agent-Aware — production container.
# Built on Playwright's official Python image so the headless Chromium the
# scraper needs (plus all its system libraries) is already present.
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

WORKDIR /app

# Install Python dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

# App source.
COPY . .

# Server-safe defaults. Real secrets (API keys, OAuth) are injected at runtime
# as environment variables by the hosting platform — never built into the image.
ENV PLAYWRIGHT_HEADLESS=true \
    STREAMLIT_SERVER_HEADLESS=true \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONPATH=/app

RUN chmod +x scripts/docker-entrypoint.sh

# Hosting platforms inject $PORT; 8501 is the local default.
EXPOSE 8501

# Lightweight healthcheck against Streamlit's built-in endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://localhost:'+os.getenv('PORT','8501')+'/_stcore/health')" || exit 1

ENTRYPOINT ["scripts/docker-entrypoint.sh"]
