# Alf-E — your household AI agent

A self-hosted personal AI that lives on your own hardware. Reads your Home
Assistant, drafts automations for you, answers questions about the house,
and — once wired up — handles email, calendar, weather, and more.

No subscriptions. No cloud dependency beyond the AI brain itself.
Your data stays on your machine.

---

## What you'll need

1. A computer that stays on (Mac, Windows PC, Linux box, mini PC — anything
   that can run Docker). Can be the same machine as Home Assistant.
2. **Home Assistant** already running somewhere on your network.
3. An **Anthropic API key** — https://console.anthropic.com
   - Sign up, add $20 of credit (typically lasts months).
4. About 15 minutes.

---

## Quick install

### macOS

1. Make sure **Docker Desktop** is installed and running. If not, get it from
   https://www.docker.com/products/docker-desktop — install, open the app,
   wait for the whale icon in the menu bar to say *"Docker is running"*.
2. Double-click **`install-mac.command`**
3. Follow the prompts. The first run opens `.env` in TextEdit — paste your
   two API keys (Anthropic + Home Assistant), save, then run the installer
   again.
4. Done. Your browser opens to `http://localhost:8099`.

### Windows

1. Install **Docker Desktop** from https://www.docker.com/products/docker-desktop
   Open the app; wait for the whale icon to go green.
2. Double-click **`install-windows.bat`**
3. Follow the prompts. Notepad opens `.env` for you to paste keys into.
4. Done. Your browser opens to `http://localhost:8099`.

### Linux / HA Green / Ubuntu mini PC

1. Install Docker if you don't have it:
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   # log out and back in
   ```
2. In a terminal, `cd` into this folder and run:
   ```bash
   chmod +x install-linux.sh
   ./install-linux.sh
   ```
3. Follow the prompts. Edit `.env` with `nano`, paste your keys, re-run the
   script.
4. Open `http://localhost:8099` (or `http://<this-pc-ip>:8099` from another
   machine on your network).

---

## Getting your two API keys

### Anthropic API key

1. Go to https://console.anthropic.com
2. Sign in / sign up.
3. Click **API Keys** → **Create Key**.
4. Copy the key.
5. Click **Billing** → add $20 credit. That typically lasts months of home use.

### Home Assistant token

1. Open Home Assistant in your browser.
2. Click your user icon (bottom-left corner).
3. Click the **Security** tab.
4. Scroll to **"Long-Lived Access Tokens"** → **Create Token**.
5. Name it "Alf-E", click OK, copy the entire long string.

---

## After install: log in

Open `http://localhost:8099`. The PWA asks for a login token.

Your token is in the `.env` file, on the line starting with `ALFE_API_TOKEN=`.
Copy the part after the `=` sign and paste it into the login box.

**Pro tip:** bookmark the page and let your browser save the token. You won't
need to enter it again.

**Install as a home-screen app:** on iPhone/iPad, tap Share → *"Add to Home
Screen"*. On Android, tap the three dots → *"Install app"*. On Mac/Windows,
Chrome shows an install icon in the address bar.

---

## Add Alf-E to your HA sidebar (optional, very nice)

Want Alf-E to show up inside Home Assistant itself? In your HA config folder,
create a file called `alfe_dashboard.yaml`:

```yaml
title: Alf-E
views:
  - title: Alf-E
    panel: true
    cards:
      - type: iframe
        url: "http://localhost:8099?token=YOUR_ALFE_API_TOKEN_HERE"
        aspect_ratio: 100%
```

Replace `YOUR_ALFE_API_TOKEN_HERE` with the `ALFE_API_TOKEN` value from `.env`.

Then in your `configuration.yaml`, add:

```yaml
lovelace:
  mode: storage
  dashboards:
    alfe-dashboard:
      mode: yaml
      title: Alf-E
      icon: mdi:robot
      show_in_sidebar: true
      filename: alfe_dashboard.yaml
```

Restart HA. Alf-E is now in your sidebar.

---

## Updating Alf-E

From the folder Alf-E was installed in:

```bash
docker compose pull
docker compose up -d
```

That pulls the latest build and restarts — your `.env` and playbook stay put.

---

## Troubleshooting

**"Connection error" on first open**
Wait 30 seconds and refresh. The first start downloads ~500MB.

**"Invalid token" on login**
Copy the `ALFE_API_TOKEN` from `.env` *exactly* — no quotes, no leading space.

**Can't reach Home Assistant**
Check `HA_URL` in `.env`. If HA is on a different machine, change
`host.docker.internal` to the HA machine's IP address. If HA is in a
different Docker stack, use that container's name or the host IP.

**Something else**
Open a terminal in the Alf-E folder and run:
```bash
docker compose logs -f alf-e
```
Send the output to whoever installed it for you.

---

## What's next

This comes with an **example playbook** that works but is generic. The real
magic happens when someone writes a playbook **with you** — adding:

- Your family members and their roles
- Your HA entity IDs (plugs, sensors, cameras, etc.)
- Your energy tariff, solar, battery (if you have them)
- Scheduled briefings (morning summary, bedtime check, etc.)
- Connectors (Gmail, Calendar, weather for your area)

Reach out to your installer — they'll remote in, wire it up, and Alf-E will
get to know your house.
