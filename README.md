# Discord Azure + Pterodactyl Controller Bot

Python Discord bot with slash commands to control:

- Azure VM: `/startserver`, `/stopserver`, `/statusserver`
- Pterodactyl servers: `/mcservers`, `/startmcserver`

## 1) Prerequisites

- Python 3.10+
- Azure VM
- Azure App Registration with RBAC permission on the VM or resource group
- Discord bot token

## 2) Azure Permission Setup

Assign the app registration to your VM scope (or resource group scope):

- Role: `Virtual Machine Contributor`
- Scope: VM or resource group that contains the VM

## 3) Configure Environment

1. Copy `.env.example` to `.env`
2. Fill these values:
   - `DISCORD_BOT_TOKEN`
   - `AZURE_CLIENT_SECRET`
   - `AZURE_SUBSCRIPTION_ID`
   - `AZURE_RESOURCE_GROUP`
   - `AZURE_VM_NAME`
   - `PTERODACTYL_PANEL_URL`
   - `PTERODACTYL_API_KEY`

`AZURE_TENANT_ID` and `AZURE_CLIENT_ID` are prefilled from your provided app registration.

For Pterodactyl:

- `PTERODACTYL_PANEL_URL` example: `https://panel.yourdomain.com`
- `PTERODACTYL_API_KEY` should be a key with permission to list servers.
- Starting via `/startmcserver` uses `/api/client/servers/{identifier}/power` and often requires a `ptlc_` key.

## 4) Install And Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py
```

## 5) Discord Slash Commands

Once the bot is online, use in your server:

- `/statusserver`
- `/startserver`
- `/stopserver`
- `/mcservers`
- `/startmcserver`

## Security Notes

- Keep `.env` private and never commit it.
- Rotate secrets if they are ever exposed.
- Optional: set `DISCORD_ADMIN_USER_ID` to restrict command use to your Discord account.
