# Discord Bot — Azure VM + Pterodactyl

Discord bot to start/stop an Azure VM and manage Pterodactyl game servers.

## Features

- **Timed sessions** — When starting the VM, choose a duration (30m / 1h / 2h / 4h). The bot warns you 5 minutes before expiry and auto-deallocates the VM when time is up. You can extend while the session is active.
- **Admin-only** — All commands require Discord server Administrator permission.
- **Pterodactyl panel** — Rich `/mc` UI with dropdown + start/stop/restart/refresh buttons.
- **Offline cache** — Server list is saved locally so the bot works even when the Pterodactyl panel is unreachable.

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

| Command         | Description                                                          |
| --------------- | -------------------------------------------------------------------- |
| `/startserver`  | Start Azure VM with a timed session (choose duration)                |
| `/stopserver`   | Stop Azure VM (cancels any active timer)                             |
| `/statusserver` | Check Azure VM status                                                |
| `/mc`           | Server panel — dropdown to pick a server, start/stop/refresh buttons |

## Environment Variables

See `.env.example` for all required values.

## Security Notes

- Keep `.env` private and never commit it.
- Rotate secrets if they are ever exposed.
- All commands are restricted to Discord server Administrators.
