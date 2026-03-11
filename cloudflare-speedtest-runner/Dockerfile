FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY cloudflare_speedtest_runner.py .

# Run the Cloudflare speedtest runner
CMD ["python", "-u", "cloudflare_speedtest_runner.py"]
