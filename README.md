# Discord Bot — Azure VM + Pterodactyl

Discord bot to start/stop an Azure VM and manage Pterodactyl game servers, with timed sessions, auto-shutdown, live server status, mod/plugin management, backups, and an in-Discord console channel.

## Features

- **Timed sessions** — Choose a duration (30m / 1h / 2h / 4h / custom) when starting. The bot warns 5 minutes before expiry and auto-deallocates the VM when time is up. Extend anytime.
- **Custom timer** — Enter any duration in minutes via a modal popup.
- **Auto-shutdown** — When the last game server is stopped or killed, the bot automatically deallocates the Azure VM to save costs.
- **Live server status** — `/mc` shows online/offline state, player count, version, CPU/RAM usage, and server address.
- **Server creation** — `/create-server` provisions new Minecraft servers via the Pterodactyl Admin API (Vanilla, Paper, Forge, Fabric, Spigot).
- **Mod/plugin management** — Upload, enable/disable, and delete mods/plugins from the Discord UI. Auto-detects Forge/Fabric/Bukkit mod types and warns about incompatibilities.
- **Backups** — Create, list, restore, and delete server backups through Discord.
- **Console channel** — A temporary text channel is created per session. Messages you type are forwarded as commands to the game server.
- **Setup command** — `/setup` creates a dedicated `mc-server-stuff` channel with a welcome panel and quick-access buttons.
- **Admin-only** — All commands require Discord server Administrator permission.
- **Offline cache** — Server list is cached locally so `/mc` works even when the panel is unreachable.

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

| Command          | Description                                                                   |
| ---------------- | ----------------------------------------------------------------------------- |
| `/setup`         | Create the `mc-server-stuff` channel with welcome panel and quick buttons     |
| `/mc`            | Server panel — list all servers, view status/metrics, start/stop/restart      |
| `/create-server` | Create a new Minecraft server (version, type, plan picker)                    |
| `/upload-mod`    | Upload a mod/plugin JAR to a server (auto-detects type, checks compatibility) |
| `/backup`        | Create a named backup of a server                                             |
| `/startserver`   | Start Azure VM with a timed session (choose duration)                         |
| `/stopserver`    | Stop (deallocate) Azure VM and cancel any active timer                        |
| `/statusserver`  | Check Azure VM power state                                                    |

## `/mc` Workflow

1. Run `/mc` → see server list with live status.
2. Select a server from the dropdown → see details + action buttons + metrics.
3. Hit **Start** → pick a duration → VM boots (if off) → panel comes up → game server starts → console channel is created.
4. Hit **Stop** or **Kill** → game server stops → if no other servers are running, VM auto-deallocates.
5. Hit **Mods/Plugins** → view, enable/disable, or delete mods. Use `/upload-mod` to add new ones.
6. Hit **Backups** → create, restore, or delete backups.
7. Hit **Back** to return to the server list. **Refresh** to update status.

## Project Structure

```
bot.py              — Entry point (MCBot class, loads cogs)
config.py           — Environment variables and constants
api/
  pterodactyl.py    — Client API (ptlc_) + Admin API (ptla_)
  minecraft.py      — Version manifest, mod type detection
cogs/
  vm.py             — Azure VM management and timed sessions
  servers.py        — Server listing, creation, dashboard, power controls
  mods.py           — Mod/plugin upload, enable/disable, compatibility
  backups.py        — Backup creation, restore, deletion
  console.py        — Console channel message forwarding
  setup.py          — Channel setup command
utils/
  permissions.py    — Permission checks
  embeds.py         — Reusable embed builders
```

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
