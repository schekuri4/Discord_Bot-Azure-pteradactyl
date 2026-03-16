# Discord Bot — Azure VM + Pterodactyl

Discord bot to start/stop an Azure VM and manage Pterodactyl game servers.

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

| Command          | Description                                              |
| ---------------- | -------------------------------------------------------- |
| `/startserver`   | Start Azure VM                                           |
| `/stopserver`    | Stop Azure VM                                            |
| `/statusserver`  | Check Azure VM status                                    |
| `/mc`            | Server panel — dropdown to pick a server, start/stop/refresh buttons |

## Environment Variables

See `.env.example` for all required values.

## Security Notes

- Keep `.env` private and never commit it.
- Rotate secrets if they are ever exposed.
- Optional: set `DISCORD_ADMIN_USER_ID` to restrict command use to your Discord account.
