# Discord Bot — Azure VM + Pterodactyl

Discord bot to start/stop an Azure VM and manage Pterodactyl game servers, with timed sessions, auto-shutdown, live server status, and an in-Discord console channel.

## Features

- **Timed sessions** — Choose a duration (30m / 1h / 2h / 4h / custom) when starting. The bot warns 5 minutes before expiry and auto-deallocates the VM when time is up. Extend anytime.
- **Custom timer** — Enter any duration in minutes via a modal popup.
- **Auto-shutdown** — When the last game server is stopped or killed, the bot automatically deallocates the Azure VM to save costs.
- **Live server status** — `/mc` shows online/offline state, player count, Minecraft version, and server address for each server.
- **Console channel** — A temporary text channel is created per session. Messages you type are forwarded as commands to the game server via the Pterodactyl API.
- **Admin-only** — All commands require Discord server Administrator permission.
- **Offline cache** — Server list is cached locally so `/mc` works even when the panel is unreachable (shows cached data with a warning).

## Setup

```bash
git clone https://github.com/schekuri4/Discord_Bot-Azure-pteradactyl.git
cd Discord_Bot-Azure-pteradactyl
python3 -m venv .venv
source .venv/bin/activate   # Linux/Mac
# .\.venv\Scripts\Activate.ps1  # Windows
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your values
python bot.py
```

## Commands

| Command         | Description                                                                  |
| --------------- | ---------------------------------------------------------------------------- |
| `/startserver`  | Start Azure VM with a timed session (choose duration)                        |
| `/stopserver`   | Stop (deallocate) Azure VM and cancel any active timer                       |
| `/statusserver` | Check Azure VM power state                                                   |
| `/mc`           | Server panel — shows all servers with status, player count, version; dropdown to select a server for start/stop/restart/kill/refresh controls |

## `/mc` Workflow

1. Run `/mc` → see server list with live status.
2. Select a server from the dropdown → see details + action buttons.
3. Hit **Start** → pick a duration → VM boots (if off) → panel comes up → game server starts → console channel is created.
4. Hit **Stop** or **Kill** → game server stops → if no other servers are running, VM auto-deallocates.
5. Hit **Back** to return to the server list. **Refresh** to update status.

## Dependencies

- `discord.py` — Discord API
- `azure-identity` / `azure-mgmt-compute` — Azure VM control
- `aiohttp` — Pterodactyl API calls
- `mcstatus` — Minecraft server status queries
- `python-dotenv` — Environment variable loading

## Environment Variables

See `.env.example` for all required values.

## Deployment (VPS)

```bash
cd ~/Discord_Bot-Azure-pteradactyl
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
screen -X -S bot quit 2>/dev/null
screen -dmS bot bash -c 'cd ~/Discord_Bot-Azure-pteradactyl && source .venv/bin/activate && python3 bot.py'
```

## Security Notes

- Keep `.env` private and never commit it.
- Rotate secrets if they are ever exposed.
- All commands are restricted to Discord server Administrators.
- The bot uses the Pterodactyl **Client API** (`ptlc_` key) — not the Application API.
