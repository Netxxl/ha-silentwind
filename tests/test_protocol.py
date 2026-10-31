"""Tests for protocol.py and client.py (no Home Assistant needed).

Run: python tests/test_protocol.py   (or python -m pytest tests)
"""

import asyncio
import importlib.util
from pathlib import Path
import sys
import types

# Load protocol.py/client.py without importing the HA-dependent package __init__.
PKG = Path(__file__).resolve().parents[1] / "custom_components" / "silentwind"
pkg = types.ModuleType("silentwind")
pkg.__path__ = [str(PKG)]
sys.modules["silentwind"] = pkg
for name in ("protocol", "client"):
    spec = importlib.util.spec_from_file_location(f"silentwind.{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

from silentwind import protocol as p  # noqa: E402
from silentwind.client import SilentwindClient, SilentwindTimeout  # noqa: E402

SYSTEM_12V_600W = 0x10


def live_payload() -> bytearray:
    """12 V system: 13.6 V battery, wind 20 V / 5 A / 100 W, manual brake, load 1 on."""
    f = bytearray(42)
    f[8] = 0x40
    f[9] = 0x01
    f[10], f[11] = 272 >> 1, 0  # 272 * 0.05 = 13.6 V
    f[12], f[13] = 400 >> 1, 0  # 20.0 V
    raw_w = 20000  # * 0.005 = 100 W
    f[16], f[17], f[18] = raw_w >> 10, (raw_w >> 2) & 0xFF, (raw_w & 3) << 6
    f[22], f[23] = 50 >> 1, 0  # 5.0 A
    f[27] = 23  # load 1: 2.3 A
    return f[8:40]


def params_payload() -> bytearray:
    f = bytearray(42)
    f[p.P_SYSTEM] = SYSTEM_12V_600W
    f[p.P_FLAGS] = 0x00
    f[p.P_ABSORPTION_V] = 144  # * 0.1 = 14.4 V
    f[p.P_STORM_BRAKE_A] = 100  # * 0.2 = 20 A
    f[p.P_LOAD1_MODE] = p.LOAD_ON
    f[p.P_LOAD2_MODE] = p.LOAD_OFF
    return f[8:40]


def test_request_frames_match_app():
    assert p.build_frame(p.REQ_LIVE).hex() == "eb90eb9000000020" + "00" * 32 + "0020"
    assert p.build_frame(p.REQ_PARAMS).hex().endswith("0021")


def test_scale_table():
    s = p.Scale.from_code(SYSTEM_12V_600W)
    assert (s.system_voltage, s.rated_power, s.kv, s.ki, s.kv_set, s.ki_set) == (12, 600, 0.05, 0.1, 0.1, 0.2)
    assert abs(s.kp - 0.005) < 1e-12
    s = p.Scale.from_code(0x21)  # 24 V 1000 W
    assert (s.kv, s.ki, s.kp, s.ki_set) == (0.1, 0.2, 0.02, 0.4)


def test_extract_skips_garbage_and_bad_checksum():
    good = p.build_frame(p.RESP_LIVE, bytes(live_payload()))
    bad = bytearray(good)
    bad[20] ^= 0xFF
    buf = bytearray(b"\x01\xeb\x90" + bytes(bad) + good + good[:10])
    assert p.extract_frame(buf) == good
    assert p.extract_frame(buf) is None
    assert bytes(buf) == good[:10]


def test_live_parse():
    scale = p.Scale.from_code(SYSTEM_12V_600W)
    live = p.LiveData.parse(p.build_frame(p.RESP_LIVE, bytes(live_payload())), scale)
    assert live.battery_voltage == 13.6
    assert (live.wind_voltage, live.wind_current, live.wind_power) == (20.0, 5.0, 100.0)
    assert live.battery_current == round(100 / 13.6, 1)
    assert live.load1_current == 2.3 and live.load1_on and not live.load2_on
    assert live.manual_brake and not live.overspeed_brake and not live.dumping


def test_params_brake_bit_and_write_frame():
    params = p.Params(p.build_frame(7, bytes(params_payload())))
    assert not params.manual_brake
    assert params.get_voltage(p.P_ABSORPTION_V) == 14.4
    assert params.get_current(p.P_STORM_BRAKE_A) == 20.0
    params.set_manual_brake(True)
    frame = params.write_frame()
    assert frame[:8] == bytes.fromhex("eb90eb9000040020")
    assert frame[p.P_FLAGS] & p.BRAKE_BIT
    assert int.from_bytes(frame[40:42], "big") == p.checksum(frame)
    params.set_manual_brake(False)
    assert not params.manual_brake
    try:
        params.set_scaled(p.P_ABSORPTION_V, 30.0, params.scale.kv_set)
    except ValueError:
        pass
    else:
        raise AssertionError("expected range error")


class FakeController:
    """Answers like the controller: 20-byte chunks with noise, drops every n-th answer."""

    def __init__(self, drop_every: int = 3) -> None:
        self.params = params_payload()
        self.live = live_payload()
        self.drop_every = drop_every
        self.requests: list[int] = []
        self.connected = False
        self.cb = None

    def set_data_callback(self, cb):
        self.cb = cb

    @property
    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def write(self, data: bytes):
        assert len(data) == 42 and data[:4] == p.HEADER
        assert int.from_bytes(data[40:42], "big") == p.checksum(data)
        kind = data[5]
        assert kind != 3, "factory reset must never be sent"
        self.requests.append(kind)
        if kind == p.REQ_WRITE_PARAMS:
            self.params = bytearray(data[8:40])
            self.live[1] = (self.live[1] & ~2 & 0xFF) | (2 if self.params[p.P_LOAD2_MODE - 8] == p.LOAD_ON else 0)
            self.live[0] = 0x40 if self.params[p.P_FLAGS - 8] & p.BRAKE_BIT else 0
            answer = None  # unknown in the app: stay silent
        elif kind == p.REQ_PARAMS:
            answer = p.build_frame(7, bytes(self.params))
        elif kind == p.REQ_LIVE:
            answer = p.build_frame(p.RESP_LIVE, bytes(self.live))
        else:
            answer = None
        if answer is None or len(self.requests) % self.drop_every == 0:
            return
        stream = b"\x00\x13" + answer
        loop = asyncio.get_running_loop()
        for i in range(0, len(stream), 20):
            loop.call_later(0.005 * (i // 20 + 1), self.cb, stream[i : i + 20])


def test_client_poll_and_brake_with_losses():
    async def run():
        dev = FakeController()
        client = SilentwindClient(dev, timeout=0.15, retries=3)
        await client.connect()
        live = await client.read_live()  # reads params first
        assert dev.requests[0] == p.REQ_PARAMS
        assert live.manual_brake and live.battery_voltage == 13.6

        params = client.params.copy()
        params.set_manual_brake(False)
        params.set_raw(p.P_LOAD2_MODE, p.LOAD_ON)
        new = await client.write_params(params)
        assert not new.manual_brake and new[p.P_LOAD2_MODE] == p.LOAD_ON
        live = await client.read_live()
        assert not live.manual_brake and live.load2_on
        assert p.REQ_WRITE_PARAMS in dev.requests

    asyncio.run(run())


def test_client_times_out_when_silent():
    async def run():
        client = SilentwindClient(FakeController(drop_every=1), timeout=0.05, retries=2)
        await client.connect()
        try:
            await client.read_params()
        except SilentwindTimeout:
            return
        raise AssertionError("expected timeout")

    asyncio.run(run())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
