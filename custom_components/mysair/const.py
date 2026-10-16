DOMAIN = "mysair"

# Time to wait for a command acknowledgement (ACK) on the feedback topic
# before logging a warning. Value taken from the official app
# (VUE_APP_OUTSERVICE_MILISECOND=5000, see docs/protocol-findings.md §6b).
FEEDBACK_TIMEOUT_SECONDS = 5

# Maximum age of an MQTT status before an entity is marked unavailable (C5).
# Three times the interval of the periodic fallback refresh (120 s,
# __init__.py), so normal jitter does not cause false "unavailable" states.
MQTT_STALE_AFTER_SECONDS = 360

# Common attributes
ATTR_TARGET_TEMP = "target_temperature"
ATTR_CURRENT_TEMP = "current_temperature"
ATTR_MODE = "mode"
ATTR_HVAC_STATE = "hvac_action"

# Default intervals
SCAN_INTERVAL = 60  # seconds

# Service mysair.stop_installation (F5)
SERVICE_STOP_INSTALLATION = "stop_installation"
ATTR_INSTALLATION_REF = "installation_ref"
