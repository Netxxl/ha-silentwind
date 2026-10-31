"""Win-Power / Silentwind controller protocol (from the Silentwind Android app 1.3.3).

Every frame is 42 bytes, in both directions:

    0..3   EB 90 EB 90      header
    4      address          (app sends 0)
    5      type             request: 0 live data, 1 read parameters,
                                     3 FACTORY RESET (never sent here), 4 write parameters
                            answer:  6 live data, 7/8/9 parameters
    6..7   00 20            payload length 32
    8..39  payload
    40..41 checksum         16-bit sum of bytes 4..39, big endian

No Home Assistant imports here, so it can be unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass

HEADER = b"\xeb\x90\xeb\x90"
FRAME_LEN = 42

REQ_LIVE = 0
REQ_PARAMS = 1
REQ_WRITE_PARAMS = 4
RESP_LIVE = 6
RESP_PARAMS = (7, 8, 9)

# Parameter frame byte offsets (absolute, i.e. 8 = first payload byte)
P_SYSTEM = 8  # low nibble: system voltage code, bits 4..6: rated power code
P_FLAGS = 9  # bit 2 (0x04): manual brake
P_ABSORPTION_V = 10
P_STORM_BRAKE_A = 11
P_UNDERVOLTAGE_OFF_V = 12
P_UNDERVOLTAGE_ON_V = 13
P_OVERVOLTAGE_V = 14
P_LIGHT_ON_V = 15
P_LIGHT_OFF_V = 16
P_LOW_WIND_BOOST_V = 22
P_LOAD1_MODE = 23
P_LOAD1_HALF_POWER_H = 24
P_LOAD1_OFF_H = 25
P_LOAD2_MODE = 26
P_LOAD2_HALF_POWER_H = 27
P_LOAD2_OFF_H = 28

BRAKE_BIT = 0x04

# One-hot load modes as used by the app's settings page.
LOAD_MODES = {
    1: "light_on_off",
    2: "light_on_timer_off",
    4: "light_on_half_light_off",
    8: "light_on_half_timer_off",
    16: "constant_on",
    32: "constant_off",
    64: "constant_half",
}
LOAD_ON = 16
LOAD_OFF = 32

_VOLTAGES = {0: 12, 1: 24, 2: 36, 3: 48, 4: 96, 5: 110, 6: 120, 7: 220, 8: 240, 9: 360, 10: 480}
_POWERS = {0: 300, 1: 600, 2: 1000, 3: 2000, 4: 3000, 5: 5000, 6: 10000, 7: 20000}
# system voltage -> (live voltage factor, settings voltage factor)
_KV = {
    12: (0.05, 0.1), 24: (0.1, 0.2), 36: (0.2, 0.3), 48: (0.2, 0.4), 96: (0.5, 0.8),
    110: (0.5, 1.0), 120: (0.5, 1.0), 220: (1.0, 2.0), 240: (1.0, 2.0), 360: (2.0, 3.0),
    480: (2.0, 4.0),
}
_KI_HIGH = {(24, 1000), (48, 2000), (240, 10000), (480, 20000), (120, 5000)}
_KI_HIGHER = {(48, 3000), (48, 5000), (120, 10000), (240, 20000)}
_KP = {
    (24, 600): 0.01, (24, 1000): 0.02, (48, 2000): 0.04, (480, 20000): 0.4,
    (240, 10000): 0.2, (120, 5000): 0.1, (48, 3000): 0.08, (48, 5000): 0.08,
    (120, 10000): 0.2, (240, 20000): 0.4,
}


class FrameError(Exception):
    """Malformed frame."""


def checksum(frame: bytes | bytearray) -> int:
    return sum(frame[4:40]) & 0xFFFF


def build_frame(frame_type: int, payload: bytes = bytes(32), address: int = 0) -> bytes:
    if len(payload) != 32:
        raise ValueError("payload must be 32 bytes")
    body = bytearray(HEADER + bytes([address, frame_type, 0, 32]) + payload + b"\0\0")
    body[40:42] = checksum(body).to_bytes(2, "big")
    return bytes(body)


def extract_frame(buf: bytearray) -> bytes | None:
    """Remove and return the first valid frame in buf, discarding garbage before it."""
    while True:
        start = buf.find(HEADER)
        if start < 0:
            # keep a possible partial header at the end
            del buf[: max(0, len(buf) - 3)]
            return None
        if start:
            del buf[:start]
        if len(buf) < FRAME_LEN:
            return None
        frame = bytes(buf[:FRAME_LEN])
        if int.from_bytes(frame[40:42], "big") == checksum(frame):
            del buf[:FRAME_LEN]
            return frame
        del buf[:1]  # false header or corrupted frame: resync


@dataclass(frozen=True)
class Scale:
    system_voltage: int
    rated_power: int
    kv: float  # live voltages
    ki: float  # live currents
    kp: float  # live power
    kv_set: float  # voltage settings
    ki_set: float  # current settings

    @classmethod
    def from_code(cls, code: int) -> Scale:
        volt = _VOLTAGES.get(code & 0x0F, 0)
        power = _POWERS.get((code & 0x70) >> 4, 0)
        kv, kv_set = _KV.get(volt, (1.0, 0.0))
        if (volt, power) in _KI_HIGH:
            ki, ki_set = 0.2, 0.4
        elif (volt, power) in _KI_HIGHER:
            ki, ki_set = 0.4, 0.8
        else:
            ki, ki_set = 0.1, 0.2
        kp = _KP.get((volt, power), volt / 24 * 0.01)
        return cls(volt, power, kv, ki, kp, kv_set, ki_set)


def _u9(frame: bytes, i: int) -> int:
    return (frame[i] << 1) | (frame[i + 1] >> 7)


def _u18(frame: bytes, i: int) -> int:
    return (frame[i] << 10) | (frame[i + 1] << 2) | (frame[i + 2] >> 6)


@dataclass(frozen=True)
class LiveData:
    battery_voltage: float
    battery_current: float
    battery_power: float
    wind_voltage: float
    wind_current: float
    wind_power: float
    solar_voltage: float
    solar_current: float
    solar_power: float
    load1_current: float
    load2_current: float
    battery_overvoltage: bool
    battery_undervoltage: bool
    load_overload: bool
    load_short_circuit: bool
    dumping: bool  # "Unload": dump load active
    overspeed_brake: bool  # "RapidBrake"
    manual_brake: bool
    load1_on: bool
    load2_on: bool

    @classmethod
    def parse(cls, frame: bytes, scale: Scale) -> LiveData:
        if frame[5] != RESP_LIVE:
            raise FrameError(f"not a live data frame (type {frame[5]})")
        battery_v = _u9(frame, 10) * scale.kv
        wind_w = _u18(frame, 16) * scale.kp
        solar_w = _u18(frame, 19) * scale.kp
        battery_w = wind_w + solar_w
        status, outputs = frame[8], frame[9]
        return cls(
            battery_voltage=round(battery_v, 2),
            battery_current=round(battery_w / battery_v, 1) if battery_v else 0.0,
            battery_power=round(battery_w, 2),
            wind_voltage=round(_u9(frame, 12) * scale.kv, 2),
            wind_current=round(_u9(frame, 22) * scale.ki, 2),
            wind_power=round(wind_w, 2),
            solar_voltage=round(_u9(frame, 14) * scale.kv, 2),
            solar_current=round(_u9(frame, 24) * scale.ki, 2),
            solar_power=round(solar_w, 2),
            load1_current=round(frame[27] * 0.1, 1),
            load2_current=round(frame[26] * 0.1, 1),
            battery_overvoltage=bool(status & 0x01),
            battery_undervoltage=bool(status & 0x02),
            load_overload=bool(status & 0x04),
            load_short_circuit=bool(status & 0x08),
            dumping=bool(status & 0x10),
            overspeed_brake=bool(status & 0x20),
            manual_brake=bool(status & 0x40),
            load1_on=bool(outputs & 0x01),
            load2_on=bool(outputs & 0x02),
        )


class Params:
    """The controller's parameter block (answer types 7/8/9), editable and re-sendable."""

    def __init__(self, frame: bytes) -> None:
        if frame[5] not in RESP_PARAMS:
            raise FrameError(f"not a parameter frame (type {frame[5]})")
        self.frame = bytearray(frame)
        self.scale = Scale.from_code(frame[P_SYSTEM])

    def __getitem__(self, offset: int) -> int:
        return self.frame[offset]

    def copy(self) -> Params:
        return Params(bytes(self.frame))

    @property
    def manual_brake(self) -> bool:
        return bool(self.frame[P_FLAGS] & BRAKE_BIT)

    def set_manual_brake(self, on: bool) -> None:
        if on:
            self.frame[P_FLAGS] |= BRAKE_BIT
        else:
            self.frame[P_FLAGS] &= ~BRAKE_BIT & 0xFF

    def get_voltage(self, offset: int) -> float:
        return round(self.frame[offset] * self.scale.kv_set, 1)

    def get_current(self, offset: int) -> float:
        return round(self.frame[offset] * self.scale.ki_set, 1)

    def set_scaled(self, offset: int, value: float, factor: float) -> None:
        if factor <= 0:
            raise ValueError("unknown system voltage, cannot scale settings")
        raw = int(round(value / factor))
        if not 0 <= raw <= 255:
            raise ValueError(f"{value} out of range (max {255 * factor:.1f})")
        self.frame[offset] = raw

    def set_raw(self, offset: int, value: int) -> None:
        if not 0 <= value <= 255:
            raise ValueError("byte out of range")
        self.frame[offset] = value

    def write_frame(self) -> bytes:
        """Type-4 frame carrying this parameter block, as the app builds it."""
        return build_frame(REQ_WRITE_PARAMS, bytes(self.frame[8:40]))
