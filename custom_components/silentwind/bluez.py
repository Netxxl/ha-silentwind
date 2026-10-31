"""Bluetooth Classic helpers via BlueZ D-Bus: discovery, pairing and SPP connect.

Home Assistant OS has no UI for pairing Classic devices, so the integration
does it itself. No `from __future__ import annotations` here: dbus-fast reads
the D-Bus signatures from the annotations.
"""

import asyncio
import contextlib
import logging
import os

from dbus_fast import BusType, Variant
from dbus_fast.errors import DBusError
from dbus_fast.service import ServiceInterface, method

_LOGGER = logging.getLogger(__name__)

BLUEZ = "org.bluez"
AGENT_PATH = "/custom/silentwind/agent"
PROFILE_PATH = "/custom/silentwind/spp"
# BlueZ normally answers within milliseconds; a hung call must not block polling
DBUS_TIMEOUT = 10
SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"
KNOWN_NAMES = {
    "slientwind", "silentwind", "win-power", "winpower", "ios-win-power",
}


class BluezError(Exception):
    """Pairing or discovery failed."""


def is_known_name(name: str | None) -> bool:
    return bool(name) and name.strip().lower() in KNOWN_NAMES


class _PinAgent(ServiceInterface):
    def __init__(self, pin: str):
        super().__init__("org.bluez.Agent1")
        self._pin = pin

    @method()
    def Release(self):  # noqa: N802
        pass

    @method()
    def RequestPinCode(self, device: "o") -> "s":  # noqa: F821, N802
        _LOGGER.debug("PIN requested by %s", device)
        return self._pin

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821, N802
        pass

    @method()
    def RequestPasskey(self, device: "o") -> "u":  # noqa: F821, N802
        return int(self._pin) if self._pin.isdigit() else 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: F821, N802
        pass

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821, N802
        pass

    @method()
    def RequestAuthorization(self, device: "o"):  # noqa: F821, N802
        pass

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821, N802
        pass

    @method()
    def Cancel(self):  # noqa: N802
        pass


def _device_path(adapter_path: str, address: str) -> str:
    return f"{adapter_path}/dev_{address.upper().replace(':', '_')}"


async def _connect(negotiate_unix_fd: bool = False):
    from dbus_fast.aio import MessageBus  # noqa: PLC0415 - Linux only

    try:
        return await MessageBus(bus_type=BusType.SYSTEM, negotiate_unix_fd=negotiate_unix_fd).connect()
    except Exception as err:  # noqa: BLE001
        raise BluezError(f"system D-Bus not reachable: {err}") from err


async def _managed_objects(bus) -> dict:
    intro = await bus.introspect(BLUEZ, "/")
    manager = bus.get_proxy_object(BLUEZ, "/", intro).get_interface(
        "org.freedesktop.DBus.ObjectManager"
    )
    return await manager.call_get_managed_objects()


def _adapters(objects: dict) -> list[str]:
    return sorted(path for path, ifaces in objects.items() if "org.bluez.Adapter1" in ifaces)


def _classic_devices(objects: dict) -> dict[str, str]:
    """address -> name of all known devices that look like a Win-Power controller."""
    found = {}
    for ifaces in objects.values():
        dev = ifaces.get("org.bluez.Device1")
        if not dev:
            continue
        name = dev.get("Name", dev.get("Alias", Variant("s", ""))).value
        if is_known_name(name):
            found[dev["Address"].value] = name
    return found


async def _adapter_iface(bus, path: str):
    intro = await bus.introspect(BLUEZ, path)
    return bus.get_proxy_object(BLUEZ, path, intro).get_interface("org.bluez.Adapter1")


async def _discover(bus, adapter_path: str, seconds: float, until=None) -> dict:
    adapter = await _adapter_iface(bus, adapter_path)
    with contextlib.suppress(DBusError):
        await adapter.call_set_discovery_filter({"Transport": Variant("s", "bredr")})
    started = False
    try:
        await adapter.call_start_discovery()
        started = True
    except DBusError as err:
        _LOGGER.debug("StartDiscovery: %s", err)  # e.g. already in progress
    try:
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while loop.time() < end:
            await asyncio.sleep(1)
            objects = await _managed_objects(bus)
            if until is not None and until(objects):
                return objects
        return await _managed_objects(bus)
    finally:
        if started:
            with contextlib.suppress(DBusError):
                await adapter.call_stop_discovery()


async def async_discover_controllers(seconds: float = 12) -> dict[str, str]:
    """Classic inquiry; returns address -> name of Win-Power/Silentwind controllers."""
    bus = await _connect()
    try:
        objects = await _managed_objects(bus)
        adapters = _adapters(objects)
        if not adapters:
            raise BluezError("no Bluetooth adapter")
        objects = await _discover(bus, adapters[0], seconds)
        return _classic_devices(objects)
    except DBusError as err:
        raise BluezError(f"BlueZ: {err.type}: {err.text}") from err
    finally:
        bus.disconnect()


async def _find_device(bus, address: str):
    """Device1 interface of address; runs a Classic inquiry if BlueZ does not know it yet."""
    objects = await _managed_objects(bus)
    adapters = _adapters(objects)
    if not adapters:
        raise BluezError("no Bluetooth adapter")
    path = next(
        (_device_path(a, address) for a in adapters if _device_path(a, address) in objects),
        None,
    )
    if path is None:
        path = _device_path(adapters[0], address)
        objects = await _discover(bus, adapters[0], 20, until=lambda o: path in o)
        if path not in objects:
            raise BluezError(f"{address} not found by Bluetooth Classic inquiry")
    props = objects[path]["org.bluez.Device1"]

    def prop(name, default=None):
        variant = props.get(name)
        return default if variant is None else variant.value

    _LOGGER.debug(
        "%s: name=%s class=%s paired=%s trusted=%s connected=%s uuids=%s",
        address,
        prop("Name", "?"),
        hex(prop("Class")) if "Class" in props else "?",
        prop("Paired"),
        prop("Trusted"),
        prop("Connected"),
        prop("UUIDs", []),
    )
    intro = await bus.introspect(BLUEZ, path)
    device = bus.get_proxy_object(BLUEZ, path, intro).get_interface("org.bluez.Device1")
    if prop("Connected", False):
        # a link left over from an earlier attempt blocks the single-connection module
        _LOGGER.debug("%s: dropping stale BlueZ connection", address)
        with contextlib.suppress(DBusError, TimeoutError):
            await asyncio.wait_for(device.call_disconnect(), DBUS_TIMEOUT)
            await asyncio.sleep(1)
    return device


async def _bluez_manager(bus, interface: str):
    intro = await bus.introspect(BLUEZ, "/org/bluez")
    return bus.get_proxy_object(BLUEZ, "/org/bluez", intro).get_interface(interface)


async def async_ensure_paired(address: str, pin: str, timeout: float = 40) -> None:
    """Make sure BlueZ knows, pairs and trusts the controller."""
    address = address.upper()
    bus = await _connect()
    try:
        device = await asyncio.wait_for(_find_device(bus, address), 30 + DBUS_TIMEOUT)
        if await asyncio.wait_for(device.get_paired(), DBUS_TIMEOUT):
            return

        _LOGGER.info("Pairing %s", address)
        bus.export(AGENT_PATH, _PinAgent(pin))
        manager = await _bluez_manager(bus, "org.bluez.AgentManager1")
        await manager.call_register_agent(AGENT_PATH, "KeyboardDisplay")
        try:
            await asyncio.wait_for(device.call_pair(), timeout)
        except DBusError as err:
            if "AlreadyExists" not in str(err.type):
                raise BluezError(f"pairing failed: {err.text or err.type}") from err
        except TimeoutError as err:
            with contextlib.suppress(DBusError):
                await device.call_cancel_pairing()
            raise BluezError("pairing timed out") from err
        finally:
            with contextlib.suppress(DBusError):
                await manager.call_unregister_agent(AGENT_PATH)
            bus.unexport(AGENT_PATH)
        with contextlib.suppress(DBusError):
            await device.set_trusted(True)
    except DBusError as err:
        raise BluezError(f"BlueZ: {err.type}: {err.text}") from err
    except TimeoutError as err:
        raise BluezError("BlueZ did not answer") from err
    finally:
        bus.disconnect()


class _SppProfile(ServiceInterface):
    """Receives the RFCOMM socket BlueZ opens for ConnectProfile(SPP)."""

    def __init__(self):
        super().__init__("org.bluez.Profile1")
        self.fd: asyncio.Future = asyncio.get_running_loop().create_future()

    @method()
    def Release(self):  # noqa: N802
        pass

    @method()
    def NewConnection(self, device: "o", fd: "h", fd_properties: "a{sv}"):  # noqa: F821, N802
        _LOGGER.debug("SPP connection to %s, fd %s, %s", device, fd, fd_properties)
        if self.fd.done():
            os.close(fd)
        else:
            self.fd.set_result(fd)

    @method()
    def RequestDisconnection(self, device: "o"):  # noqa: F821, N802
        _LOGGER.debug("BlueZ requests disconnection of %s", device)


class SppConnection:
    """RFCOMM link that BlueZ opened after an SDP lookup, like Android does.

    The D-Bus connection and the profile registration must live as long as the link.
    """

    def __init__(self, bus, manager, device, fd: int):
        self._bus = bus
        self._manager = manager
        self._device = device
        self.fd = fd

    async def close(self) -> None:
        try:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._device.call_disconnect_profile(SPP_UUID), DBUS_TIMEOUT)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._manager.call_unregister_profile(PROFILE_PATH), DBUS_TIMEOUT)
            with contextlib.suppress(Exception):
                self._bus.unexport(PROFILE_PATH)
        finally:
            # BlueZ drops the profile anyway once our D-Bus connection is gone
            self._bus.disconnect()


async def async_connect_spp(address: str, timeout: float = 30) -> SppConnection:
    """Let BlueZ look up the serial port service and connect to it."""
    bus = await _connect(negotiate_unix_fd=True)
    manager = device = None
    try:
        device = await asyncio.wait_for(_find_device(bus, address.upper()), 30 + DBUS_TIMEOUT)
        profile = _SppProfile()
        bus.export(PROFILE_PATH, profile)
        manager = await _bluez_manager(bus, "org.bluez.ProfileManager1")
        await asyncio.wait_for(manager.call_register_profile(
            PROFILE_PATH,
            SPP_UUID,
            {
                "Role": Variant("s", "client"),
                "Name": Variant("s", "Silentwind"),
                # like Android's secure RFCOMM socket: authenticated + encrypted
                "RequireAuthentication": Variant("b", True),
                "RequireAuthorization": Variant("b", False),
            },
        ), DBUS_TIMEOUT)
        await asyncio.wait_for(device.call_connect_profile(SPP_UUID), timeout)
        fd = await asyncio.wait_for(profile.fd, 5)
    except (DBusError, TimeoutError) as err:
        with contextlib.suppress(Exception):
            if device is not None:
                # abort a connect still in progress
                await asyncio.wait_for(device.call_disconnect(), DBUS_TIMEOUT)
        if manager is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(manager.call_unregister_profile(PROFILE_PATH), DBUS_TIMEOUT)
        bus.disconnect()
        text = err.text if isinstance(err, DBusError) else "timeout"
        raise BluezError(f"SPP connect failed: {text}") from err
    except BaseException:
        bus.disconnect()
        raise
    return SppConnection(bus, manager, device, fd)
