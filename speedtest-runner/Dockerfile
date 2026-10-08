# The base image is pinned by digest so a rebuild is reproducible and a
# retagged upstream image cannot slip in; Dependabot moves the digest.
FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2

WORKDIR /app

# pipefail so a failed download in the piped install below fails the build
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install speedtest-cli and system dependencies. `apt-get upgrade` applies pending
# Debian security updates (the base image lags them, and the image scan fails on
# any fixed HIGH finding such as libpcre2).
# Apt versions are left unpinned on purpose: the base image is pinned by digest,
# and Debian removes superseded package versions, so exact apt pins would break
# the build at every security update.
# hadolint ignore=DL3008
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    apt-transport-https \
    && rm -rf /var/lib/apt/lists/*

# Install Ookla Speedtest CLI (official)
# Apt versions are left unpinned on purpose: the base image is pinned by digest,
# and Debian removes superseded package versions, so exact apt pins would break
# the build at every security update.
# hadolint ignore=DL3008
RUN curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | bash \
    && apt-get install -y --no-install-recommends speedtest \
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
