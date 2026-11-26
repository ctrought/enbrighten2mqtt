"""LAN discovery cache: no scanner, no network."""

import pytest

from enbrighten2mqtt.local import discovery as D


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    D.forget()
    clock = {"t": 1000.0}
    monkeypatch.setattr(D.time, "time", lambda: clock["t"])
    yield clock
    D.forget()


def fake_scan(monkeypatch, results):
    scans = []

    def scan(want_ids, scantime):
        scans.append(want_ids)
        return results.pop(0) if results else {}
    monkeypatch.setattr(D, "_scan", scan)
    return scans


def test_recent_sighting_is_shared(monkeypatch):
    scans = fake_scan(monkeypatch, [{"a": {"ip": "10.0.0.1", "version": 3.5},
                                     "b": {"ip": "10.0.0.2", "version": 3.5}}])
    assert D.find_device_ip("a") == ("10.0.0.1", 3.5)
    assert D.find_device_ip("b") == ("10.0.0.2", 3.5)
    assert len(scans) == 1


def test_stale_ip_is_never_returned(monkeypatch, fresh):
    fake_scan(monkeypatch, [{"a": {"ip": "10.0.0.1", "version": 3.5}}, {}])
    assert D.find_device_ip("a")[0] == "10.0.0.1"
    fresh["t"] += 60
    assert D.find_device_ip("a") == (None, None)     # offline, not the old IP


def test_moved_device_gets_new_ip(monkeypatch, fresh):
    fake_scan(monkeypatch, [{"a": {"ip": "10.0.0.1", "version": 3.5}},
                            {"a": {"ip": "10.0.0.9", "version": 3.5}}])
    D.find_device_ip("a")
    fresh["t"] += 60
    assert D.find_device_ip("a")[0] == "10.0.0.9"


def test_other_devices_scan_does_not_hide_a_missing_one(monkeypatch):
    scans = fake_scan(monkeypatch, [{"a": {"ip": "10.0.0.1", "version": 3.5}},
                                    {"b": {"ip": "10.0.0.2", "version": 3.5}}])
    D.find_device_ip("a")
    assert D.find_device_ip("b")[0] == "10.0.0.2"    # a fresh scan, not a's cache
    assert scans == [["a"], ["b"]]


def test_full_scan_always_scans(monkeypatch):
    scans = fake_scan(monkeypatch, [{"a": {"ip": "10.0.0.1", "version": 3.5}},
                                    {"a": {"ip": "10.0.0.1", "version": 3.5}}])
    D.discover()
    D.discover()
    assert scans == [None, None]
