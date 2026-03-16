"""Mod and plugin upload, listing, enable/disable, and compatibility checking."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from api.minecraft import detect_mod_type, check_compatibility, MOD_TYPE_UNKNOWN
from api.pterodactyl import PterodactylApiError
from utils.embeds import mod_list_embed
from utils.permissions import check_auth, is_authorized

if TYPE_CHECKING:
    from bot import MCBot
    from cogs.vm import VMCog

# Directories where mods/plugins live by server type
MOD_DIRECTORIES = {
    "forge": "/mods",
    "fabric": "/mods",
    "paper": "/plugins",
    "spigot": "/plugins",
    "bukkit": "/plugins",
    "vanilla": "/mods",  # won't have any but allows browsing
}

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB


class ModsCog(commands.Cog):
    def __init__(self, bot: MCBot) -> None:
        self.bot = bot

    def _guess_directory(self, server: dict[str, Any]) -> str:
        """Guess the mods/plugins directory for a server."""
        name = server.get("name", "").lower()
        desc = server.get("description", "").lower()
        combined = f"{name} {desc}"
        for key, directory in MOD_DIRECTORIES.items():
            if key in combined:
                return directory
        return "/mods"

    async def show_mods_panel(
        self,
        interaction: discord.Interaction,
        server: dict[str, Any],
        all_servers: list[dict[str, Any]] | None = None,
    ) -> None:
        """Show the mods/plugins panel for a server."""
        await interaction.response.defer(ephemeral=True)

        identifier = server["identifier"]
        directory = self._guess_directory(server)

        try:
            files = await self.bot.ptero_client().list_files(identifier, directory)
            # Filter to jar files and .disabled files
            jar_files = [
                f for f in files
                if str(f.get("name", "")).endswith((".jar", ".jar.disabled", ".disabled"))
            ]
        except PterodactylApiError:
            # Directory might not exist — try the other one
            alt_dir = "/plugins" if directory == "/mods" else "/mods"
            try:
                files = await self.bot.ptero_client().list_files(identifier, alt_dir)
                jar_files = [
                    f for f in files
                    if str(f.get("name", "")).endswith((".jar", ".jar.disabled", ".disabled"))
                ]
                directory = alt_dir
            except Exception:
                jar_files = []
        except Exception:
            jar_files = []

        embed = mod_list_embed(jar_files, server["name"], directory)
        view = ModsPanelView(self.bot, server, jar_files, directory, all_servers or [])
        await interaction.edit_original_response(embed=embed, view=view)

    @app_commands.command(name="upload-mod", description="Upload a mod/plugin JAR to a server")
    @app_commands.describe(
        server_id="The server identifier (from /mc)",
        file="The .jar file to upload",
        directory="Target directory (default: auto-detect)",
    )
    async def upload_mod(
        self,
        interaction: discord.Interaction,
        server_id: str,
        file: discord.Attachment,
        directory: str | None = None,
    ) -> None:
        if not await check_auth(interaction):
            return

        if not file.filename.endswith(".jar"):
            await interaction.response.send_message("\u274c Only `.jar` files can be uploaded.", ephemeral=True)
            return

        if file.size > MAX_UPLOAD_SIZE:
            await interaction.response.send_message(f"\u274c File too large. Max size is {MAX_UPLOAD_SIZE // 1_048_576} MB.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        # Download the file
        try:
            file_data = await file.read()
        except Exception as exc:
            await interaction.followup.send(f"\u274c Failed to download attachment: {exc}", ephemeral=True)
            return

        # Detect mod type
        mod_type, metadata = detect_mod_type(file_data)

        # Check compatibility
        compat_msg = ""
        if mod_type != MOD_TYPE_UNKNOWN:
            # Try to determine server type from server list
            try:
                servers = await self.bot.ptero_client().list_servers()
                target = next((s for s in servers if s["identifier"] == server_id), None)
                if target:
                    server_type_guess = self._guess_server_type(target)
                    is_compat, msg = check_compatibility(mod_type, server_type_guess)
                    compat_msg = f"\n{msg}"
            except Exception:
                pass

        target_dir = directory or ("/plugins" if mod_type == "bukkit" else "/mods")

        try:
            await self.bot.ptero_client().upload_file(server_id, target_dir, file.filename, file_data)
        except PterodactylApiError as exc:
            await interaction.followup.send(f"\u274c Upload failed: {exc}", ephemeral=True)
            return

        mod_name = metadata.get("name") or file.filename
        mod_ver = metadata.get("version", "")
        ver_str = f" v{mod_ver}" if mod_ver else ""

        embed = discord.Embed(
            title="\u2705  Upload Successful",
            description=(
                f"**{mod_name}**{ver_str}\n"
                f"Type: `{mod_type}`\n"
                f"Uploaded to: `{target_dir}/{file.filename}`\n"
                f"Size: {file.size / 1024:.1f} KB"
                f"{compat_msg}"
            ),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    def _guess_server_type(self, server: dict[str, Any]) -> str:
        name = server.get("name", "").lower()
        desc = server.get("description", "").lower()
        combined = f"{name} {desc}"
        for stype in ["forge", "fabric", "paper", "spigot", "vanilla"]:
            if stype in combined:
                return stype
        return "unknown"


# ── Mods panel views ─────────────────────────────────────────────────


class ModsPanelView(discord.ui.View):
    def __init__(self, bot: "MCBot", server: dict[str, Any], files: list[dict[str, Any]],
                 directory: str, all_servers: list[dict[str, Any]]) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.server = server
        self.files = files
        self.directory = directory
        self.all_servers = all_servers

        if files:
            options = [
                discord.SelectOption(
                    label=f["name"][:100],
                    value=f["name"][:100],
                    description=f"{(f.get('size', 0) or 0) / 1024:.0f} KB"[:100],
                )
                for f in files[:25]
            ]
            self.file_select = discord.ui.Select(
                placeholder="Select a mod/plugin to manage...",
                options=options,
                row=0,
            )
            self.file_select.callback = self._file_callback
            self.add_item(self.file_select)

        self.add_item(UploadInstructionButton())
        self.add_item(RefreshModsButton(bot, server, directory, all_servers))
        self.add_item(ModsBackButton(bot, server, all_servers))

    async def _file_callback(self, interaction: discord.Interaction) -> None:
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        filename = self.file_select.values[0]
        file_info = next((f for f in self.files if f.get("name") == filename), None)
        if not file_info:
            await interaction.response.send_message("File not found.", ephemeral=True)
            return

        is_disabled = filename.endswith(".disabled")
        embed = discord.Embed(
            title=f"\U0001f9e9  {filename}",
            description=f"Server: **{self.server['name']}**\nDirectory: `{self.directory}`",
            color=discord.Color.purple(),
        )
        size_kb = (file_info.get("size", 0) or 0) / 1024
        embed.add_field(name="Size", value=f"{size_kb:.1f} KB", inline=True)
        embed.add_field(name="Status", value="Disabled" if is_disabled else "Enabled", inline=True)
        modified = str(file_info.get("modified_at", ""))[:19]
        if modified:
            embed.add_field(name="Modified", value=modified, inline=True)

        view = ModFileActionView(
            self.bot, self.server, filename, self.directory,
            is_disabled, self.files, self.all_servers,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class ModFileActionView(discord.ui.View):
    def __init__(self, bot: "MCBot", server: dict[str, Any], filename: str, directory: str,
                 is_disabled: bool, all_files: list[dict[str, Any]], all_servers: list[dict[str, Any]]) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.server = server
        self.filename = filename
        self.directory = directory
        self.is_disabled = is_disabled
        self.all_files = all_files
        self.all_servers = all_servers

    @discord.ui.button(label="Toggle Enable/Disable", style=discord.ButtonStyle.blurple, row=0)
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        identifier = self.server["identifier"]
        if self.is_disabled:
            new_name = self.filename.removesuffix(".disabled")
        else:
            new_name = f"{self.filename}.disabled"

        try:
            await self.bot.ptero_client().rename_file(identifier, self.directory, self.filename, new_name)
        except PterodactylApiError as exc:
            await interaction.edit_original_response(
                embed=discord.Embed(title="\u274c  Rename failed", description=str(exc), color=discord.Color.red()),
            )
            return

        status = "enabled" if self.is_disabled else "disabled"
        nav = discord.ui.View(timeout=900)
        nav.add_item(ModsBackButton(self.bot, self.server, self.all_servers))
        await interaction.edit_original_response(
            embed=discord.Embed(
                title=f"\u2705  Mod {status}",
                description=f"`{self.filename}` → `{new_name}`\n\nRestart the server for changes to take effect.",
                color=discord.Color.green(),
            ),
            view=nav,
        )

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, row=0)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        view = ConfirmDeleteView(self.bot, self.server, self.filename, self.directory, self.all_servers)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="\u26a0\ufe0f  Confirm Deletion",
                description=f"Are you sure you want to delete `{self.filename}` from {self.server['name']}?",
                color=discord.Color.orange(),
            ),
            view=view,
        )

    @discord.ui.button(label="\u25c0 Back to list", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        mods_cog = self.bot.get_cog("ModsCog")
        if mods_cog:
            await mods_cog.show_mods_panel(interaction, self.server, self.all_servers)


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, bot: "MCBot", server: dict[str, Any], filename: str,
                 directory: str, all_servers: list[dict[str, Any]]) -> None:
        super().__init__(timeout=60)
        self.bot = bot
        self.server = server
        self.filename = filename
        self.directory = directory
        self.all_servers = all_servers

    @discord.ui.button(label="Yes, delete", style=discord.ButtonStyle.danger, row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.ptero_client().delete_files(self.server["identifier"], self.directory, [self.filename])
        except PterodactylApiError as exc:
            await interaction.edit_original_response(
                embed=discord.Embed(title="\u274c  Delete failed", description=str(exc), color=discord.Color.red()),
            )
            return
        nav = discord.ui.View(timeout=900)
        nav.add_item(ModsBackButton(self.bot, self.server, self.all_servers))
        await interaction.edit_original_response(
            embed=discord.Embed(title="\u2705  Deleted", description=f"`{self.filename}` has been removed.", color=discord.Color.green()),
            view=nav,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        mods_cog = self.bot.get_cog("ModsCog")
        if mods_cog:
            await mods_cog.show_mods_panel(interaction, self.server, self.all_servers)


class UploadInstructionButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(style=discord.ButtonStyle.green, label="\u2b06 Upload", row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "\U0001f4e4 **To upload a mod/plugin:**\n"
            "Use the `/upload-mod` command with:\n"
            "• `server_id` — the server identifier\n"
            "• `file` — drag-and-drop your `.jar` file\n"
            "• `directory` — (optional) target folder\n\n"
            "The bot will auto-detect the mod type and check compatibility.",
            ephemeral=True,
        )


class RefreshModsButton(discord.ui.Button):
    def __init__(self, bot: "MCBot", server: dict[str, Any], directory: str, all_servers: list[dict[str, Any]]) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="\U0001f504 Refresh", row=1)
        self.bot = bot
        self.server = server
        self.directory = directory
        self.all_servers = all_servers

    async def callback(self, interaction: discord.Interaction) -> None:
        mods_cog = self.bot.get_cog("ModsCog")
        if mods_cog:
            await mods_cog.show_mods_panel(interaction, self.server, self.all_servers)


class ModsBackButton(discord.ui.Button):
    """Return to the server action view."""
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
    await bot.add_cog(ModsCog(bot))
