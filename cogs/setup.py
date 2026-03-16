"""Setup command — creates the mc-server-stuff channel and welcome embed."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.permissions import check_auth

if TYPE_CHECKING:
    from bot import MCBot


class SetupCog(commands.Cog):
    def __init__(self, bot: MCBot) -> None:
        self.bot = bot

    @app_commands.command(name="setup", description="Create the mc-server-stuff channel with server panel")
    async def setup_cmd(self, interaction: discord.Interaction) -> None:
        if not await check_auth(interaction):
            return

        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        guild = interaction.guild

        # Check if channel already exists
        existing = discord.utils.get(guild.text_channels, name="mc-server-stuff")
        if existing:
            await interaction.followup.send(
                f"\u26a0\ufe0f Channel already exists: {existing.mention}. Use `/mc` there to manage servers.",
                ephemeral=True,
            )
            return

        # Create the channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                read_message_history=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
                embed_links=True,
                attach_files=True,
            ),
        }
        # Grant admin roles send permission
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                )

        channel = await guild.create_text_channel(
            name="mc-server-stuff",
            topic="Minecraft Servers — create, upload mods/plugins, and manage instances.",
            overwrites=overwrites,
        )

        # Send welcome embed
        embed = discord.Embed(
            title="\U0001f3ae  Minecraft Server Panel",
            description=(
                "Create and manage Minecraft servers, upload mods/plugins, and control runtime.\n\n"
                "**Quick Start:**\n"
                "\u2022 `/mc` — View and manage existing servers\n"
                "\u2022 `/create-server` — Create a new Minecraft server\n"
                "\u2022 `/upload-mod` — Upload a mod or plugin JAR\n"
                "\u2022 `/backup` — Create a server backup\n"
                "\u2022 `/startserver` / `/stopserver` — Control the Azure VM\n"
                "\u2022 `/statusserver` — Check VM status\n\n"
                "**Tips:**\n"
                "\u2022 The bot auto-detects mod types and checks compatibility\n"
                "\u2022 Sessions auto-shutdown the VM to save costs\n"
                "\u2022 Console channels are created when a server starts\n"
            ),
            color=discord.Color.dark_green(),
        )
        embed.set_footer(text="Use the commands above to get started!")

        view = WelcomeView()
        await channel.send(embed=embed, view=view)

        await interaction.followup.send(
            f"\u2705 Created {channel.mention}! Your server panel is ready.",
            ephemeral=True,
        )


class WelcomeView(discord.ui.View):
    """Persistent buttons on the welcome embed."""
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="\U0001f3ae  Open Server Panel", style=discord.ButtonStyle.green, custom_id="welcome_mc")
    async def open_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        # Invoke /mc logic
        servers_cog = interaction.client.get_cog("ServersCog")  # type: ignore
        if servers_cog:
            await servers_cog.mc.callback(servers_cog, interaction)
        else:
            await interaction.response.send_message("Server management is not loaded.", ephemeral=True)

    @discord.ui.button(label="\U0001f680  Create Server", style=discord.ButtonStyle.blurple, custom_id="welcome_create")
    async def create_server(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        servers_cog = interaction.client.get_cog("ServersCog")  # type: ignore
        if servers_cog:
            await servers_cog.create_server.callback(servers_cog, interaction)
        else:
            await interaction.response.send_message("Server management is not loaded.", ephemeral=True)


async def setup(bot: MCBot) -> None:
    # Register persistent view so buttons work after restart
    bot.add_view(WelcomeView())
    await bot.add_cog(SetupCog(bot))
