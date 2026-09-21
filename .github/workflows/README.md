# GitHub Actions Workflows

Three thin callers. The jobs themselves live in
[ChiefGyk3D/git-your-ship-together](https://github.com/ChiefGyk3D/git-your-ship-together),
shared with Typo Sniper, Stream Daemon, Star Daemon and Boon Tube Daemon, so a
pipeline fix or a new scan step lands once. Each file here says only what is
specific to NetPulse: Python versions, the check command, the Dockerfile path
and build context, the Doppler project.

| Workflow | Triggers | Calls | What it does |
|---|---|---|---|
| `ci.yml` | push to main/develop/copilot/**, PRs, manual | `python-ci.yml` | Lint (ruff), a byte-compile of `speedtest-runner/` on Python 3.10–3.14, Docker build with an import check, one `CI green` gate job for branch protection |
| `release.yml` | push to main, `v*.*.*` tags, PRs, weekly, manual | `python-docker-release.yml` | Build and test on every PR; on main and tags publish a multi-arch (amd64 + arm64) image to `ghcr.io/chiefgyk3d/netpulse`, signed with cosign, with a syft SBOM attached and SLSA provenance recorded; Trivy scan to the Security tab |
| `security.yml` | push to main/develop, PRs, weekly, manual | `security.yml` | CodeQL, gitleaks over the full history, pip-audit, dependency review on PRs, Snyk |

## Secrets: Doppler, not GitHub

No secret is stored in this repository's GitHub secrets. A job authenticates
to Doppler with a short-lived token minted from its own GitHub OIDC identity
(a Doppler Service Account Identity) and reads the `ci` config of the shared
`ci` Doppler project, which holds only what the pipelines need:

| Name | Used by |
|---|---|
| `SNYK_TOKEN` | `security.yml`, Snyk |

Nothing else is needed today: the image goes to GHCR with the job's own
`GITHUB_TOKEN` (Docker Hub publishing is off, as it always was), and there is
no coverage to upload to Codecov until a test suite exists.

The one per-repository setting is the **repository variable**
`DOPPLER_IDENTITY_ID` (Settings → Secrets and variables → Actions →
Variables), the UUID of the identity. It is an identifier, not a secret.

Before that is set the pipelines still run: Snyk warns and skips, and GHCR
publishing works regardless.

The setup runbook, the fallback path (a Doppler Service Token as the single
GitHub secret `DOPPLER_TOKEN`), and every input are documented in the
git-your-ship-together README.

## Verifying a published image

```sh
cosign verify ghcr.io/chiefgyk3d/netpulse:latest \
  --certificate-identity-regexp '^https://github.com/ChiefGyk3D/git-your-ship-together/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

gh attestation verify oci://ghcr.io/chiefgyk3d/netpulse:latest --owner ChiefGyk3D
```

The SBOM is also attached to every run of `release.yml` as the
`sbom.spdx.json` artifact.

## Image tags

- `latest` (main branch only)
- `1.2.3`, `1.2`, `1` (from `v1.2.3` tags)
- `main`, `sha-<short>` (branch and commit)
- `pr-123` (pull requests; built and tested, never pushed)

## Dependabot

`dependabot.yml` opens weekly PRs for the Python packages in
`speedtest-runner/requirements.txt`, the base image in
`speedtest-runner/Dockerfile` and GitHub Actions, each with a seven-day
cooldown on new releases.

## Status badges

```markdown
[![CI](https://github.com/ChiefGyk3D/netpulse/actions/workflows/ci.yml/badge.svg)](https://github.com/ChiefGyk3D/netpulse/actions/workflows/ci.yml)
[![Release](https://github.com/ChiefGyk3D/netpulse/actions/workflows/release.yml/badge.svg)](https://github.com/ChiefGyk3D/netpulse/actions/workflows/release.yml)
[![Security](https://github.com/ChiefGyk3D/netpulse/actions/workflows/security.yml/badge.svg)](https://github.com/ChiefGyk3D/netpulse/actions/workflows/security.yml)
```

## What changed in the migration

- `ci-tests.yml`, `docker-build-publish.yml`, `codeql-analysis.yml`,
  `dependency-review.yml`, `dependency-scan.yml` and `snyk-security.yml` were
  replaced by the three callers above.
- The test step is honest now. The old one ran `pytest tests/` against a
  directory that does not exist and hid the result behind
  `|| echo "No tests found - skipping"`; the new one byte-compiles
  `speedtest-runner/` on every Python in the matrix. Replace it with pytest
  and turn on `codecov` when a test suite lands.
- Bandit and `safety check` were dropped: `safety check` is deprecated
  upstream and needs an account, and both ran as advisory-only. CodeQL and
  ruff's `S` rules cover SAST and pip-audit covers the advisory database.
- pip-audit gates. Ruff lint stays advisory (`lint-continue-on-error`) until
  the tree is clean; delete that line to make it gate.
- `.gitleaks.toml` extends the default ruleset; the full-history scan found
  nothing, so it allowlists nothing.
- Images are now signed, carry an SBOM and provenance, and a publishing build
  never reads the GitHub Actions cache.
