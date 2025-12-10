FROM python:3.11-slim

WORKDIR /app

# Install speedtest-cli and system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    apt-transport-https \
    && rm -rf /var/lib/apt/lists/*

# Install Ookla Speedtest CLI (official)
RUN curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | bash \
    && apt-get install -y speedtest

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY speedtest_runner.py .

# Run the speedtest runner
CMD ["python", "-u", "speedtest_runner.py"]
