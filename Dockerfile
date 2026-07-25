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

# --proxy-headers so uvicorn honours X-Forwarded-Proto/For from the reverse
# proxy in front of it. --forwarded-allow-ips is restricted to the trusted
# proxy hop(s) via FORWARDED_ALLOW_IPS (a Docker network is typically
#172.16.0.0/12) so a client can't spoof its IP by sending its own
# X-Forwarded-For. Override for your topology; only widen to "*" when the
# container port is genuinely unreachable except through the proxy.
ENV FORWARDED_ALLOW_IPS="172.16.0.0/12"
ENTRYPOINT ["./entrypoint.sh"]
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips \"$FORWARDED_ALLOW_IPS\""]
