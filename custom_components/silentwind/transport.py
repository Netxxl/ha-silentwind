"""Bluetooth Classic SPP/RFCOMM transport, as used by the Android app."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import ctypes
import errno
import logging
import os
import socket
import struct

from homeassistant.core import HomeAssistant

from .bluez import BluezError, SppConnection, async_connect_spp, async_ensure_paired
from .client import SilentwindConnectionError

_LOGGER = logging.getLogger(__name__)

AF_BLUETOOTH = 31
BTPROTO_RFCOMM = 3
SOL_BLUETOOTH = 274
BT_SECURITY = 4
# Android's createRfcommSocketToServiceRecord() is a "secure" socket:
# authenticated and encrypted. Linux defaults to no security at all.
BT_SECURITY_MEDIUM = 2
BT_SECURITY_OPT = struct.pack("BB", BT_SECURITY_MEDIUM, 0)
RFCOMM_CHANNELS = range(1, 7)
# page timeout etc.: the module is off, out of range or hung
UNREACHABLE = {errno.EHOSTDOWN, errno.EHOSTUNREACH, errno.ETIMEDOUT}
# The app sends its first request up to 1 s after connecting.
CONNECT_SETTLE = 0.8


class _SockaddrRc(ctypes.Structure):
    _fields_ = [
        ("rc_family", ctypes.c_ushort),
        ("rc_bdaddr", ctypes.c_ubyte * 6),
        ("rc_channel", ctypes.c_ubyte),
    ]


def _rfcomm_connect_fd(address: str, channel: int, timeout: float) -> int:
    """Blocking RFCOMM connect; returns a non-blocking fd.

    Uses Python's socket module when it was built with Bluetooth support and
    falls back to libc via ctypes otherwise.
    """
    if hasattr(socket, "AF_BLUETOOTH"):
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(timeout)
        try:
            sock.setsockopt(SOL_BLUETOOTH, BT_SECURITY, BT_SECURITY_OPT)
            sock.connect((address, channel))
        except BaseException:
            sock.close()
            raise
        sock.setblocking(False)
        return sock.detach()

    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.socket(AF_BLUETOOTH, socket.SOCK_STREAM, BTPROTO_RFCOMM)
    if fd < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"RFCOMM socket: {os.strerror(err)}")
    opt = ctypes.create_string_buffer(BT_SECURITY_OPT, len(BT_SECURITY_OPT))
    if libc.setsockopt(fd, SOL_BLUETOOTH, BT_SECURITY, opt, len(BT_SECURITY_OPT)) != 0:
        err = ctypes.get_errno()
        os.close(fd)
        raise OSError(err, f"BT_SECURITY: {os.strerror(err)}")
    addr = _SockaddrRc()
    addr.rc_family = AF_BLUETOOTH
    for i, part in enumerate(reversed(address.split(":"))):
        addr.rc_bdaddr[i] = int(part, 16)
    addr.rc_channel = channel
    if libc.connect(fd, ctypes.byref(addr), ctypes.sizeof(addr)) != 0:
        err = ctypes.get_errno()
        os.close(fd)
        raise OSError(err, os.strerror(err))
    os.set_blocking(fd, False)
    return fd


def _peer_channel(fd: int) -> int | None:
    """RFCOMM channel of a connected socket that BlueZ handed over, without closing it."""
    try:
        sock = socket.socket(fileno=fd)
    except OSError:
        return None  # Python built without Bluetooth support
    try:
        return sock.getpeername()[1]
    except (OSError, IndexError, TypeError):
        return None
    finally:
        sock.detach()


class RfcommTransport:
    """Bluetooth Classic serial port profile, paired automatically with the PIN."""

    def __init__(self, hass: HomeAssistant, address: str, channel: int, pin: str) -> None:
        self._hass = hass
        self._address = address.upper()
        self.channel = channel  # 0 = SDP lookup via BlueZ, else this RFCOMM channel
        self._pin = pin
        self._fd: int | None = None
        self._spp: SppConnection | None = None
        self._closing: asyncio.Task | None = None
        self._learned_channel: int | None = None  # channel of the last working link
        self._callback: Callable[[bytes], None] = lambda _data: None

    def set_data_callback(self, callback: Callable[[bytes], None]) -> None:
        self._callback = callback

    @property
    def is_connected(self) -> bool:
        return self._fd is not None

    async def _try_connect(self, channel: int) -> int:
        return await self._hass.async_add_executor_job(
            _rfcomm_connect_fd, self._address, channel, 15.0
        )

    async def connect(self) -> None:
        try:
            await self._connect()
        except SilentwindConnectionError:
            raise
        except Exception as err:
            _LOGGER.exception("%s: unexpected error while connecting", self._address)
            raise SilentwindConnectionError(
                f"{self._address}: {type(err).__name__}: {err}"
            ) from err

    async def _connect(self) -> None:
        if self._closing is not None:
            await self.disconnect()
        try:
            await async_ensure_paired(self._address, self._pin)
        except BluezError as err:
            raise SilentwindConnectionError(f"{self._address}: {err}") from err

        if self.channel:
            await self._attach(await self._connect_channel([self.channel]))
            return

        # Direct RFCOMM first: BlueZ's SDP route (ConnectProfile) mostly times out
        # with this module and sometimes tries its BLE side.
        channels = list(RFCOMM_CHANNELS)
        if self._learned_channel in channels:
            channels.remove(self._learned_channel)
            channels.insert(0, self._learned_channel)
        try:
            fd = await self._connect_channel(channels)
        except SilentwindConnectionError as err:
            cause = err.__cause__
            if isinstance(cause, OSError) and cause.errno in UNREACHABLE:
                raise  # module does not answer at all, SDP cannot help
            _LOGGER.debug("%s: %s, trying SDP", self._address, err)
        else:
            await self._attach(fd)
            return

        try:
            self._spp = await async_connect_spp(self._address)
        except BluezError as err:
            raise SilentwindConnectionError(f"{self._address}: {err}") from err
        os.set_blocking(self._spp.fd, False)
        self._learned_channel = _peer_channel(self._spp.fd)
        _LOGGER.debug(
            "%s: serial port connected via SDP, channel %s", self._address, self._learned_channel
        )
        await self._attach(self._spp.fd)

    async def _connect_channel(self, channels: list[int]) -> int:
        last: OSError | None = None
        for channel in channels:
            try:
                fd = await self._try_connect(channel)
            except OSError as err:
                last = err
                _LOGGER.debug("RFCOMM %s channel %s: %s", self._address, channel, err)
                if err.errno in (errno.ECONNREFUSED, errno.EINVAL, errno.ENOENT):
                    continue  # nothing listening on this channel
                break
            _LOGGER.debug("RFCOMM %s connected on channel %s", self._address, channel)
            self._learned_channel = channel
            return fd
        raise SilentwindConnectionError(f"RFCOMM {self._address}: {last}") from last

    async def _attach(self, fd: int) -> None:
        self._fd = fd
        asyncio.get_running_loop().add_reader(fd, self._readable)
        await asyncio.sleep(CONNECT_SETTLE)
        if self._fd is None:
            raise SilentwindConnectionError(f"{self._address} closed the connection right away")

    def _readable(self) -> None:
        try:
            data = os.read(self._fd, 512)
        except BlockingIOError:
            return
        except OSError as err:
            _LOGGER.debug("RFCOMM read failed: %s", err)
            data = b""
        if not data:
            self._close()
            return
        self._callback(data)

    def _close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(fd)
            with contextlib.suppress(OSError):
                os.close(fd)
        spp, self._spp = self._spp, None
        if spp is not None:
            self._closing = self._hass.async_create_background_task(
                spp.close(), "silentwind spp close"
            )

    async def disconnect(self) -> None:
        self._close()
        closing, self._closing = self._closing, None
        if closing is not None:
            # the next ConnectProfile only works once the old profile is gone
            with contextlib.suppress(Exception):
                await asyncio.wait_for(closing, 15)

    async def write(self, data: bytes) -> None:
        if self._fd is None:
            raise SilentwindConnectionError("connection was closed by the controller")
        try:
            view = memoryview(data)
            while view:
                try:
                    view = view[os.write(self._fd, view) :]
                except BlockingIOError:
                    await asyncio.sleep(0.02)
        except OSError as err:
            self._close()
            raise SilentwindConnectionError(f"write failed: {err}") from err


def create_transport(hass: HomeAssistant, address: str, channel: int, pin: str) -> RfcommTransport:
    return RfcommTransport(hass, address, channel, pin)
