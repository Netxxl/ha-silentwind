"""Request/response client over a Bluetooth byte stream.

No Home Assistant imports here, so it can be tested with a fake transport.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection
import logging
from typing import Protocol

from . import protocol
from .protocol import LiveData, Params

_LOGGER = logging.getLogger(__name__)


class SilentwindError(Exception):
    """Base error."""


class SilentwindConnectionError(SilentwindError):
    """Transport could not be opened or broke."""


class SilentwindTimeout(SilentwindError):
    """No valid answer after all retries."""


class Transport(Protocol):
    def set_data_callback(self, callback: Callable[[bytes], None]) -> None: ...
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def write(self, data: bytes) -> None: ...
    @property
    def is_connected(self) -> bool: ...


class SilentwindClient:
    def __init__(self, transport: Transport, timeout: float = 2.5, retries: int = 3) -> None:
        self._transport = transport
        self._timeout = timeout
        self._retries = retries
        self._buf = bytearray()
        self._frames: asyncio.Queue[bytes] = asyncio.Queue()
        self.params: Params | None = None
        transport.set_data_callback(self._on_data)

    @property
    def is_connected(self) -> bool:
        return self._transport.is_connected

    async def connect(self) -> None:
        if not self._transport.is_connected:
            await self._transport.connect()

    async def disconnect(self) -> None:
        await self._transport.disconnect()

    def _on_data(self, data: bytes) -> None:
        self._buf.extend(data)
        while (frame := protocol.extract_frame(self._buf)) is not None:
            self._frames.put_nowait(frame)

    async def _wait_for(self, types: Collection[int]) -> bytes:
        while True:
            frame = await self._frames.get()
            if frame[5] in types:
                return frame
            _LOGGER.debug("ignoring frame type %s", frame[5])

    async def _transact(self, request: bytes, types: Collection[int]) -> bytes:
        for attempt in range(1, self._retries + 1):
            while not self._frames.empty():
                self._frames.get_nowait()
            await self._transport.write(request)
            try:
                return await asyncio.wait_for(self._wait_for(types), self._timeout)
            except TimeoutError:
                _LOGGER.debug(
                    "no answer of type %s to request type %s (attempt %d/%d), buffer %s",
                    list(types), request[5], attempt, self._retries, self._buf.hex(" ") or "empty",
                )
        raise SilentwindTimeout(f"controller did not answer request type {request[5]}")

    async def read_params(self) -> Params:
        frame = await self._transact(protocol.build_frame(protocol.REQ_PARAMS), protocol.RESP_PARAMS)
        self.params = Params(frame)
        return self.params

    async def read_live(self) -> LiveData:
        if self.params is None:
            await self.read_params()
        frame = await self._transact(protocol.build_frame(protocol.REQ_LIVE), (protocol.RESP_LIVE,))
        return LiveData.parse(frame, self.params.scale)

    async def write_params(self, params: Params) -> Params:
        """Send a parameter block, then read it back for verification."""
        while not self._frames.empty():
            self._frames.get_nowait()
        await self._transport.write(params.write_frame())
        # The app does not wait for a specific answer type; give the controller a moment.
        try:
            frame = await asyncio.wait_for(self._wait_for(protocol.RESP_PARAMS), self._timeout)
            _LOGGER.debug("answer to write: type %s", frame[5])
        except TimeoutError:
            _LOGGER.debug("no direct answer to parameter write")
        return await self.read_params()
