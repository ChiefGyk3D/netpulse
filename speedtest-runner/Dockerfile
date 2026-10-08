# The base image is pinned by digest so a rebuild is reproducible and a
# retagged upstream image cannot slip in; Dependabot moves the digest.
FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2

WORKDIR /app

# pipefail so a failed download in the piped install below fails the build
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install speedtest-cli and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates=20250419 \
    curl=8.14.1-2+deb13u5 \
    gnupg=2.4.7-21+deb13u1 \
    apt-transport-https=3.0.3 \
    && rm -rf /var/lib/apt/lists/*

# Install Ookla Speedtest CLI (official)
RUN curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | bash \
    && apt-get install -y --no-install-recommends speedtest=1.2.0.84-1.ea6b6773cf \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
# Upgrade pip and setuptools in the same layer, then remove pip itself:
# Trivy's setuptools (CVE-2025-47273, CVE-2026-59890) and msgpack
# (GHSA-6v7p-g79w-8964) findings come from pip's own vendored copies under
# pip/_vendor, which no upgrade of the real packages touches. Nothing at
# runtime invokes pip (CMD is the runner), so the image does not need it.
RUN pip install --no-cache-dir --upgrade "pip>=25.3" "setuptools>=80" \
    && pip install --no-cache-dir --require-hashes -r requirements.txt \
    && pip uninstall -y pip

# Copy application
COPY speedtest_runner.py .

# Dedicated non-root user. UID/GID 1000. The runner persists its ISP state under
# /data (the netpulse-state volume, NETPULSE_STATE_FILE=/data/state.json), and
# the Ookla CLI writes its license acceptance under the home directory.
RUN groupadd --gid 1000 netpulse \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin netpulse \
    && mkdir -p /data \
    && chown -R netpulse:netpulse /app /data
USER 1000:1000

# Run the speedtest runner
CMD ["python", "-u", "speedtest_runner.py"]
