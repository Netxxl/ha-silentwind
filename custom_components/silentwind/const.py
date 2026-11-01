"""Constants for the Silentwind Windgenerator integration."""

DOMAIN = "silentwind"

CONF_CHANNEL = "channel"
CONF_PIN = "pin"
CONF_HAS_SOLAR = "has_solar"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_KEEP_CONNECTED = "keep_connected"
CONF_APP_ACCESS_MINUTES = "app_access_minutes"

DEFAULT_CHANNEL = 0  # 0 = probe channels 1..6
DEFAULT_PIN = "1234"
DEFAULT_SCAN_INTERVAL = 20
# One link per poll fails now and then; a kept link only reconnects when it breaks.
DEFAULT_KEEP_CONNECTED = True
# How long the "app access" switch keeps Home Assistant off the Bluetooth link.
DEFAULT_APP_ACCESS_MINUTES = 10
# Re-read the parameter block (brake/load state, settings) every n polls.
PARAMS_EVERY = 4
