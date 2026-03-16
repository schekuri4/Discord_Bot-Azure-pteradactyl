"""MCBot - Discord bot for managing Azure VMs and Minecraft servers via Pterodactyl."""

from __future__ import annotations

from typing import Any

import discord
from discord.ext import commands
from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient

from api.pterodactyl import PterodactylClient, PterodactylAdmin
from config import (
    DISCORD_BOT_TOKEN,
    DISCORD_GUILD_ID,
    AZURE_TENANT_ID,
    AZURE_CLIENT_ID,
    AZURE_CLIENT_SECRET,
    AZURE_SUBSCRIPTION_ID,
    PTERODACTYL_PANEL_URL,
    PTERODACTYL_API_KEY,
    PTERODACTYL_ADMIN_KEY,
)

COGS = [
    "cogs.vm",
    "cogs.servers",
    "cogs.mods",
    "cogs.backups",
    "cogs.console",
    "cogs.setup",
]


class MCBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

        self.active_session: dict[str, Any] | None = None

        # Azure
        credential = ClientSecretCredential(
            tenant_id=AZURE_TENANT_ID,
            client_id=AZURE_CLIENT_ID,
            client_secret=AZURE_CLIENT_SECRET,
        )
        self.compute_client = ComputeManagementClient(credential, AZURE_SUBSCRIPTION_ID)

    def ptero_client(self) -> PterodactylClient:
        if not PTERODACTYL_PANEL_URL or not PTERODACTYL_API_KEY:
            raise RuntimeError("Missing PTERODACTYL_PANEL_URL or PTERODACTYL_API_KEY in .env")
        return PterodactylClient(PTERODACTYL_PANEL_URL, PTERODACTYL_API_KEY)

    def ptero_admin(self) -> PterodactylAdmin:
        if not PTERODACTYL_PANEL_URL or not PTERODACTYL_ADMIN_KEY:
            raise RuntimeError("Missing PTERODACTYL_PANEL_URL or PTERODACTYL_ADMIN_KEY in .env")
        return PterodactylAdmin(PTERODACTYL_PANEL_URL, PTERODACTYL_ADMIN_KEY)

    async def setup_hook(self) -> None:
        for cog in COGS:
            await self.load_extension(cog)
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"Loaded {len(COGS)} cogs and synced slash commands to guild {DISCORD_GUILD_ID}.")
        else:
            await self.tree.sync()
            print(f"Loaded {len(COGS)} cogs and synced slash commands globally.")

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} and ready.")


if __name__ == "__main__":
    MCBot().run(DISCORD_BOT_TOKEN)

