# Meshtastic WAHA Bridge

A Home Assistant integration that links a WhatsApp chat with a Meshtastic channel or node, using a
[WAHA](https://waha.devlike.pro) (WhatsApp HTTP API) instance that **you host yourself**.

> ## Please read before installing
>
> WAHA is an independent third-party project that automates WhatsApp through **unofficial** means (it drives a
> WhatsApp Web session). It is not affiliated with, endorsed by or supported by this integration, the
> [Meshtastic integration](https://github.com/meshtastic/home-assistant), Meshtastic or Home Assistant.
>
> - You install, run and secure WAHA yourself. This integration only talks to a WAHA instance you point it at.
> - Automating WhatsApp with unofficial clients is against
>   [WhatsApp's Terms of Service](https://www.whatsapp.com/legal/terms-of-service). WhatsApp may suspend or
>   **ban the phone number** you use. Use a number you can afford to lose.
> - By enabling this integration you confirm that you have read the terms and accept this risk. The authors of this
>   integration accept no liability and provide no support for WAHA itself.
>
> If you need an officially supported WhatsApp channel, look at the WhatsApp Business Cloud API instead. It has
> different constraints (templates, 24 hour reply windows) that this integration does not cover.

## What it does

- Messages written in the WhatsApp chat are sent to the mesh through a gateway of the Meshtastic integration,
  prefixed with the sender's name.
- Messages sent from Home Assistant to that channel or node (the Mesh Chat panel, automations, another
  bridge) are posted too, labelled with who sent them. Requires Mesh Chat test build 4 or newer.
- Messages received from the mesh are posted with a satellite emoji and the node's long name, e.g.
  `\U0001f6f0\ufe0f Anna Hiking: on my way`. Names come from the gateway's node database, or from the device name when the
  node is a Home Assistant device.
- Each bridge links one chat (group or contact) with one channel, or one node for direct messages. Add the
  integration several times to bridge more chats.
- With the Mesh Chat build of the Meshtastic integration, bridged messages also appear in the **Mesh Chat** panel,
  labelled `WhatsApp: <name>`.

Only the WAHA URL, API key and optional webhook signing key are stored, in the config entry like any other
integration. The gateway connection stays in the Meshtastic integration.

## Requirements

- Home Assistant 2025.7 or newer
- The [Meshtastic integration](https://github.com/meshtastic/home-assistant) with at least one gateway
- A running WAHA instance (Core or Plus) with a paired WhatsApp session that can reach Home Assistant's webhook URL

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/rdkmedia0/meshtastic-waha-bridge`,
   type *Integration*
2. Download **Meshtastic WAHA Bridge** and restart Home Assistant

### Manual

Copy `custom_components/meshtastic_waha_bridge` into the `custom_components` folder of your configuration and
restart Home Assistant.

## Setup

1. **Run WAHA** following its own documentation, with an API key and a webhook pointing at Home Assistant.
   A minimal example (see the WAHA docs for the details and the current image names):

   ```bash
   docker run -d --name waha -p 3000:3000 \
     -e WHATSAPP_API_KEY=change-me \
     -e WHATSAPP_HOOK_URL=https://your-home-assistant/api/webhook/<id> \
     -e WHATSAPP_HOOK_EVENTS=message.any \
     -e WHATSAPP_HOOK_HMAC_KEY=change-me-too \
     devlikeapro/waha
   ```

   You get the exact webhook URL in step 3; you can also set the webhook per session through WAHA's API instead of
   the environment variables. Start the `default` session and pair it by scanning the QR code in WAHA's dashboard.
2. **Find the chat ID**: groups are listed by WAHA at `GET /api/{session}/groups` (IDs end in `@g.us`), contacts
   use the phone number without `+` followed by `@c.us`. A bare phone number is accepted too.
3. **Add the bridge**: Settings → Devices & services → **Add integration** → *Meshtastic WAHA Bridge*.
   - Confirm the terms, enter the WAHA URL, API key, session name and (optionally) the HMAC key.
   - Enter the chat ID, select the gateway and the mesh target: a channel index (0 is the primary channel) or a
     node for direct messages.
   - The last step shows the **webhook URL** to configure in WAHA. It is shown again in the bridge options.

Home Assistant must be reachable by WAHA at that URL. If WAHA runs on the same network, the internal URL
(Settings → System → Network) is enough; webhooks need no authentication, which is why the HMAC key is
recommended.

## Options

Open the bridge's entry → **Configure**.

| Option | Default | Description |
|---|---|---|
| Direction | Both | Only forward WhatsApp to the mesh, only the mesh to WhatsApp, or both |
| Prefix mesh messages with the WhatsApp sender's name | on | `Alice: on my way` on the mesh (name cut to 24 characters) |
| Prefix WhatsApp messages with the mesh node's name | on | **Anna Hiking**: on my way in WhatsApp |
| WhatsApp messages longer than 200 bytes | Split | Split into up to 5 numbered packets (`[1/3]`) or truncate |

## Good to know

- Meshtastic text messages are limited to 200 bytes including the prefix. A busy WhatsApp group can quickly use up
  LoRa airtime; long messages are split into at most 5 packets, the rest is dropped and logged.
- Every message sent to the mesh comes from the gateway node. The prefix tells mesh users who wrote it.
- Subscribe WAHA to `message.any`, not just `message`: only `message.any` includes messages you send from the
  WhatsApp account WAHA is logged in as, which is usually your own phone.
- Messages posted by this bridge (and anything sent through WAHA's API) are never forwarded back to the mesh,
  and messages the gateway sends are never forwarded to WhatsApp, so a bridge cannot loop.
- The sender's name comes from the WhatsApp push name in the webhook payload, or from WAHA's contact list.
  Without either, the phone number is used.
- Media, stickers, reactions and messages without text are ignored.
- Messages sent to the mesh are passed to the `meshtastic.send_text` action with `sent_by: "WhatsApp: <name>"`.
  The Mesh Chat build of the Meshtastic integration records them in the chat history; other versions ignore the
  label and still send the message.

## Troubleshooting

- *Nothing arrives on the mesh*: check WAHA's webhook configuration and logs; the webhook must receive
  `message.any` events and WAHA must be able to reach the URL. With an HMAC key configured, rejected calls are
  logged by Home Assistant. With debug logging enabled, the bridge logs why it ignores an event (wrong chat,
  no text, sent through the API).
- *Nothing arrives in WhatsApp*: check that the WAHA session is `WORKING` and that the chat ID is right. Errors from
  WAHA are logged as warnings.
- *The bridge shows "not ready"*: WAHA or the Meshtastic gateway could not be reached. The bridge retries.
- Enable debug logging with `logger: logs: custom_components.meshtastic_waha_bridge: debug`.

## License

MIT
