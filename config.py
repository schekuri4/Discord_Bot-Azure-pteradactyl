import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# Discord
DISCORD_BOT_TOKEN = _require_env("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")  # Optional: for instant slash command sync

# Azure
AZURE_TENANT_ID = _require_env("AZURE_TENANT_ID")
AZURE_CLIENT_ID = _require_env("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = _require_env("AZURE_CLIENT_SECRET")
AZURE_SUBSCRIPTION_ID = _require_env("AZURE_SUBSCRIPTION_ID")
AZURE_RESOURCE_GROUP = _require_env("AZURE_RESOURCE_GROUP")
AZURE_VM_NAME = _require_env("AZURE_VM_NAME")

# Pterodactyl
PTERODACTYL_PANEL_URL = os.getenv("PTERODACTYL_PANEL_URL")
PTERODACTYL_API_KEY = os.getenv("PTERODACTYL_API_KEY")  # Client key (ptlc_)
PTERODACTYL_ADMIN_KEY = os.getenv("PTERODACTYL_ADMIN_KEY")  # Application key (ptla_)

# Paths
BASE_DIR = Path(__file__).parent
SERVER_CACHE_FILE = BASE_DIR / "server_cache.json"

# Server creation defaults
DEFAULT_MEMORY_MB = 2048
DEFAULT_DISK_MB = 10240
DEFAULT_CPU_PERCENT = 100
DEFAULT_BACKUPS = 3
DEFAULT_DATABASES = 1

# Plan definitions
PLANS = {
    "basic": {"memory": 2048, "cpu": 100, "disk": 10240, "label": "Basic (2 GB RAM, 1 core, 10 GB disk)"},
    "standard": {"memory": 4096, "cpu": 200, "disk": 20480, "label": "Standard (4 GB RAM, 2 cores, 20 GB disk)"},
    "premium": {"memory": 8192, "cpu": 400, "disk": 51200, "label": "Premium (8 GB RAM, 4 cores, 50 GB disk)"},
}

# Server types → Pterodactyl egg names (mapped during setup)
SERVER_TYPES = {
    "vanilla": "Vanilla Minecraft",
    "paper": "Paper",
    "forge": "Forge Minecraft",
    "fabric": "Fabric Minecraft",
    "spigot": "Spigot",
    "bungeecord": "Bungeecord",
}
