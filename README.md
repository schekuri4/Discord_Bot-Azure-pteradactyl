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

## Prerequisites

- **Python 3.10+**
- **Azure account** with a VM and an App Registration (Service Principal)
- **Pterodactyl Panel** installed on the Azure VM (or any server)
- **Discord Bot** created at [discord.com/developers](https://discord.com/developers/applications)

## Setup

### 1. Clone and install

```bash
git clone https://github.com/schekuri4/Discord_Bot-Azure-pteradactyl.git
cd Discord_Bot-Azure-pteradactyl
python3 -m venv .venv
source .venv/bin/activate   # Linux/Mac
# .\.venv\Scripts\Activate.ps1  # Windows
pip install -r requirements.txt
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and fill in all values:

```env
# Discord
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_GUILD_ID=your_guild_id_here          # Right-click server → Copy Server ID

# Azure App Registration
AZURE_TENANT_ID=your_tenant_id
AZURE_CLIENT_ID=your_client_id
AZURE_CLIENT_SECRET=your_client_secret
AZURE_SUBSCRIPTION_ID=your_subscription_id

# Target VM
AZURE_RESOURCE_GROUP=your_resource_group
AZURE_VM_NAME=your_vm_name

# Pterodactyl Panel
PTERODACTYL_PANEL_URL=http://your-panel-ip/
PTERODACTYL_API_KEY=ptlc_your_client_api_key
PTERODACTYL_ADMIN_KEY=ptla_your_admin_api_key
```

### 3. Get your API keys

| Key | Where to find it |
|-----|-----------------|
| `DISCORD_BOT_TOKEN` | [Discord Developer Portal](https://discord.com/developers/applications) → Bot → Token |
| `DISCORD_GUILD_ID` | Discord → right-click your server name → Copy Server ID (enable Developer Mode in Settings → Advanced) |
| `AZURE_*` | Azure Portal → App Registrations → your app → Overview (Tenant/Client ID) and Certificates & Secrets (Client Secret) |
| `AZURE_SUBSCRIPTION_ID` | Azure Portal → Subscriptions |
| `AZURE_RESOURCE_GROUP` / `AZURE_VM_NAME` | Azure Portal → Virtual Machines → your VM |
| `PTERODACTYL_API_KEY` | Pterodactyl Panel → Account → API Credentials → Create (gives a `ptlc_` key) |
| `PTERODACTYL_ADMIN_KEY` | Pterodactyl Panel → Application API → Create API Key (gives a `ptla_` key) — needed for `/create-server` |

### 4. Run the bot

```bash
python bot.py
```

## Commands

| Command | Description |
|---------|-------------|
| `/setup` | Create the `mc-server-stuff` channel with welcome panel and quick buttons |
| `/mc` | Server panel — list all servers, view status/metrics, start/stop/restart |
| `/create-server` | Create a new Minecraft server (type, version, plan picker) |
| `/upload-mod` | Upload a mod/plugin JAR to a server (auto-detects type, checks compatibility) |
| `/backup` | Create a named backup of a server |
| `/startserver` | Start Azure VM with a timed session |
| `/stopserver` | Stop (deallocate) Azure VM and cancel any active timer |
| `/statusserver` | Check Azure VM power state |

## Command Details

### `/setup`
Creates a dedicated `mc-server-stuff` channel in your Discord server with:
- A welcome embed explaining all available commands
- A **Open Server Panel** button (runs `/mc`)
- A **Create Server** button (runs `/create-server`)

Run this once to set up your server. The channel is read-only for non-admins.

### `/mc` — Server Panel
The main command. Opens an interactive panel showing all your Minecraft servers with:
- **Live status** (online/offline/starting/stopping)
- **Player count** and **Minecraft version** for running servers
- **CPU, RAM, disk usage** and uptime metrics
- **Server address** (`ip:port`)

Select a server from the dropdown to see:
- **Start** — Pick a session duration → boots Azure VM → starts game server → creates console channel
- **Stop** / **Kill** — Stops the game server. If no servers are running, auto-deallocates the VM.
- **Restart** — Restarts the running server
- **Refresh** — Update status and metrics
- **Mods/Plugins** — Open the mod management panel
- **Backups** — Open the backup management panel

### `/create-server`
Provisions a new Minecraft server through a step-by-step wizard:

1. **Choose server type** — Vanilla, Paper, Spigot, Forge, Fabric, or BungeeCord
2. **Choose Minecraft version** — Popular versions shown first, or pick from all releases
3. **Choose a plan** — Basic (2 GB), Standard (4 GB), or Premium (8 GB)
4. **Enter a name** — Server name and optional description

The server is created on your Pterodactyl panel and immediately available in `/mc`.

**Requires:** `PTERODACTYL_ADMIN_KEY` (`ptla_` key) in your `.env`.

### `/upload-mod`
Upload a mod or plugin JAR file to a server:
- **Auto-detects mod type** by inspecting the JAR: Fabric (`fabric.mod.json`), Forge (`mods.toml` / `mcmod.info`), Bukkit/Spigot/Paper (`plugin.yml`), BungeeCord (`bungee.yml`)
- **Checks compatibility** — warns if you upload a Forge mod to a Paper server, etc.
- **Auto-selects directory** — uploads to `/mods` or `/plugins` based on detected type
- **50 MB max** file size

Parameters:
- `server_id` — The server identifier (shown in `/mc`)
- `file` — Drag and drop your `.jar` file
- `directory` — (Optional) Override target folder

### `/backup`
Quick backup creation:
- `server_id` — The server identifier
- `name` — (Optional) Name for the backup (e.g., "Before mod update")

For full backup management (list, restore, delete), use the **Backups** button in `/mc`.

### Mods/Plugins Panel (via `/mc` → Mods/Plugins button)
- **List** all mods/plugins with status (enabled/disabled), size, and last modified date
- **Enable/Disable** — Toggle a mod by renaming `.jar` ↔ `.jar.disabled`. Requires server restart.
- **Delete** — Remove a mod/plugin with confirmation prompt
- **Upload instructions** — Guides you to use `/upload-mod`

### Backups Panel (via `/mc` → Backups button)
- **List** all backups with name, size, date, and completion status
- **Create** — Name your backup and start it in the background
- **Restore** — Restore a backup to the server (with confirmation)
- **Delete** — Remove a backup permanently

### Timed Sessions
When starting a server, you choose a duration:
- **30 minutes / 1 hour / 2 hours / 4 hours** — Quick picks
- **Custom** — Enter any duration in minutes

The bot:
1. Warns you **5 minutes** before the session expires
2. Offers an **Extend** button to add more time
3. **Auto-deallocates** the Azure VM when time runs out (saves money)

### Console Channel
When a server starts, a temporary `#console-servername` channel is created:
- Everything you type is sent as a command to the game server
- Commands get a ✅ reaction on success
- Channel is auto-deleted when the session ends

## Server Types

| Type | Description | Supports |
|------|-------------|----------|
| **Vanilla** | Official Mojang server | No mods or plugins |
| **Paper** | High-performance Bukkit/Spigot fork | Plugins (Bukkit API) |
| **Spigot** | Modified Bukkit server | Plugins (Bukkit API) |
| **Forge** | Mod loader for Java Edition | Forge mods |
| **Fabric** | Lightweight mod loader | Fabric mods |
| **BungeeCord** | Proxy for linking multiple servers | BungeeCord plugins |

## Mod Compatibility

The bot auto-detects mod types and warns about mismatches:

| Mod Type | Compatible With |
|----------|----------------|
| Fabric mods | Fabric servers only |
| Forge mods | Forge servers only |
| Bukkit plugins | Paper, Spigot servers |
| BungeeCord plugins | BungeeCord servers only |

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
- The bot uses both Pterodactyl **Client API** (`ptlc_` key) and **Application API** (`ptla_` key).
