# NetPulse: agent guidance

NetPulse monitors internet speed, latency, jitter and packet loss, and detects ISP failover.
A Python speedtest runner (`speedtest-runner/`) writes to InfluxDB; a provisioned Grafana
dashboard (`grafana/`) reads it. Deployment is `docker-compose.yml`, `setup.sh` and `systemd/`.

## The gate

CI is the GYST `python-ci` caller in `.github/workflows/ci.yml`; the required check is `CI green`.
Run the same checks locally from the repo root:

```
ruff check speedtest-runner/ && python3 -m compileall -q speedtest-runner
```

Lint config is `ruff.toml` (bandit `S` rules on). There is no test suite yet; add pytest and
update the `test-command` in `ci.yml` when tests land. Capture the exit code to a log; do not
pipe a gate into grep or tail.

## Rules

- Issue and board card before any work starts. Every PR links its issue (`Closes #n` or `Refs #n`).
- The maintainer merges. Agents commit on a branch and report; never push, merge or tag.
- Every subagent dispatch sets an explicit model (Sonnet by default). Never inherit.
- Briefs carry a line `issue: ChiefGyk3D/netpulse#N`.
- CI is the GYST reusable workflows, pinned by full commit SHA with a `# vX.Y.Z` comment. Keep only
  NetPulse-specific settings in the callers.
- No secrets, hostnames, serials or callsigns in files, commits or logs. `.env` is never read or written.
- Python requirements are hash-pinned (`requirements.txt` from `requirements.in`); regenerate, do not hand-edit.
- American English.

## Roles and packs

`.claude/agents/` and `.claude/skills/` are installed by the Department of Internal Arguments
(DoIA) installer and carry a generator marker. Never hand-edit them; update with the installer's
`--update` from a pinned release, and check drift with `--list-managed`. Codex copies live in
`.codex/agents/` and `.agents/skills/`.
