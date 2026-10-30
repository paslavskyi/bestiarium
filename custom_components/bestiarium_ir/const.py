"""Constants for bestiarium_ir."""

from __future__ import annotations

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "bestiarium_ir"

SUBENTRY_TYPE_BLASTER = "blaster"
SUBENTRY_TYPE_HISENSE = "hisense"

CONF_ZHA_DEVICE = "zha_device"
CONF_EMITTER = "emitter"

# The IR command of a Zosung blaster (TS1201) in ZHA, as the home's scripts
# send it
ZHA_DOMAIN = "zha"
ZHA_SERVICE = "issue_zigbee_cluster_command"
ZHA_MODEL = "TS1201"
ZOSUNG_ENDPOINT = 1
ZOSUNG_CLUSTER = 0xE004
ZOSUNG_SEND = 2

# The unit does not carry out a frame sooner after the previous one (FEAT-025)
FRAME_PAUSE_SECONDS = 3
