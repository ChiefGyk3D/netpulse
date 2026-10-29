"""The network guard must bite: proves tests cannot reach the network."""

import os
import socket

import pytest
from conftest import NetworkBlocked


def test_name_resolution_blocked():
    for fn in (socket.gethostbyname, socket.gethostbyname_ex):
        with pytest.raises(NetworkBlocked):
            fn("example.invalid")
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("example.invalid", 80)


def test_socket_calls_blocked():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(NetworkBlocked):
            s.connect(("127.0.0.1", 9))
        with pytest.raises(NetworkBlocked):
            s.connect_ex(("127.0.0.1", 9))
        with pytest.raises(NetworkBlocked):
            s.sendto(b"x", ("127.0.0.1", 9))
        with pytest.raises(NetworkBlocked):
            s.sendto(b"x", 0, ("127.0.0.1", 9))
        with pytest.raises(NetworkBlocked):
            s.sendmsg([b"x"], [], 0, ("127.0.0.1", 9))
    finally:
        s.close()


def test_real_requests_get_is_blocked():
    import requests

    with pytest.raises(Exception) as exc:  # any failure proves no network; the type varies with requests
        requests.get("http://example.invalid/", timeout=1)
    assert "blocked" in repr(exc.value)


def test_env_is_cleared():
    assert not [k for k in os.environ if k.startswith(("INFLUXDB_", "SPEEDTEST_"))]
