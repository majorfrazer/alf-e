# Alf-E — Scholz Brotherhood Edition

Your household AI agent. Runs on your mac mini alongside Home Assistant.

---

## What Alf-E does

- Reads everything in your HA — sensors, entities, states
- Builds dashboards, automations, scripts for you
- Drafts code for new HA integrations
- Answers questions about your house in plain English
- Checks your email, weather, calendar (once connected)

---

## Install (5 steps)

### 1. Install Docker Desktop

Download from https://www.docker.com/products/docker-desktop and open it once so it's running.

### 2. Clone this repo

Open Terminal and paste:

```bash
git clone https://github.com/majorfrazer/alf-e-brotherhood.git ~/alf-e
cd ~/alf-e
```

### 3. Get your three keys

You need three things in your `.env` file:

**(a) Anthropic API key** — go to https://console.anthropic.com, sign in, create an API key. Add $20 of credit (lasts months).

**(b) Home Assistant token** — in HA: click your user icon (bottom-left) → Security tab → scroll to "Long-Lived Access Tokens" → Create Token. Copy the whole thing.

**(c) A PWA login token** — just any long random string. Generate one by running:

```bash
openssl rand -base64 32
```

Copy that output.

### 4. Create your .env file

```bash
cp .env.example .env
nano .env
```

Paste the three values in. Save with `Ctrl+O`, `Enter`, `Ctrl+X`.

### 5. Start Alf-E

```bash
docker compose up -d
```

Wait 30 seconds, then open **http://localhost:8099** in your browser. Enter your PWA login token (the one from step 3c) and you're in.

---

## Add to HA sidebar

Want Alf-E to appear inside your HA sidebar? On your mac mini:

1. Find your HA config folder (usually `~/homeassistant/config/` or wherever your HA is mounted)
2. Create a file called `alfe_dashboard.yaml` in there with:

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

Replace `YOUR_ALFE_API_TOKEN_HERE` with your `ALFE_API_TOKEN` from `.env`.

3. Add to `configuration.yaml`:

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

4. Restart HA. Click **Alf-E** in your sidebar.

---

## Update Alf-E

```bash
cd ~/alf-e
docker compose pull
docker compose up -d
```

That pulls the latest version from Fraser's image registry.

---

## Troubleshooting

**"Connection error" when opening http://localhost:8099**
Wait another 30 seconds and refresh — first startup can be slow.

**Can't connect to HA**
Check your `HA_URL` in `.env`. If HA runs on a different machine, change it from `host.docker.internal` to that machine's IP.

**"Invalid token"**
Double-check the `ALFE_API_TOKEN` in `.env` matches what you're typing into the login screen. Don't include quotes or spaces.

**Something else**
Check the logs: `docker compose logs -f alf-e`

---

## Credits

Built by Fraser Scholz for the Scholz Brotherhood.
Fraser / Harley / Matt — this one's yours.
