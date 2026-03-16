"""Backup creation, listing, restore, and deletion."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from api.pterodactyl import PterodactylApiError
from utils.embeds import backup_embed
from utils.permissions import check_auth, is_authorized

if TYPE_CHECKING:
    from bot import MCBot
    from cogs.vm import VMCog


class BackupsCog(commands.Cog):
    def __init__(self, bot: MCBot) -> None:
        self.bot = bot

    async def show_backups_panel(
        self,
        interaction: discord.Interaction,
        server: dict[str, Any],
        all_servers: list[dict[str, Any]] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        identifier = server["identifier"]
        try:
            backups = await self.bot.ptero_client().list_backups(identifier)
        except PterodactylApiError as exc:
            await interaction.edit_original_response(
                embed=discord.Embed(title="\u274c  Failed to list backups", description=str(exc), color=discord.Color.red()),
            )
            return
        except Exception:
            backups = []

        embed = backup_embed(backups, server["name"])
        view = BackupsPanelView(self.bot, server, backups, all_servers or [])
        await interaction.edit_original_response(embed=embed, view=view)

    @app_commands.command(name="backup", description="Create a backup of a server")
    @app_commands.describe(server_id="Server identifier", name="Backup name (optional)")
    async def backup_cmd(self, interaction: discord.Interaction, server_id: str, name: str | None = None) -> None:
        if not await check_auth(interaction):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            result = await self.bot.ptero_client().create_backup(server_id, name)
            backup_name = result.get("name", "Backup")
            backup_uuid = result.get("uuid", "?")[:8]
            await interaction.followup.send(
                embed=discord.Embed(
                    title="\u2705  Backup Started",
                    description=f"**{backup_name}** (`{backup_uuid}`)\nBackup is being created in the background.",
                    color=discord.Color.green(),
                ),
                ephemeral=True,
            )
        except PterodactylApiError as exc:
            await interaction.followup.send(f"\u274c Backup failed: {exc}", ephemeral=True)


# ── Backup panel views ───────────────────────────────────────────────


class BackupsPanelView(discord.ui.View):
    def __init__(self, bot: "MCBot", server: dict[str, Any], backups: list[dict[str, Any]],
                 all_servers: list[dict[str, Any]]) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.server = server
        self.backups = backups
        self.all_servers = all_servers

        if backups:
            options = [
                discord.SelectOption(
                    label=(b.get("name") or "Unnamed")[:100],
                    value=str(b.get("uuid", ""))[:100],
                    description=f"{(b.get('bytes', 0) or 0) / 1_048_576:.1f} MB — {str(b.get('created_at', ''))[:10]}"[:100],
                )
                for b in backups[:25]
                if b.get("uuid")
            ]
            if options:
                self.backup_select = discord.ui.Select(
                    placeholder="Select a backup to manage...",
                    options=options,
                    row=0,
                )
                self.backup_select.callback = self._backup_callback
                self.add_item(self.backup_select)

        self.add_item(CreateBackupButton(bot, server))
        self.add_item(RefreshBackupsButton(bot, server, all_servers))
        self.add_item(BackupsBackButton(bot, server, all_servers))

    async def _backup_callback(self, interaction: discord.Interaction) -> None:
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        backup_uuid = self.backup_select.values[0]
        backup = next((b for b in self.backups if b.get("uuid") == backup_uuid), None)
        if not backup:
            await interaction.response.send_message("Backup not found.", ephemeral=True)
            return

        name = backup.get("name") or "Unnamed"
        size_mb = (backup.get("bytes", 0) or 0) / 1_048_576
        created = str(backup.get("created_at", ""))[:19]
        successful = backup.get("is_successful", False)

        embed = discord.Embed(
            title=f"\U0001f4be  {name}",
            description=f"Server: **{self.server['name']}**",
            color=discord.Color.blue(),
        )
        embed.add_field(name="UUID", value=f"`{backup_uuid[:8]}`", inline=True)
        embed.add_field(name="Size", value=f"{size_mb:.1f} MB", inline=True)
        embed.add_field(name="Status", value="\u2705 Complete" if successful else "\u23f3 Pending", inline=True)
        embed.add_field(name="Created", value=created, inline=True)

        view = BackupActionView(self.bot, self.server, backup, self.backups, self.all_servers)
        await interaction.response.edit_message(embed=embed, view=view)


class BackupActionView(discord.ui.View):
    def __init__(self, bot: "MCBot", server: dict[str, Any], backup: dict[str, Any],
                 all_backups: list[dict[str, Any]], all_servers: list[dict[str, Any]]) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.server = server
        self.backup = backup
        self.all_backups = all_backups
        self.all_servers = all_servers

    @discord.ui.button(label="\U0001f504 Restore", style=discord.ButtonStyle.blurple, row=0)
    async def restore_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        view = ConfirmRestoreView(self.bot, self.server, self.backup, self.all_servers)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="\u26a0\ufe0f  Confirm Restore",
                description=f"Restore **{self.backup.get('name', 'Unnamed')}** to **{self.server['name']}**?\n\n"
                            "This will overwrite current server files.",
                color=discord.Color.orange(),
            ),
            view=view,
        )

    @discord.ui.button(label="\U0001f5d1 Delete", style=discord.ButtonStyle.danger, row=0)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.ptero_client().delete_backup(self.server["identifier"], self.backup["uuid"])
            nav = discord.ui.View(timeout=900)
            nav.add_item(BackupsBackButton(self.bot, self.server, self.all_servers))
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="\u2705  Backup Deleted",
                    description=f"**{self.backup.get('name', 'Unnamed')}** has been removed.",
                    color=discord.Color.green(),
                ),
                view=nav,
            )
        except PterodactylApiError as exc:
            await interaction.edit_original_response(
                embed=discord.Embed(title="\u274c  Delete failed", description=str(exc), color=discord.Color.red()),
            )

    @discord.ui.button(label="\u25c0 Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        backups_cog = self.bot.get_cog("BackupsCog")
        if backups_cog:
            await backups_cog.show_backups_panel(interaction, self.server, self.all_servers)


class ConfirmRestoreView(discord.ui.View):
    def __init__(self, bot: "MCBot", server: dict[str, Any], backup: dict[str, Any],
                 all_servers: list[dict[str, Any]]) -> None:
        super().__init__(timeout=60)
        self.bot = bot
        self.server = server
        self.backup = backup
        self.all_servers = all_servers

    @discord.ui.button(label="Yes, restore", style=discord.ButtonStyle.danger, row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.ptero_client().restore_backup(self.server["identifier"], self.backup["uuid"])
            nav = discord.ui.View(timeout=900)
            nav.add_item(BackupsBackButton(self.bot, self.server, self.all_servers))
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="\u2705  Restore Started",
                    description=f"**{self.backup.get('name', 'Unnamed')}** is being restored to **{self.server['name']}**.\n"
                                "The server may restart during this process.",
                    color=discord.Color.green(),
                ),
                view=nav,
            )
        except PterodactylApiError as exc:
            await interaction.edit_original_response(
                embed=discord.Embed(title="\u274c  Restore failed", description=str(exc), color=discord.Color.red()),
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        backups_cog = self.bot.get_cog("BackupsCog")
        if backups_cog:
            await backups_cog.show_backups_panel(interaction, self.server, self.all_servers)


class CreateBackupButton(discord.ui.Button):
    def __init__(self, bot: "MCBot", server: dict[str, Any]) -> None:
        super().__init__(style=discord.ButtonStyle.green, label="\u2795 Create Backup", row=1)
        self.bot = bot
        self.server = server

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        modal = BackupNameModal(self.bot, self.server)
        await interaction.response.send_modal(modal)


class BackupNameModal(discord.ui.Modal, title="Create Backup"):
    backup_name = discord.ui.TextInput(
        label="Backup name (optional)", placeholder="e.g. Before mod update",
        max_length=100, required=False,
    )

    def __init__(self, bot: "MCBot", server: dict[str, Any]) -> None:
        super().__init__()
        self.bot = bot
        self.server = server

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.backup_name.value.strip() if self.backup_name.value else None
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.bot.ptero_client().create_backup(self.server["identifier"], name)
            backup_name = result.get("name", "Backup")
            await interaction.followup.send(
                embed=discord.Embed(
                    title="\u2705  Backup Started",
                    description=f"**{backup_name}** is being created in the background.",
                    color=discord.Color.green(),
                ),
                ephemeral=True,
            )
        except PterodactylApiError as exc:
            await interaction.followup.send(f"\u274c Backup failed: {exc}", ephemeral=True)


class RefreshBackupsButton(discord.ui.Button):
    def __init__(self, bot: "MCBot", server: dict[str, Any], all_servers: list[dict[str, Any]]) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="\U0001f504 Refresh", row=1)
        self.bot = bot
        self.server = server
        self.all_servers = all_servers

    async def callback(self, interaction: discord.Interaction) -> None:
        backups_cog = self.bot.get_cog("BackupsCog")
        if backups_cog:
            await backups_cog.show_backups_panel(interaction, self.server, self.all_servers)


class BackupsBackButton(discord.ui.Button):
    def __init__(self, bot: "MCBot", server: dict[str, Any], all_servers: list[dict[str, Any]]) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="\u25c0 Back to server", row=2)
        self.bot = bot
        self.server = server
        self.all_servers = all_servers

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            resources = await self.bot.ptero_client().get_server_resources(self.server["identifier"])
        except Exception:
            resources = {}
        state = str(resources.get("current_state", "unknown"))
        vm_cog = self.bot.get_cog("VMCog")
        session_str = vm_cog.session_remaining_str() if vm_cog else None
        from utils.embeds import server_embed
        embed = server_embed(self.server, state, session_str)
        from cogs.servers import ServerActionView
        view = ServerActionView(self.bot, self.server, state, self.all_servers)
        await interaction.edit_original_response(embed=embed, view=view)


async def setup(bot: "MCBot") -> None:
    await bot.add_cog(BackupsCog(bot))
