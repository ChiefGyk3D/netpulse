# The base image is pinned by digest so a rebuild is reproducible and a
# retagged upstream image cannot slip in; Dependabot moves the digest.
FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2

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
# Upgrade pip and setuptools in the same layer, then remove pip itself:
# Trivy's setuptools (CVE-2025-47273, CVE-2026-59890) and msgpack
# (GHSA-6v7p-g79w-8964) findings come from pip's own vendored copies under
# pip/_vendor, which no upgrade of the real packages touches. Nothing at
# runtime invokes pip (CMD is the runner), so the image does not need it.
RUN pip install --no-cache-dir --upgrade pip setuptools \
    && pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y pip

# Copy application
COPY speedtest_runner.py .

# Run the speedtest runner
CMD ["python", "-u", "speedtest_runner.py"]
