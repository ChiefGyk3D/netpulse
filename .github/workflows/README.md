# GitHub Actions Workflows

This directory contains automated CI/CD workflows for NetPulse.

## 📋 Workflows Overview

### 🧪 CI - Tests (`ci-tests.yml`)
**Triggers:** Push to main/develop/copilot branches, PRs, manual dispatch

**What it does:**
- Tests on Python 3.10, 3.11, 3.12, and 3.13
- Runs full pytest suite with coverage
- Uploads coverage to Codecov
- Runs security scanning (Bandit)
- Lints code with Ruff
- Checks for known vulnerabilities (Safety)

**Required Secrets:**
- `CODECOV_TOKEN` (optional, for coverage reports)

---

### 🔍 Dependency Scanning

#### Dependency Review (`dependency-review.yml`)
**Triggers:** PRs to main/develop

**What it does:**
- Reviews dependency changes in PRs
- Detects new vulnerabilities
- Checks license compatibility
- Posts summary in PR comments

#### Dependency Vulnerability Scan (`dependency-scan.yml`)
**Triggers:** All pushes, PRs, manual dispatch

**What it does:**
- Scans with Safety (curated vulnerability DB)
- Scans with pip-audit (OSV database)
- Uploads reports as artifacts

#### Snyk Security Scanning (`snyk-security.yml`)
**Triggers:** PRs, weekly (Mondays), manual dispatch

**What it does:**
- Snyk Code (SAST): Static application security testing
- Snyk Open Source (SCA): Dependency vulnerability scanning
- Uploads results to GitHub Security tab

**Required Secrets:**
- `SNYK_TOKEN` (required for Snyk scanning)

---

### 🔐 CodeQL Analysis (`codeql-analysis.yml`)
**Triggers:** Push to main/develop, PRs, weekly (Sundays), manual dispatch

**What it does:**
- Static code analysis for security vulnerabilities
- Detects common vulnerability patterns
- Results appear in GitHub Security > Code Scanning

---

### 🐳 Docker Build & Publish (`docker-build-publish.yml`)
**Triggers:** Push to main, version tags (v*.*.*), PRs, manual dispatch

**What it does:**
- Builds multi-architecture images (amd64, arm64)
- Scans for vulnerabilities with Trivy
- Publishes to GitHub Container Registry (ghcr.io)
- Creates versioned tags and 'latest' tag

**Published Image:** `ghcr.io/chiefgyk3d/netpulse`

**Pull Command:**
```bash
docker pull ghcr.io/chiefgyk3d/netpulse:latest
```

---

## 🔧 Required Repository Secrets

| Secret | Required | Description |
|--------|----------|-------------|
| `CODECOV_TOKEN` | Optional | Codecov upload token for coverage reports |
| `SNYK_TOKEN` | Required* | Snyk API token for security scanning |

*Snyk workflow will skip gracefully if token is not configured.

---

## 📦 Dependabot Configuration

The `dependabot.yml` file configures automatic dependency updates:

- **Python (pip):** Weekly updates for `speedtest-runner/requirements.txt`
- **Docker:** Weekly updates for `speedtest-runner/Dockerfile`
- **GitHub Actions:** Weekly updates for all workflows

Updates are scheduled for Mondays at 09:00 UTC.
