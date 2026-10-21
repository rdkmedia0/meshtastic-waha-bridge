# SPDX-FileCopyrightText: 2026 Meshtastic WAHA Bridge contributors
#
# SPDX-License-Identifier: MIT

from typing import Final

DOMAIN: Final = "meshtastic_waha_bridge"

MESHTASTIC_DOMAIN: Final = "meshtastic"
MESHTASTIC_SERVICE_SEND_TEXT: Final = "send_text"
EVENT_MESHTASTIC_TEXT_MESSAGE: Final = "meshtastic_api_text_message"
# fired by the Mesh Chat build for messages sent from Home Assistant (panel, actions, other bridges)
EVENT_MESHTASTIC_CHAT_MESSAGE_SENT: Final = "meshtastic_chat_message_sent"
# sent_by label this bridge puts on messages it sends to the mesh, used to avoid mirroring them back
SENT_BY_PREFIX: Final = "WhatsApp: "

CONF_ACKNOWLEDGED: Final = "acknowledged"
CONF_WAHA_URL: Final = "waha_url"
CONF_API_KEY: Final = "api_key"
CONF_SESSION: Final = "session"
CONF_HMAC_KEY: Final = "hmac_key"
CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_WHATSAPP_CHAT_ID: Final = "whatsapp_chat_id"

CONF_GATEWAY_ENTRY_ID: Final = "gateway_entry_id"
CONF_TARGET_TYPE: Final = "target_type"
CONF_CHANNEL_INDEX: Final = "channel_index"
CONF_NODE_ID: Final = "node_id"

CONF_DIRECTION: Final = "direction"
CONF_PREFIX_WHATSAPP_SENDER: Final = "prefix_whatsapp_sender"
CONF_PREFIX_MESH_SENDER: Final = "prefix_mesh_sender"
CONF_LONG_MESSAGES: Final = "long_messages"

TARGET_CHANNEL: Final = "channel"
TARGET_NODE: Final = "node"

DIRECTION_BOTH: Final = "both"
DIRECTION_TO_MESH: Final = "to_mesh"
DIRECTION_TO_WHATSAPP: Final = "to_whatsapp"

LONG_MESSAGES_SPLIT: Final = "split"
LONG_MESSAGES_TRUNCATE: Final = "truncate"

DEFAULT_SESSION: Final = "default"
DEFAULT_DIRECTION: Final = DIRECTION_BOTH
DEFAULT_PREFIX_WHATSAPP_SENDER: Final = True
DEFAULT_PREFIX_MESH_SENDER: Final = True
DEFAULT_LONG_MESSAGES: Final = LONG_MESSAGES_SPLIT

# WAHA webhook events carrying a new message
WAHA_MESSAGE_EVENTS: Final = ("message", "message.any")
WAHA_HMAC_HEADER: Final = "X-Webhook-Hmac"
WAHA_TIMEOUT: Final = 15

# shown in front of the node name for messages that came over the radio
MESH_SENDER_EMOJI: Final = "\U0001f6f0\ufe0f"

# Meshtastic text messages are limited to 200 bytes (including any prefix)
MAX_TEXT_BYTES: Final = 200
# never send more than this many packets for one WhatsApp message, LoRa airtime is precious
MAX_CHUNKS: Final = 5
# WAHA retries webhooks and the gateway can report the same packet more than once
DEDUPLICATE_WINDOW: Final = 50
# sender names are cut to this length before they are used as a prefix on the mesh
MAX_SENDER_NAME: Final = 24
