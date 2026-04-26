# Alf-E — Your Personal AI Agent

Alf-E is a self-hosted AI assistant that lives inside Home Assistant.
It can control your home, answer questions, monitor energy, read your emails,
and run scheduled tasks — all without sending your data to a third-party service.

---

## First-Time Setup

### 1. Get an Anthropic API Key

Go to [console.anthropic.com](https://console.anthropic.com), create an account,
and generate an API key. Paste it into the **Anthropic API Key** field in the
Configuration tab.

### 2. Fill in your details

| Field | What to enter |
|-------|--------------|
| Owner Name | Your first name (e.g. `John`) |
| Household Name | What to call your home (e.g. `The Smith House`) |
| Timezone | Your timezone (e.g. `Australia/Sydney`, `Pacific/Auckland`) |
| Google API Key | Optional — enables Gemini as a cheaper fallback model |
| Playbook | Leave blank to auto-generate, or enter a custom slug |

### 3. Start the add-on

Click **Start**. Alf-E will appear in your HA sidebar within 30 seconds.

---

## Using Alf-E

Open Alf-E from the sidebar and start chatting:

- **"Turn on the living room lights"** — controls HA devices
- **"What's using the most power right now?"** — reads energy sensors
- **"Set a reminder at 6pm to take the bins out"** — schedules a notification
- **"What's the weather like today?"** — fetches a forecast
- **"Switch to [client name] site"** — hops to another HA instance (installer mode)

### Voice

Click the microphone button to speak. Click the speaker button to hear responses.

---

## Multiple HA Sites (Installer Mode)

If you're managing multiple homes, add each client's Nabu Casa URL via the
**+** button in the header. Alf-E will switch between sites on request.

---

## Costs

Alf-E uses Claude (Anthropic) to think. Typical household usage runs
**$1–5 USD per month** depending on how chatty you are.

A daily spend limit of $2 USD is set by default — Alf-E will switch to the
cheaper model automatically if you approach it.

---

## Support

- GitHub: [github.com/majorfrazer/alf-e](https://github.com/majorfrazer/alf-e)
- Installed and configured by Fraser Cole
