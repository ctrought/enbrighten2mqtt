"""LocalDevice error handling, with a fake tinytuya device (no network)."""

import pytest

from enbrighten2mqtt.local.device import DeviceError, DeviceInfo, LocalDevice

EMPTY_REPLY = {"Error": "Unexpected Payload from Device", "Err": "904", "Payload": None}
TIMEOUT = {"Error": "Timeout Waiting for Device", "Err": "902", "Payload": None}


class FakeTuya:
    def __init__(self, reply):
        self.reply = reply

    def status(self):
        return self.reply

    def set_multiple_values(self, dps, nowait=False):
        return self.reply

    def receive(self):
        return self.reply

    def set_socketTimeout(self, s):
        pass


def make_dev(reply):
    dev = LocalDevice(DeviceInfo("id", "1.2.3.4", "key", 3.5))
    dev._dev = FakeTuya(reply)
    dev._connected = True
    return dev


@pytest.mark.parametrize("call", [
    lambda d: d.status(), lambda d: d.set_dp(20, True), lambda d: d.receive()])
def test_empty_reply_keeps_the_session(call):
    dev = make_dev(EMPTY_REPLY)
    assert call(dev) is None
    assert dev.connected


@pytest.mark.parametrize("call", [lambda d: d.status(), lambda d: d.set_dp(20, True)])
def test_real_failure_raises_and_drops_the_session(call):
    dev = make_dev(TIMEOUT)
    with pytest.raises(DeviceError):
        call(dev)
    assert not dev.connected


def test_receive_failure_drops_the_session_without_raising():
    dev = make_dev(TIMEOUT)
    dev.receive()
    assert not dev.connected


def test_receive_delivers_pushed_dps():
    seen = []
    dev = make_dev({"dps": {"20": True}})
    dev.on_dps = seen.append
    dev.receive()
    assert seen == [{"20": True}]
