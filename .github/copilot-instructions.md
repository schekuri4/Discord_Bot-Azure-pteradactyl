# MCBot — Copilot Instructions

## Project Overview

Discord bot for managing Azure VMs and Minecraft servers via Pterodactyl Panel. Python 3.x, discord.py 2.5.2, aiohttp, azure-sdk, mcstatus.

## Architecture

- **Entry point:** `bot.py` — `MCBot(commands.Bot)` class, loads 6 cogs
- **Config:** `config.py` — all env vars from `.env`, plan definitions, server type mappings
- **API layer:** `api/pterodactyl.py` (Client + Admin API), `api/minecraft.py` (version manifest, mod detection)
- **Cogs:** `cogs/vm.py`, `cogs/servers.py`, `cogs/mods.py`, `cogs/backups.py`, `cogs/console.py`, `cogs/setup.py`
- **Utils:** `utils/permissions.py`, `utils/embeds.py`

## Key Conventions

- All slash commands require Discord Administrator permission (`utils/permissions.py`)
- Pterodactyl Client API uses `ptlc_` keys, Application/Admin API uses `ptla_` keys
- `TYPE_CHECKING` guard for circular import prevention between cogs and `bot.py`
- Views use `timeout=900` for long-lived panels, `timeout=300` for action views, `timeout=60` for confirmations
- Server state flows through `bot.active_session` dict (shared across cogs)

## Running

```bash
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Environment

All config in `.env` — see `.env.example` for required variables. Key vars: `DISCORD_BOT_TOKEN`, `AZURE_*`, `PTERODACTYL_PANEL_URL`, `PTERODACTYL_API_KEY`, `PTERODACTYL_ADMIN_KEY`.
