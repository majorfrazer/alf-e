ARG BUILD_FROM
FROM $BUILD_FROM

# Install build dependencies (Alpine-based HA images use apk)
RUN apk add --no-cache \
    gcc \
    musl-dev \
    python3-dev \
    libffi-dev \
    openssl-dev

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Entrypoint
RUN chmod +x /app/run.sh
CMD ["/app/run.sh"]
