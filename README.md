# NetPulse

A complete solution for monitoring your internet connection speed, latency, jitter, and packet loss with automatic ISP failover detection.

## Features

- **Speed Metrics**: Download/upload speed in Mbps
- **Quality Metrics**: Latency, jitter, packet loss
- **ISP Change Detection**: Automatically detects when your connection fails over to a backup ISP
- **Connection Type Tracking**: Identifies cable, cellular, fiber, DSL connections
- **Beautiful Dashboard**: Pre-configured Grafana dashboard with all metrics
- **Annotations**: Visual markers on graphs when ISP failovers occur

## Architecture

```
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│   Speedtest Runner  │────▶│      InfluxDB       │◀────│       Grafana       │
│   (Python + Ookla)  │     │  (Time-series DB)   │     │   (Visualization)   │
└─────────────────────┘     └─────────────────────┘     └─────────────────────┘
         │
         ▼
   ┌──────────────┐
   │ IP Info APIs │
   │ (ipinfo.io)  │
   └──────────────┘
```

## Quick Start

### Option 1: Easy Setup Script (Recommended)

```bash
git clone https://github.com/chiefgyk3d/netpulse.git
cd netpulse
sudo ./setup.sh
```

The interactive setup script offers:
- **Local install** with systemd timer (best for bare metal/VMs)
- **Docker Compose** with local build
- **Docker with GHCR** pre-built images

For the Docker choices, `setup.sh` writes a `.env` next to `docker-compose.yml` on the first run. It holds freshly generated random values (256 bits each, never a shared default) for the three required secrets, `INFLUXDB_PASSWORD`, `INFLUXDB_TOKEN` and `GRAFANA_PASSWORD`. The file is created with mode `0600`, the values are never printed, and an existing `.env` is never overwritten, so re-running the script keeps your credentials. To rotate them, delete `.env` and the data volumes, then run it again. A token you pass through `NETPULSE_INFLUXDB_TOKEN` is kept instead of a generated one. Read the Grafana password with `grep GRAFANA_PASSWORD .env`.

The local install (`--local`) points at an InfluxDB you already run, so its token or password is yours to supply (flag, environment or silent prompt) and is stored in a `0600` file; the script cannot invent credentials for a server it does not own.

The published InfluxDB (8086) and Grafana (3000) ports bind to `127.0.0.1` only. To reach them from another machine, set `NETPULSE_BIND_ADDR` to that interface's address (or `0.0.0.0` for all interfaces) before running `setup.sh`, or edit it in `.env` and run `docker compose up -d`. Do that only behind a firewall or reverse proxy.

### Option 2: Docker Compose (Manual)

Compose stops with a message naming any required value that is empty, so fill the three secrets in first. Generate each one separately:

```bash
git clone https://github.com/chiefgyk3d/netpulse.git
cd netpulse

# Copy the example environment file and keep it private
cp .env.example .env
chmod 600 .env

# Put a different random value after each of INFLUXDB_PASSWORD,
# INFLUXDB_TOKEN and GRAFANA_PASSWORD (run once per value)
openssl rand -hex 32
nano .env

# Start the stack (ports bind to 127.0.0.1 unless NETPULSE_BIND_ADDR is set)
docker compose up -d
```

### Option 3: Docker with GHCR Image

```bash
git clone https://github.com/chiefgyk3d/netpulse.git
cd netpulse
./setup.sh --docker-ghcr
```

Or pull directly:
```bash
docker pull ghcr.io/chiefgyk3d/netpulse:latest
```

### Option 4: Local Install with systemd Timer

This is more efficient than running a daemon - uses systemd timers instead of Python's internal scheduler:

```bash
# Interactive setup (prompts for InfluxDB version and credentials)
sudo ./setup.sh --local

# Or specify everything on command line (InfluxDB 2.x)
sudo ./setup.sh --local --influxdb-v2 --influxdb-token "your-token" --interval 30

# For existing InfluxDB 1.x installations
sudo ./setup.sh --local --influxdb-v1 --influxdb-user admin --influxdb-pass secret
```

### Access Grafana

Open http://localhost:3000 in your browser.

- **Username**: `admin`
- **Password**: the `GRAFANA_PASSWORD` value in `.env` (there is no default password)

Grafana listens on `127.0.0.1` by default; see `NETPULSE_BIND_ADDR` above to change that.

The "Speedtest Dashboard" will be auto-provisioned and ready to use!

### Upgrading

NetPulse now names the InfluxDB organization and bucket `netpulse` everywhere (compose defaults, runner, Grafana datasource, dashboards). InfluxDB applies its `DOCKER_INFLUXDB_INIT_*` variables only on the first start with an empty data volume, so an existing install keeps the org and bucket it was created with.

If your install was initialised with the old default, its org **and** bucket are both named `speedtest`. Pick one:

- Keep them: set `INFLUXDB_ORG=speedtest` and `INFLUXDB_BUCKET=speedtest` in `.env`, and make the Grafana datasource (`organization`, `defaultBucket`) and the dashboard queries match.
- Switch to the new names: create a `netpulse` org and bucket in InfluxDB, then update the datasource to match.
- Migrate: copy the data from the `speedtest` bucket into a new `netpulse` bucket with `influx` or the InfluxDB UI.

If your `.env` already sets `INFLUXDB_ORG` and `INFLUXDB_BUCKET`, the dashboard now reads the `netpulse` bucket, so that bucket must exist. New installs need nothing.

## Installation Methods

### Systemd Timer vs Docker Daemon

| Method | Pros | Cons |
|--------|------|------|
| **systemd timer** | More efficient, no always-running process, native logging | Requires local Python setup |
| **Docker daemon** | Self-contained, includes InfluxDB + Grafana | More resource usage |
| **Docker GHCR** | Pre-built, fastest setup | Requires Docker |

### systemd Timer Commands

```bash
# Check timer status
sudo systemctl status netpulse.timer
sudo systemctl list-timers

# Run speedtest immediately
sudo systemctl start netpulse.service

# View logs
sudo journalctl -u netpulse -f

# Change interval (edit timer file)
sudo systemctl edit netpulse.timer

# Stop/start timer
sudo systemctl stop netpulse.timer
sudo systemctl start netpulse.timer
```

The unit in `systemd/netpulse.service` matches what `setup.sh` installs: it runs
as the `netpulse` user from `/opt/netpulse`, reads credentials from
`/etc/netpulse/netpulse.env` (an `EnvironmentFile`, so no token lives in the
unit), and is hardened (`NoNewPrivileges`, `ProtectSystem=strict`,
`CapabilityBoundingSet=` empty, `RestrictAddressFamilies`, and others). Note:
the Ookla `speedtest` CLI writes its license acceptance file under the service
user's `HOME`. That user is created with no home directory and the unit sets
`ProtectHome=yes`, so how the two interact is unverified; if the license
prompt repeats in the journal, report it in an issue.

### Uninstall

```bash
sudo ./setup.sh --uninstall
```

## Python dependencies

The runner's direct dependencies are in `speedtest-runner/requirements.in`;
`speedtest-runner/requirements.txt` is generated from it and pins every
dependency, transitive ones included, to a version and its SHA-256 hashes.
pip enters hash-checking mode by itself when it reads the file, so the
Docker image (`pip install --require-hashes`) and CI verify every download.
To add or bump a dependency, edit `requirements.in` and regenerate the lock
with the command in its header; never edit `requirements.txt` by hand.
Dependabot regenerates it for version bumps.

## Configuration

### InfluxDB Version Support

NetPulse supports both InfluxDB 1.x and 2.x:

| Version | Authentication | Use Case |
|---------|---------------|----------|
| **InfluxDB 2.x** | Token-based | Modern, recommended for new installs |
| **InfluxDB 1.x** | Username/Password | Legacy, existing infrastructure |

**For InfluxDB 2.x (default):**
```bash
sudo ./setup.sh --local --influxdb-v2 --influxdb-token "your-api-token"
```

**For InfluxDB 1.x:**
```bash
sudo ./setup.sh --local --influxdb-v1 --influxdb-user admin --influxdb-pass password
```

### Environment Variables

#### General Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INFLUXDB_VERSION` | `2` | InfluxDB version: `1` or `2` |
| `INFLUXDB_URL` | `http://localhost:8086` | InfluxDB server URL |
| `SPEEDTEST_INTERVAL` | `1800` | Seconds between tests (30 min) |
| `TZ` | `America/New_York` | Timezone for timestamps |

#### InfluxDB 2.x Settings (token-based)

| Variable | Default | Description |
|----------|---------|-------------|
| `INFLUXDB_TOKEN` | - | API token (required) |
| `INFLUXDB_ORG` | `netpulse` | InfluxDB organization |
| `INFLUXDB_BUCKET` | `netpulse` | InfluxDB bucket for data |

#### InfluxDB 1.x Settings (username/password)

| Variable | Default | Description |
|----------|---------|-------------|
| `INFLUXDB_USERNAME` | - | Username (required) |
| `INFLUXDB_PASSWORD` | - | Password (required) |
| `INFLUXDB_DATABASE` | `netpulse` | Database name |

#### Grafana Settings (Docker only)

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAFANA_USER` | `admin` | Grafana admin username |
| `GRAFANA_PASSWORD` | - | Grafana admin password (required; `setup.sh` generates it) |
| `INFLUXDB_PASSWORD` | - | InfluxDB initial admin password (required for Docker; `setup.sh` generates it) |
| `NETPULSE_BIND_ADDR` | `127.0.0.1` | Address the InfluxDB and Grafana ports bind to |

### Continuous Probe (optional)

A separate, opt-in service that measures packet loss, latency and jitter all
day with small probes, so a flaky link shows up between speedtests. It writes a
`probe` measurement (one point per target per cycle) to the same InfluxDB 1.x or
2.x the speedtest runner uses, and does not change how the speedtest runner
behaves.

```bash
# Docker: the probe service sits behind a compose profile
docker compose --profile probe up -d

# Local: run it beside the runner, same environment file
python speedtest-runner/probe.py          # daemon
python speedtest-runner/probe.py --once   # a single cycle
```

Each cycle sends a short burst (`PROBE_COUNT` samples) to every target and
writes `sent`, `received`, `loss_pct`, `rtt_min_ms`, `rtt_avg_ms`, `rtt_max_ms`
and `jitter_ms` (the mean difference between consecutive samples), tagged
`target`, `kind` (`icmp`, `tcp`, `https`) and `host`. With the defaults that is
one burst per target per minute.

| Variable | Default | Description |
|----------|---------|-------------|
| `PROBE_TARGETS` | `icmp:1.1.1.1,tcp:1.1.1.1:443,https:https://www.cloudflare.com/` | Comma-separated targets: `icmp:HOST`, `tcp:HOST[:PORT]` (port defaults to 443), `https:URL`. The defaults are public, well-known targets; replace them with your own |
| `PROBE_INTERVAL` | `60` | Seconds between bursts (minimum 10) |
| `PROBE_COUNT` | `5` | Samples per burst (1 to 20) |
| `PROBE_TIMEOUT` | `2` | Seconds to wait for each sample |
| `PROBE_TIMING` | `1` | Write the `probe_timing` measurement (`1` on, `0` off); see below |

**DNS and HTTP timing.** Once per cycle each target also gets one timed
attempt, written to a separate `probe_timing` measurement with the same
`target`, `kind` and `host` tags: `dns_ms` (name resolution, every kind),
`connect_ms` (TCP connect, `tcp` and `https` targets), `tls_ms` (handshake with
full certificate verification) and `ttfb_ms` (a `HEAD` request until the first
response byte; a 5xx answer fails), the last two for `https` targets only. Every
point carries `error` (`0` or `1`). A failed attempt is still written: `error=1`,
`error_stage` (`dns`, `connect`, `tls`, `http` or `unknown`) and whichever
timings finished before the failure, so an outage shows up as error points, not
a gap. Set `PROBE_TIMING=0` to turn it off. Each stage may use up to
`PROBE_TIMEOUT`, and that counts toward the worst-case cycle check below.

The probe refuses to start if the targets, count and timeout could not finish
inside one interval.

**Privileges.** ICMP uses an unprivileged datagram socket, not a raw socket, so
the container keeps `cap_drop: ALL` and gains no capability. Linux allows that
socket only for groups in `net.ipv4.ping_group_range`; Docker opens that range
inside containers by default. If the kernel refuses (a locked-down host, or a
non-Docker install with a narrow range), ICMP targets automatically switch to a
TCP connect on port 443 and the log says so. For ICMP on a local install either
widen the range (`sysctl net.ipv4.ping_group_range="0 2147483647"`) or accept
the TCP fallback. HTTPS probes verify certificates and send only a `HEAD`
request; a 5xx answer counts as a lost sample.

### Recommended Intervals

| Interval | Seconds | Use Case |
|----------|---------|----------|
| 15 minutes | `900` | Detailed monitoring |
| 30 minutes | `1800` | Standard (default) |
| 1 hour | `3600` | Light monitoring |
| 4 hours | `14400` | Minimal data |

## Reading your results

The number NetPulse reports is what the host running the test can push, not
what your ISP delivers to the building. The test host's own network port, NIC,
cable, switch port, CPU and (on a VM) virtualization layer all cap the result,
and the slowest of them wins. A Wi-Fi host reads lower and varies more, and a
2.5 GbE host reads differently from a 1 GbE host on the same line.

A real example: a host with a 1 Gbps Ethernet port behind a 2.5 Gbps WAN tops
out around 940 Mbps of TCP throughput, while a faster client on the same WAN
measured up to 1.19 Gbps. Nothing is wrong with the line; the 1 Gbps port is
the ceiling.

The arithmetic, for a standard 1500-byte MTU:

- TCP payload per packet: 1500 - 20 (IP header) - 20 (TCP header) = 1460 bytes.
- Bytes on the wire per packet: 1500 + 14 (Ethernet header) + 4 (frame check
  sequence) + 8 (preamble) + 12 (inter-frame gap) = 1538 bytes.
- 1460 / 1538 = 94.9%, so about 949 Mbps on a 1000 Mbps link, best case.
- TCP timestamps (on by default in Linux) add 12 bytes per packet, leaving 1448
  bytes of payload: 1448 / 1538 = 94.1%, so about 941 Mbps.

You can check it yourself:

```bash
python3 -c "
wire = 1500 + 14 + 4 + 8 + 12
for name, payload in (('no options', 1460), ('timestamps', 1448)):
    print(name, payload, wire, round(payload / wire * 1000, 1))
"
# no options 1460 1538 949.3
# timestamps 1448 1538 941.5
```

Sources: the 1500-byte Ethernet payload limit is in
[RFC 894](https://www.rfc-editor.org/rfc/rfc894), the 20-byte IP and TCP
headers are in RFC 791 and RFC 793, and the 12-byte TCP timestamp option
(10 bytes plus padding) is in
[RFC 7323](https://www.rfc-editor.org/rfc/rfc7323). The preamble, inter-frame
gap and frame check sequence come from IEEE 802.3, which is not an RFC. The 941
and 949 Mbps figures are this arithmetic, not a quoted citation. A 2.5 GbE host
scales the same way (about 2.35 Gbps), so read your number against the slowest
link in the chain.

## ISP Change Detection

The system detects ISP failovers using multiple methods:

### 1. External IP Tracking
Different ISPs assign different public IP addresses. When your IP changes, it indicates a potential failover.

### 2. ASN (Autonomous System Number)
Each ISP has a unique ASN. The system tracks this to definitively identify ISP changes.

### 3. ISP Name from IP Geolocation
Uses ipinfo.io and ip-api.com to get the ISP name associated with your current IP.

### 4. Connection Type Inference
Based on ISP name keywords, the system categorizes connections as:
- `cable` - Comcast, Spectrum, Cox, etc.
- `cellular` - T-Mobile, Verizon Wireless, AT&T Mobility, etc.
- `fiber` - Verizon FiOS, Google Fiber, AT&T Fiber, etc.
- `dsl` - CenturyLink, Frontier, etc.

### Viewing Failover Events

In Grafana:
1. **ISP Change Events Table**: Shows all failover events with timestamps, previous/current ISP, IPs, and connection types
2. **Annotations**: Red vertical lines on all time-series graphs marking when failovers occurred
3. **Pie Charts**: Distribution of tests by ISP and connection type
4. **Failover Counter**: Total number of ISP failovers in the selected time range

## Dashboard Panels

### Current Status Row
- Download Speed (Mbps)
- Upload Speed (Mbps)
- Latency (ms)
- Jitter (ms)
- Packet Loss (%)
- Current Connection Type

### Speed Over Time
- Combined download/upload graph with mean, max, min statistics

### Latency & Quality
- Latency & Jitter graph
- Packet Loss graph

### ISP & Connection Tracking
- ISP Change Events table
- Tests by Connection Type pie chart
- Tests by ISP pie chart
- Total Failover count

### Speed by Connection Type
- Download speed grouped by connection type (cable vs cellular)
- Latency grouped by connection type

## Troubleshooting

### Check Container Status
```bash
docker compose ps
```

### View Speedtest Runner Logs
```bash
docker compose logs -f speedtest-runner
```

### View All Logs
```bash
docker compose logs -f
```

### Reset Everything
```bash
docker compose down -v
docker compose up -d
```

### Manual Speedtest
```bash
docker compose exec speedtest-runner speedtest --format=json
```

## Data Retention

InfluxDB stores data indefinitely by default. To configure retention:

1. Access InfluxDB at http://localhost:8086
2. Log in with your credentials
3. Go to Data > Buckets > netpulse
4. Set your desired retention period

## Extending

### Adding Alerting

You can add Grafana alerts for:
- Download speed drops below threshold
- Latency exceeds threshold
- ISP failover occurs
- Packet loss exceeds threshold

### Custom ISP Detection

Edit `speedtest_runner.py` and modify the `get_ip_info()` method to add your ISP-specific keywords for better connection type detection.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
