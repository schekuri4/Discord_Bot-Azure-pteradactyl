"""Azure VM management and timed session handling."""

from __future__ import annotations

import asyncio
import time
from typing import Any, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from config import AZURE_RESOURCE_GROUP, AZURE_VM_NAME
from utils.permissions import check_auth

if TYPE_CHECKING:
    from bot import MCBot


class VMCog(commands.Cog):
    def __init__(self, bot: MCBot) -> None:
        self.bot = bot

    # ── helpers ───────────────────────────────────────────────────────

    def _vm_power_state(self) -> str:
        instance_view = self.bot.compute_client.virtual_machines.instance_view(
            AZURE_RESOURCE_GROUP, AZURE_VM_NAME,
        )
        statuses = instance_view.statuses
        if statuses is None:
            return "Unknown"
        for status in statuses:
            if status.code and status.code.startswith("PowerState/"):
                return status.display_status or status.code
        return "Unknown"

    async def shutdown_vm(self) -> str:
        self.bot.compute_client.virtual_machines.begin_deallocate(
            AZURE_RESOURCE_GROUP, AZURE_VM_NAME,
        ).result()
        return self._vm_power_state()

    def start_vm_sync(self) -> None:
        self.bot.compute_client.virtual_machines.begin_start(
            AZURE_RESOURCE_GROUP, AZURE_VM_NAME,
        ).result()

    # ── session management ───────────────────────────────────────────

    def session_remaining_str(self) -> str:
        session = self.bot.active_session
        if not session or "started_at" not in session:
            return "No active session"
        elapsed = time.time() - session["started_at"]
        total = session["duration"] * 60
        remaining = max(total - elapsed, 0)
        mins = int(remaining // 60)
        if mins >= 60:
            h, m = divmod(mins, 60)
            return f"{h}h {m}m remaining"
        return f"{mins}m remaining"

    def start_session(
        self,
        duration_minutes: int,
        channel_id: int,
        user_id: int,
        guild_id: int | None = None,
        server_identifier: str | None = None,
        console_channel_id: int | None = None,
    ) -> None:
        session = self.bot.active_session
        if session and session.get("task"):
            session["task"].cancel()

        task = asyncio.get_event_loop().create_task(
            self._session_timer(duration_minutes, channel_id, user_id)
        )
        self.bot.active_session = {
            "task": task,
            "duration": duration_minutes,
            "channel_id": channel_id,
            "user_id": user_id,
            "guild_id": guild_id,
            "server_identifier": server_identifier,
            "console_channel_id": console_channel_id,
            "started_at": time.time(),
        }

    async def _session_timer(self, duration_minutes: int, channel_id: int, user_id: int) -> None:
        warn_at = max(duration_minutes - 5, 0)
        if warn_at > 0:
            await asyncio.sleep(warn_at * 60)

        channel = self.bot.get_channel(channel_id)
        remaining = duration_minutes - warn_at
        if channel and isinstance(channel, discord.abc.Messageable):
            view = ExtendSessionView(self, user_id)
            await channel.send(
                f"<@{user_id}> \u26a0\ufe0f Your server session ends in **{remaining} minute(s)**! "
                "Press **Extend** below to add more time, or the VM will shut down automatically.",
                view=view,
            )

        await asyncio.sleep(remaining * 60)

        if self.bot.active_session and self.bot.active_session.get("task") is asyncio.current_task():
            if channel and isinstance(channel, discord.abc.Messageable):
                await channel.send(f"<@{user_id}> \u23f0 Session expired. Shutting down the Azure VM...")
            await self.cleanup_console_channel()
            await self.shutdown_vm()
            self.bot.active_session = None
            if channel and isinstance(channel, discord.abc.Messageable):
                await channel.send(f"<@{user_id}> \u2705 Azure VM has been deallocated.")

    async def cleanup_console_channel(self) -> None:
        session = self.bot.active_session
        if not session:
            return
        ch_id = session.get("console_channel_id")
        if ch_id:
            ch = self.bot.get_channel(ch_id)
            if ch and isinstance(ch, discord.abc.GuildChannel):
                try:
                    await ch.delete(reason="Server session ended.")
                except Exception:
                    pass
            session["console_channel_id"] = None

    async def create_console_channel(
        self,
        guild: discord.Guild,
        server_name: str,
        server_identifier: str,
        user: discord.Member | discord.User | None = None,
    ) -> discord.TextChannel | None:
        channel_name = f"console-{server_name.lower().replace(' ', '-')[:80]}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        if user and isinstance(user, discord.Member):
            overwrites[user] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            topic=f"Console for {server_name} ({server_identifier}). Type here to send commands.",
        )
        await channel.send(
            f"\U0001f5a5\ufe0f **Console channel for {server_name}**\n"
            "Everything you type here will be sent as a command to the game server.\n"
            "This channel will be deleted when the session ends."
        )
        return channel

    async def wait_for_panel(self, max_wait: int = 180, interval: int = 10) -> bool:
        elapsed = 0
        while elapsed < max_wait:
            try:
                await self.bot.ptero_client().list_servers()
                return True
            except Exception:
                await asyncio.sleep(interval)
                elapsed += interval
        return False

    # ── slash commands ────────────────────────────────────────────────

    @app_commands.command(name="statusserver", description="Show Azure VM power status")
    async def statusserver(self, interaction: discord.Interaction) -> None:
        if not await check_auth(interaction):
            return
        await interaction.response.defer(thinking=True)
        state = self._vm_power_state()
        await interaction.followup.send(f"`{AZURE_VM_NAME}` status: `{state}`")

    @app_commands.command(name="startserver", description="Start the Azure VM with a timed session")
    async def startserver(self, interaction: discord.Interaction) -> None:
        if not await check_auth(interaction):
            return
        view = DurationSelect(self, interaction.user.id, interaction.channel_id or 0)
        await interaction.response.send_message(
            "\u23f1\ufe0f Choose how long you need the Azure VM:",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="stopserver", description="Stop (deallocate) the Azure VM")
    async def stopserver(self, interaction: discord.Interaction) -> None:
        if not await check_auth(interaction):
            return
        await interaction.response.defer(thinking=True)
        if self.bot.active_session:
            if self.bot.active_session.get("task"):
                self.bot.active_session["task"].cancel()
            await self.cleanup_console_channel()
            self.bot.active_session = None

        self.bot.compute_client.virtual_machines.begin_deallocate(
            AZURE_RESOURCE_GROUP, AZURE_VM_NAME,
        ).result()
        state = self._vm_power_state()
        await interaction.followup.send(f"Stopped `{AZURE_VM_NAME}` (deallocated). Current status: `{state}`")


# ── Duration views ────────────────────────────────────────────────────

DURATION_OPTIONS = [
    discord.SelectOption(label="30 minutes", value="30"),
    discord.SelectOption(label="1 hour", value="60"),
    discord.SelectOption(label="2 hours", value="120"),
    discord.SelectOption(label="4 hours", value="240"),
    discord.SelectOption(label="Custom...", value="custom", description="Enter your own duration"),
]


class CustomDurationModal(discord.ui.Modal, title="Custom Duration"):
    minutes_input = discord.ui.TextInput(
        label="Duration in minutes", placeholder="e.g. 45",
        min_length=1, max_length=4, required=True,
    )

    def __init__(self, callback_coro) -> None:
        super().__init__()
        self._callback_coro = callback_coro

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.minutes_input.value.strip()
        if not raw.isdigit() or int(raw) < 1:
            await interaction.response.send_message("Please enter a valid number of minutes (1+).", ephemeral=True)
            return
        await self._callback_coro(interaction, int(raw))


class ExtendSessionView(discord.ui.View):
    def __init__(self, vm_cog: VMCog, user_id: int) -> None:
        super().__init__(timeout=300)
        self.vm_cog = vm_cog
        self.user_id = user_id

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="Extend session by...",
        options=[
            discord.SelectOption(label="30 minutes", value="30"),
            discord.SelectOption(label="1 hour", value="60"),
            discord.SelectOption(label="2 hours", value="120"),
        ],
        row=0,
    )
    async def extend_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:  # type: ignore[type-arg]
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the session owner can extend.", ephemeral=True)
            return
        extra = int(select.values[0])
        self.vm_cog.start_session(extra, interaction.channel_id or 0, self.user_id)
        await interaction.response.send_message(
            f"\u2705 Session extended by **{extra} minutes**. New timer started.",
        )
        self.stop()


class DurationSelect(discord.ui.View):
    def __init__(self, vm_cog: VMCog, user_id: int, channel_id: int) -> None:
        super().__init__(timeout=120)
        self.vm_cog = vm_cog
        self.user_id = user_id
        self.channel_id = channel_id

    @discord.ui.select(cls=discord.ui.Select, placeholder="How long do you need the server?", options=DURATION_OPTIONS, row=0)
    async def duration_callback(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:  # type: ignore[type-arg]
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the person who triggered this can choose.", ephemeral=True)
            return

        if select.values[0] == "custom":
            async def _run_custom(modal_interaction: discord.Interaction, minutes: int) -> None:
                await modal_interaction.response.defer(ephemeral=True)
                await modal_interaction.edit_original_response(
                    content=f"\u23f3 Starting Azure VM for **{minutes} minutes**...", view=None,
                )
                self.vm_cog.start_vm_sync()
                self.vm_cog.start_session(minutes, self.channel_id, self.user_id)
                hours, mins = divmod(minutes, 60)
                time_str = f"{hours}h {mins}m" if hours else f"{mins}m"
                await modal_interaction.edit_original_response(
                    content=f"\u2705 Azure VM started! Session length: **{time_str}**. "
                    "You will be warned 5 minutes before auto-shutdown.",
                    view=None,
                )
            await interaction.response.send_modal(CustomDurationModal(_run_custom))
            return

        duration = int(select.values[0])
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(
            content=f"\u23f3 Starting Azure VM for **{duration} minutes**...", view=None,
        )
        self.vm_cog.start_vm_sync()
        self.vm_cog.start_session(duration, self.channel_id, self.user_id)
        hours, mins = divmod(duration, 60)
        time_str = f"{hours}h {mins}m" if hours else f"{mins}m"
        await interaction.edit_original_response(
            content=f"\u2705 Azure VM started! Session length: **{time_str}**. "
            "You will be warned 5 minutes before auto-shutdown.",
            view=None,
        )


async def setup(bot: MCBot) -> None:
    await bot.add_cog(VMCog(bot))
