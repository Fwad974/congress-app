FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .
RUN chmod +x entrypoint.sh

# Create non-root user + a writable dir for persisted VAPID keys
RUN useradd --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app/data
USER appuser

# Persisted runtime state (auto-generated VAPID keys live here)
VOLUME ["/app/data"]

EXPOSE 8000

# --proxy-headers + --forwarded-allow-ips="*" so uvicorn trusts the
# X-Forwarded-Proto/For set by the Cloudflare/reverse proxy in front of it.
# Without this, url_for() builds http:// OAuth redirect URIs behind TLS and
# the client IP is always the proxy's. Safe because the app is only reachable
# through that proxy (the container port isn't published directly).
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
