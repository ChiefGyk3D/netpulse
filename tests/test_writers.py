import pytest
import speedtest_runner


def test_v2_without_token_raises(monkeypatch):
    monkeypatch.setattr(speedtest_runner, "INFLUXDB_VERSION", "2")
    monkeypatch.setattr(speedtest_runner, "INFLUXDB_TOKEN", "")
    with pytest.raises(ValueError, match="INFLUXDB_TOKEN"):
        speedtest_runner.create_influxdb_writer()


def test_default_version_is_v2(monkeypatch):
    monkeypatch.setattr(speedtest_runner, "INFLUXDB_VERSION", "")
    with pytest.raises(ValueError, match="INFLUXDB_TOKEN"):
        speedtest_runner.create_influxdb_writer()


def test_v1_without_username_raises(monkeypatch):
    monkeypatch.setattr(speedtest_runner, "INFLUXDB_VERSION", " 1 ")
    monkeypatch.setattr(speedtest_runner, "INFLUXDB_USERNAME", "")
    with pytest.raises(ValueError, match="INFLUXDB_USERNAME"):
        speedtest_runner.create_influxdb_writer()


def test_v2_factory_builds_writer(monkeypatch):
    seen = {}

    class Stub:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(speedtest_runner, "InfluxDB2Writer", Stub)
    monkeypatch.setattr(speedtest_runner, "INFLUXDB_TOKEN", "t")
    assert isinstance(speedtest_runner.create_influxdb_writer(), Stub)
    assert seen["token"] == "t" and seen["bucket"] == "bucket"


def test_v1_factory_builds_writer(monkeypatch):
    seen = {}

    class Stub:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(speedtest_runner, "InfluxDB1Writer", Stub)
    monkeypatch.setattr(speedtest_runner, "INFLUXDB_VERSION", "1")
    monkeypatch.setattr(speedtest_runner, "INFLUXDB_USERNAME", "u")
    assert isinstance(speedtest_runner.create_influxdb_writer(), Stub)
    assert seen["username"] == "u" and seen["database"] == "db"


def test_base_writer_is_abstract():
    with pytest.raises(NotImplementedError):
        speedtest_runner.InfluxDBWriter().write_point("m", {}, {})
    speedtest_runner.InfluxDBWriter().close()


def test_v2_writer_drops_none(monkeypatch):
    """Exercise the real v2 writer against a stubbed client: no network."""
    import influxdb_client

    writes = []

    class StubWriteApi:
        def write(self, bucket, record):
            writes.append((bucket, record))

    class StubClient:
        def __init__(self, **kw):
            self.closed = False

        def write_api(self, **kw):
            return StubWriteApi()

        def close(self):
            self.closed = True

    monkeypatch.setattr(influxdb_client, "InfluxDBClient", StubClient)
    w = speedtest_runner.InfluxDB2Writer("http://influx.invalid", "tok", "org", "bkt")
    w.write_point("speedtest", {"a": "x", "b": None}, {"f": 1.5, "g": None})
    assert len(writes) == 1
    line = writes[0][1].to_line_protocol()
    assert "a=x" in line and "f=1.5" in line
    assert "b=" not in line and "g=" not in line
    w.close()
    assert w.client.closed


def test_v1_writer_drops_none(monkeypatch):
    import influxdb

    calls = []

    class StubClient:
        def __init__(self, **kw):
            calls.append(("init", kw))

        def create_database(self, name):
            raise RuntimeError("exists")

        def write_points(self, points):
            calls.append(("write", points))

        def close(self):
            calls.append(("close", None))

    monkeypatch.setattr(influxdb, "InfluxDBClient", StubClient)
    w = speedtest_runner.InfluxDB1Writer("https://influx.invalid:9999", "u", "p", "db")
    assert calls[0][1]["ssl"] is True and calls[0][1]["port"] == 9999
    w.write_point("m", {"a": 1, "b": None}, {"f": 2, "g": None})
    assert calls[1][1] == [{"measurement": "m", "tags": {"a": "1"}, "fields": {"f": 2}}]
    w.close()
    assert calls[-1][0] == "close"
