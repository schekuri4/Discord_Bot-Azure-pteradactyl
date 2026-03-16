"""Server listing, creation, dashboard, and power controls."""

from __future__ import annotations

import asyncio
import json
from typing import Any, TYPE_CHECKING, cast

import discord
from discord import app_commands
from discord.ext import commands
from mcstatus import JavaServer

from api.pterodactyl import PterodactylApiError
from api.minecraft import (
    fetch_release_versions,
    SERVER_TYPE_INFO,
    POPULAR_VERSIONS,
)
from config import (
    AZURE_RESOURCE_GROUP,
    AZURE_VM_NAME,
    SERVER_CACHE_FILE,
    PLANS,
    PTERODACTYL_ADMIN_KEY,
)
from utils.embeds import server_embed, server_list_embed, STATUS_EMOJI
from utils.permissions import check_auth, is_authorized

if TYPE_CHECKING:
    from bot import MCBot
    from cogs.vm import VMCog
    from cogs.mods import ModsCog
    from cogs.backups import BackupsCog


class ServersCog(commands.Cog):
    def __init__(self, bot: MCBot) -> None:
        self.bot = bot

    # ── cache helpers ─────────────────────────────────────────────────

    def _save_cache(self, servers: list[dict[str, Any]]) -> None:
        SERVER_CACHE_FILE.write_text(json.dumps(servers, indent=2), encoding="utf-8")

    def _load_cache(self) -> list[dict[str, Any]] | None:
        if SERVER_CACHE_FILE.exists():
            try:
                data = json.loads(SERVER_CACHE_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return None

    async def fetch_servers_with_cache(self) -> tuple[list[dict[str, Any]], bool]:
        try:
            servers = await self.bot.ptero_client().list_servers()
            self._save_cache(servers)
            return servers, False
        except Exception:
            cached = self._load_cache()
            if cached:
                return cached, True
            raise

    async def enrich_servers(self, servers: list[dict[str, Any]]) -> None:
        ptero = self.bot.ptero_client()

        async def _enrich_one(s: dict[str, Any]) -> None:
            try:
                resources = await ptero.get_server_resources(s["identifier"])
                s["state"] = str(resources.get("current_state", "unknown"))
                # Resource usage
                res = resources.get("resources", {})
                if res:
                    s["cpu_usage"] = res.get("cpu_absolute", 0)
                    s["memory_usage"] = res.get("memory_bytes", 0)
                    s["disk_usage"] = res.get("disk_bytes", 0)
                    s["uptime"] = res.get("uptime", 0)
            except Exception:
                s["state"] = "unknown"

            if s["state"] == "running" and s.get("ip") and s.get("port"):
                try:
                    srv = JavaServer.lookup(f"{s['ip']}:{s['port']}")
                    status = await srv.async_status()
                    s["players_online"] = status.players.online
                    s["players_max"] = status.players.max
                    s["mc_version"] = status.version.name
                except Exception:
                    pass

        await asyncio.gather(*(_enrich_one(s) for s in servers))

    # ── /mc command ───────────────────────────────────────────────────

    @app_commands.command(name="mc", description="Manage your Minecraft servers")
    async def mc(self, interaction: discord.Interaction) -> None:
        if not await check_auth(interaction):
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            servers, from_cache = await self.fetch_servers_with_cache()
        except Exception as exc:
            await interaction.followup.send(f"Could not fetch servers: {exc}", ephemeral=True)
            return

        if not servers:
            await interaction.followup.send("No servers found on the panel.", ephemeral=True)
            return

        vm_cog = self.bot.get_cog("VMCog")
        if not self.bot.active_session and not from_cache and vm_cog:
            vm_cog.start_session(15, interaction.channel_id or 0, interaction.user.id,
                                 guild_id=interaction.guild_id)

        if not from_cache:
            try:
                await self.enrich_servers(servers)
            except Exception:
                pass

        session_str = vm_cog.session_remaining_str() if vm_cog else None
        embed = server_list_embed(servers, session_str, from_cache)
        view = McView(self.bot, servers)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ── /create-server command ────────────────────────────────────────

    @app_commands.command(name="create-server", description="Create a new Minecraft server")
    async def create_server(self, interaction: discord.Interaction) -> None:
        if not await check_auth(interaction):
            return

        if not PTERODACTYL_ADMIN_KEY:
            await interaction.response.send_message(
                "\u274c Admin API key (`PTERODACTYL_ADMIN_KEY`) is not configured. "
                "Add a `ptla_` key to your `.env` to enable server creation.",
                ephemeral=True,
            )
            return

        view = CreateServerStep1View(self.bot, interaction.user.id)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="\U0001f680  Create a New Minecraft Server",
                description="Choose a server type to get started.",
                color=discord.Color.blurple(),
            ),
            view=view,
            ephemeral=True,
        )


# ── Server list views ────────────────────────────────────────────────


class ServerSelect(discord.ui.Select["McView"]):
    def __init__(self, bot: "MCBot", servers: list[dict[str, Any]]) -> None:
        options = [
            discord.SelectOption(
                label=s["name"][:100],
                value=s["identifier"],
                description=f"ID: {s['identifier']}"[:100],
            )
            for s in servers[:25]
        ]
        super().__init__(placeholder="Select a server...", options=options, row=0)
        self.bot = bot
        self.servers = {s["identifier"]: s for s in servers}
        self.servers_list = servers

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        identifier = self.values[0]
        server = self.servers.get(identifier)
        if not server:
            await interaction.response.send_message("Server not found.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            resources = await self.bot.ptero_client().get_server_resources(identifier)
        except Exception:
            resources = {}

        state = str(resources.get("current_state", "unknown"))

        # Add resource info
        res = resources.get("resources", {})
        if res:
            server["cpu_usage"] = res.get("cpu_absolute", 0)
            server["memory_usage"] = res.get("memory_bytes", 0)
            server["disk_usage"] = res.get("disk_bytes", 0)
            server["uptime"] = res.get("uptime", 0)

        vm_cog = self.bot.get_cog("VMCog")
        session_str = vm_cog.session_remaining_str() if vm_cog else None
        embed = server_embed(server, state, session_str)

        # Add resource metrics if running
        if state == "running" and res:
            cpu = res.get("cpu_absolute", 0)
            mem_mb = (res.get("memory_bytes", 0) or 0) / 1_048_576
            disk_mb = (res.get("disk_bytes", 0) or 0) / 1_048_576
            uptime_s = (res.get("uptime", 0) or 0) / 1000
            uptime_h = int(uptime_s // 3600)
            uptime_m = int((uptime_s % 3600) // 60)
            embed.add_field(
                name="\U0001f4ca Metrics",
                value=f"CPU: `{cpu:.1f}%` | RAM: `{mem_mb:.0f} MB` | Disk: `{disk_mb:.0f} MB` | Uptime: `{uptime_h}h {uptime_m}m`",
                inline=False,
            )

        view = ServerActionView(self.bot, server, state, self.servers_list)
        await interaction.edit_original_response(embed=embed, view=view)


class McView(discord.ui.View):
    def __init__(self, bot: "MCBot", servers: list[dict[str, Any]]) -> None:
        super().__init__(timeout=900)
        self.add_item(ServerSelect(bot, servers))


# ── Server action views ──────────────────────────────────────────────


class ServerActionView(discord.ui.View):
    def __init__(self, bot: "MCBot", server: dict[str, Any], state: str,
                 all_servers: list[dict[str, Any]] | None = None) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.server = server
        self.identifier = server["identifier"]
        self.all_servers = all_servers or []

        if state in ("offline", "unknown"):
            self.add_item(PowerButton(bot, self.identifier, "start", discord.ButtonStyle.green, "\u25B6 Start"))
        if state == "running":
            self.add_item(PowerButton(bot, self.identifier, "restart", discord.ButtonStyle.blurple, "\U0001f504 Restart"))
            self.add_item(PowerButton(bot, self.identifier, "stop", discord.ButtonStyle.red, "\u23F9 Stop"))
        if state in ("starting", "stopping"):
            self.add_item(PowerButton(bot, self.identifier, "kill", discord.ButtonStyle.danger, "\u26A0 Kill"))

        self.add_item(RefreshButton(bot, self.server))
        # Mod/Plugin management button
        self.add_item(ModsButton(bot, self.server))
        # Backup button
        self.add_item(BackupButton(bot, self.server))
        if self.all_servers:
            self.add_item(BackButton(bot, self.all_servers))


class PowerButton(discord.ui.Button["ServerActionView"]):
    def __init__(self, bot: "MCBot", identifier: str, signal: str, style: discord.ButtonStyle, label: str) -> None:
        super().__init__(style=style, label=label, row=1)
        self.bot = bot
        self.identifier = identifier
        self.signal = signal

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        server = self.view.server if self.view else {}
        vm_cog = self.bot.get_cog("VMCog")

        # Start → show duration picker
        if self.signal == "start":
            all_servers = self.view.all_servers if self.view else []
            if self.bot.active_session:
                view = QuickStartDurationView(self.bot, interaction.user.id, interaction.channel_id or 0,
                                               self.identifier, server.get("name", "server"),
                                               server=server, all_servers=all_servers)
                await interaction.response.edit_message(
                    embed=discord.Embed(
                        title="\u23f1\ufe0f  Choose session duration",
                        description="The VM is already running. Pick how long you need the server.",
                        color=discord.Color.blurple(),
                    ),
                    view=view,
                )
            else:
                view = ServerStartDurationView(self.bot, interaction.user.id, interaction.channel_id or 0,
                                                self.identifier, server.get("name", "server"),
                                                server=server, all_servers=all_servers)
                await interaction.response.edit_message(
                    embed=discord.Embed(
                        title="\u23f1\ufe0f  Choose session duration",
                        description="The Azure VM will start, then the game server will boot automatically.",
                        color=discord.Color.blurple(),
                    ),
                    view=view,
                )
            return

        signal_labels = {"stop": "Stopping", "restart": "Restarting", "kill": "Killing"}
        action_label = signal_labels.get(self.signal, self.signal.title())

        await interaction.response.edit_message(
            embed=discord.Embed(
                title=f"\u23f3  {action_label} {server.get('name', 'server')}...",
                description="Please wait, this may take a moment.",
                color=discord.Color.gold(),
            ),
            view=None,
        )

        try:
            await self.bot.ptero_client().send_power_signal(self.identifier, self.signal)
        except Exception as exc:
            error_embed = discord.Embed(
                title="\u274c  Action Failed",
                description=f"Could not send `{self.signal}`.\n\n"
                    f"**Reason:** {exc or 'Panel may be unreachable. Is the Azure VM running?'}",
                color=discord.Color.red(),
            )
            all_servers = self.view.all_servers if self.view else []
            nav = discord.ui.View(timeout=900)
            if all_servers:
                nav.add_item(BackButton(self.bot, all_servers))
            await interaction.edit_original_response(embed=error_embed, view=nav if all_servers else None)
            return

        await asyncio.sleep(2)
        try:
            resources = await self.bot.ptero_client().get_server_resources(self.identifier)
        except Exception:
            resources = {}
        state = str(resources.get("current_state", "unknown"))

        # Auto-shutdown check after stop/kill
        if self.signal in ("stop", "kill") and self.bot.active_session:
            try:
                all_srv = await self.bot.ptero_client().list_servers()
                any_running = False
                for s in all_srv:
                    try:
                        res = await self.bot.ptero_client().get_server_resources(s["identifier"])
                        s_state = str(res.get("current_state", "offline"))
                    except Exception:
                        s_state = "offline"
                    if s_state in ("running", "starting"):
                        any_running = True
                        break

                if not any_running and vm_cog:
                    session_str = vm_cog.session_remaining_str()
                    embed = server_embed(server, state, session_str)
                    embed.add_field(name="\U0001f6d1 Auto-shutdown", value="No game servers are running. Deallocating Azure VM...", inline=False)
                    await interaction.edit_original_response(embed=embed, view=None)

                    if self.bot.active_session.get("task"):
                        self.bot.active_session["task"].cancel()
                    await vm_cog.cleanup_console_channel()
                    await vm_cog.shutdown_vm()
                    self.bot.active_session = None

                    embed = server_embed(server, state)
                    embed.add_field(name="\u2705 VM Deallocated", value="All game servers were offline — Azure VM has been shut down to save costs.", inline=False)
                    all_servers = self.view.all_servers if self.view else []
                    nav = discord.ui.View(timeout=900)
                    if all_servers:
                        nav.add_item(BackButton(self.bot, all_servers))
                    await interaction.edit_original_response(embed=embed, view=nav if all_servers else None)
                    return
            except Exception:
                pass

        session_str = vm_cog.session_remaining_str() if vm_cog else None
        embed = server_embed(server, state, session_str)
        new_view = ServerActionView(self.bot, server, state, self.view.all_servers if self.view else [])
        await interaction.edit_original_response(embed=embed, view=new_view)


class RefreshButton(discord.ui.Button["ServerActionView"]):
    def __init__(self, bot: "MCBot", server: dict[str, Any]) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="\U0001f504 Refresh", row=1)
        self.bot = bot
        self.server = server

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
        embed = server_embed(self.server, state, session_str)
        new_view = ServerActionView(self.bot, self.server, state, self.view.all_servers if self.view else [])
        await interaction.edit_original_response(embed=embed, view=new_view)


class ModsButton(discord.ui.Button["ServerActionView"]):
    def __init__(self, bot: "MCBot", server: dict[str, Any]) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="\U0001f9e9 Mods/Plugins", row=2)
        self.bot = bot
        self.server = server

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        # Delegate to the ModsCog
        mods_cog = self.bot.get_cog("ModsCog")
        if mods_cog:
            await mods_cog.show_mods_panel(interaction, self.server, self.view.all_servers if self.view else [])
        else:
            await interaction.response.send_message("Mod management is not loaded.", ephemeral=True)


class BackupButton(discord.ui.Button["ServerActionView"]):
    def __init__(self, bot: "MCBot", server: dict[str, Any]) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="\U0001f4be Backups", row=2)
        self.bot = bot
        self.server = server

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        backups_cog = self.bot.get_cog("BackupsCog")
        if backups_cog:
            await backups_cog.show_backups_panel(interaction, self.server, self.view.all_servers if self.view else [])
        else:
            await interaction.response.send_message("Backup management is not loaded.", ephemeral=True)


class BackButton(discord.ui.Button["ServerActionView"]):
    def __init__(self, bot: "MCBot", servers: list[dict[str, Any]]) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="\u25c0 Back", row=2)
        self.bot = bot
        self.servers = servers

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        servers_cog = self.bot.get_cog("ServersCog")
        if servers_cog:
            try:
                await servers_cog.enrich_servers(self.servers)
            except Exception:
                pass
        vm_cog = self.bot.get_cog("VMCog")
        session_str = vm_cog.session_remaining_str() if vm_cog else None
        embed = server_list_embed(self.servers, session_str)
        view = McView(self.bot, self.servers)
        await interaction.response.edit_message(embed=embed, view=view)


# ── Duration views for starting game servers ──────────────────────────

from cogs.vm import DURATION_OPTIONS, CustomDurationModal  # noqa: E402


class ServerStartDurationView(discord.ui.View):
    """Boot Azure VM → wait for panel → start game server."""
    def __init__(self, bot: "MCBot", user_id: int, channel_id: int, server_identifier: str, server_name: str,
                 server: dict[str, Any] | None = None, all_servers: list[dict[str, Any]] | None = None) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.user_id = user_id
        self.channel_id = channel_id
        self.server_identifier = server_identifier
        self.server_name = server_name
        self.server = server or {"name": server_name, "identifier": server_identifier}
        self.all_servers = all_servers or []

    @discord.ui.select(cls=discord.ui.Select, placeholder="How long do you need the server?", options=DURATION_OPTIONS, row=0)
    async def duration_callback(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:  # type: ignore[type-arg]
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the person who triggered this can choose.", ephemeral=True)
            return

        if select.values[0] == "custom":
            async def _run_custom(modal_interaction: discord.Interaction, minutes: int) -> None:
                await modal_interaction.response.edit_message(
                    embed=discord.Embed(title="\u23f3  Starting Azure VM...", description="Booting the underlying VM.", color=discord.Color.gold()),
                    view=None,
                )
                await self._do_start(modal_interaction, minutes)
            await interaction.response.send_modal(CustomDurationModal(_run_custom))
            return

        duration = int(select.values[0])
        hours, mins = divmod(duration, 60)
        time_str = f"{hours}h {mins}m" if hours else f"{mins}m"
        await interaction.response.edit_message(
            embed=discord.Embed(title=f"\u23f3  Starting Azure VM ({time_str})...", description="Booting the underlying VM.", color=discord.Color.gold()),
            view=None,
        )
        await self._do_start(interaction, duration)

    async def _do_start(self, interaction: discord.Interaction, duration: int) -> None:
        hours, mins = divmod(duration, 60)
        time_str = f"{hours}h {mins}m" if hours else f"{mins}m"
        vm_cog = self.bot.get_cog("VMCog")
        if vm_cog:
            vm_cog.start_vm_sync()
            vm_cog.start_session(duration, self.channel_id, self.user_id)

            await interaction.edit_original_response(
                embed=discord.Embed(title="\u23f3  Waiting for game panel...", description="Azure VM is up. Waiting for Pterodactyl.", color=discord.Color.gold()),
            )
            panel_up = await vm_cog.wait_for_panel()
            if not panel_up:
                nav = discord.ui.View(timeout=900)
                if self.all_servers:
                    nav.add_item(BackButton(self.bot, self.all_servers))
                await interaction.edit_original_response(
                    embed=discord.Embed(title="\u26a0\ufe0f  Panel did not respond", description=f"VM running (session: **{time_str}**) but panel didn't come up.", color=discord.Color.orange()),
                    view=nav if self.all_servers else None,
                )
                return

            await interaction.edit_original_response(
                embed=discord.Embed(title=f"\u23f3  Starting {self.server_name}...", description="Panel is up. Starting game server.", color=discord.Color.gold()),
            )
            try:
                await self.bot.ptero_client().send_power_signal(self.server_identifier, "start")
            except Exception as exc:
                nav = discord.ui.View(timeout=900)
                if self.all_servers:
                    nav.add_item(BackButton(self.bot, self.all_servers))
                await interaction.edit_original_response(
                    embed=discord.Embed(title="\u274c  Could not start game server", description=str(exc), color=discord.Color.red()),
                    view=nav if self.all_servers else None,
                )
                return

            await asyncio.sleep(3)
            try:
                resources = await self.bot.ptero_client().get_server_resources(self.server_identifier)
            except Exception:
                resources = {}
            state = str(resources.get("current_state", "unknown"))

            console_ch = None
            if interaction.guild:
                try:
                    console_ch = await vm_cog.create_console_channel(
                        interaction.guild, self.server_name, self.server_identifier, user=interaction.user,
                    )
                except Exception:
                    pass

            if self.bot.active_session:
                self.bot.active_session["server_identifier"] = self.server_identifier
                self.bot.active_session["guild_id"] = interaction.guild_id
                if console_ch:
                    self.bot.active_session["console_channel_id"] = console_ch.id

            session_str = vm_cog.session_remaining_str()
            embed = server_embed(self.server, state, session_str)
            if console_ch:
                embed.add_field(name="\U0001f5a5\ufe0f Console", value=console_ch.mention, inline=False)
            embed.set_footer(text=f"Session: {time_str} \u2022 Auto-shutdown enabled")
            action_view = ServerActionView(self.bot, self.server, state, self.all_servers)
            await interaction.edit_original_response(embed=embed, view=action_view)


class QuickStartDurationView(discord.ui.View):
    """VM already running — just start game server + set timer."""
    def __init__(self, bot: "MCBot", user_id: int, channel_id: int, server_identifier: str, server_name: str,
                 server: dict[str, Any] | None = None, all_servers: list[dict[str, Any]] | None = None) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.user_id = user_id
        self.channel_id = channel_id
        self.server_identifier = server_identifier
        self.server_name = server_name
        self.server = server or {"name": server_name, "identifier": server_identifier}
        self.all_servers = all_servers or []

    @discord.ui.select(cls=discord.ui.Select, placeholder="How long do you need the server?", options=DURATION_OPTIONS, row=0)
    async def duration_callback(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:  # type: ignore[type-arg]
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the person who triggered this can choose.", ephemeral=True)
            return

        if select.values[0] == "custom":
            async def _run_custom(modal_interaction: discord.Interaction, minutes: int) -> None:
                await modal_interaction.response.edit_message(
                    embed=discord.Embed(title=f"\u23f3  Starting {self.server_name}...", description="Sending start signal.", color=discord.Color.gold()),
                    view=None,
                )
                await self._do_start(modal_interaction, minutes)
            await interaction.response.send_modal(CustomDurationModal(_run_custom))
            return

        duration = int(select.values[0])
        await interaction.response.edit_message(
            embed=discord.Embed(title=f"\u23f3  Starting {self.server_name}...", description="Sending start signal.", color=discord.Color.gold()),
            view=None,
        )
        await self._do_start(interaction, duration)

    async def _do_start(self, interaction: discord.Interaction, duration: int) -> None:
        hours, mins = divmod(duration, 60)
        time_str = f"{hours}h {mins}m" if hours else f"{mins}m"
        vm_cog = self.bot.get_cog("VMCog")
        if vm_cog:
            vm_cog.start_session(duration, self.channel_id, self.user_id,
                                  guild_id=interaction.guild_id, server_identifier=self.server_identifier)
        try:
            await self.bot.ptero_client().send_power_signal(self.server_identifier, "start")
        except Exception as exc:
            nav = discord.ui.View(timeout=900)
            if self.all_servers:
                nav.add_item(BackButton(self.bot, self.all_servers))
            await interaction.edit_original_response(
                embed=discord.Embed(title="\u274c  Could not start game server", description=str(exc), color=discord.Color.red()),
                view=nav if self.all_servers else None,
            )
            return

        await asyncio.sleep(3)
        try:
            resources = await self.bot.ptero_client().get_server_resources(self.server_identifier)
        except Exception:
            resources = {}
        state = str(resources.get("current_state", "unknown"))

        console_ch = None
        if interaction.guild and vm_cog:
            try:
                console_ch = await vm_cog.create_console_channel(
                    interaction.guild, self.server_name, self.server_identifier, user=interaction.user,
                )
            except Exception:
                pass

        if self.bot.active_session:
            self.bot.active_session["server_identifier"] = self.server_identifier
            self.bot.active_session["guild_id"] = interaction.guild_id
            if console_ch:
                self.bot.active_session["console_channel_id"] = console_ch.id

        session_str = vm_cog.session_remaining_str() if vm_cog else None
        embed = server_embed(self.server, state, session_str)
        if console_ch:
            embed.add_field(name="\U0001f5a5\ufe0f Console", value=console_ch.mention, inline=False)
        embed.set_footer(text=f"Session: {time_str} \u2022 Auto-shutdown enabled")
        action_view = ServerActionView(self.bot, self.server, state, self.all_servers)
        await interaction.edit_original_response(embed=embed, view=action_view)


# ── Server creation flow ─────────────────────────────────────────────


class CreateServerStep1View(discord.ui.View):
    """Step 1: Choose server type."""
    def __init__(self, bot: "MCBot", user_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="Choose server type...",
        options=[
            discord.SelectOption(label="Vanilla", value="vanilla", description="Official Mojang server, no mods/plugins"),
            discord.SelectOption(label="Paper", value="paper", description="High-performance, supports Bukkit plugins"),
            discord.SelectOption(label="Forge", value="forge", description="Supports Forge mods"),
            discord.SelectOption(label="Fabric", value="fabric", description="Lightweight mod loader"),
            discord.SelectOption(label="Spigot", value="spigot", description="Modified CraftBukkit, supports plugins"),
        ],
        row=0,
    )
    async def type_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:  # type: ignore[type-arg]
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the person who started creation can choose.", ephemeral=True)
            return

        server_type = select.values[0]
        info = SERVER_TYPE_INFO.get(server_type, {})

        embed = discord.Embed(
            title=f"\U0001f680  Create Server — {info.get('label', server_type.title())}",
            description=f"{info.get('description', '')}\n\nNow choose a Minecraft version.",
            color=discord.Color.blurple(),
        )
        view = CreateServerStep2View(self.bot, self.user_id, server_type)
        await interaction.response.edit_message(embed=embed, view=view)


class CreateServerStep2View(discord.ui.View):
    """Step 2: Choose version."""
    def __init__(self, bot: "MCBot", user_id: int, server_type: str) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.server_type = server_type

        options = [
            discord.SelectOption(label=v, value=v)
            for v in POPULAR_VERSIONS[:25]
        ]
        self.version_select = discord.ui.Select(
            placeholder="Choose Minecraft version...",
            options=options,
            row=0,
        )
        self.version_select.callback = self._version_callback
        self.add_item(self.version_select)

    async def _version_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the person who started creation can choose.", ephemeral=True)
            return

        version = self.version_select.values[0]
        embed = discord.Embed(
            title=f"\U0001f680  Create Server — {self.server_type.title()} {version}",
            description="Choose a performance plan.",
            color=discord.Color.blurple(),
        )
        view = CreateServerStep3View(self.bot, self.user_id, self.server_type, version)
        await interaction.response.edit_message(embed=embed, view=view)


class CreateServerStep3View(discord.ui.View):
    """Step 3: Choose plan, then confirm."""
    def __init__(self, bot: "MCBot", user_id: int, server_type: str, version: str) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.server_type = server_type
        self.version = version

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="Choose a plan...",
        options=[
            discord.SelectOption(label="Basic", value="basic", description="2 GB RAM, 1 core, 10 GB disk"),
            discord.SelectOption(label="Standard", value="standard", description="4 GB RAM, 2 cores, 20 GB disk"),
            discord.SelectOption(label="Premium", value="premium", description="8 GB RAM, 4 cores, 50 GB disk"),
        ],
        row=0,
    )
    async def plan_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:  # type: ignore[type-arg]
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the person who started creation can choose.", ephemeral=True)
            return

        plan_key = select.values[0]
        plan = PLANS[plan_key]

        # Show confirmation with name/description modal
        modal = ServerNameModal(self.bot, self.user_id, self.server_type, self.version, plan_key)
        await interaction.response.send_modal(modal)


class ServerNameModal(discord.ui.Modal, title="Server Details"):
    server_name = discord.ui.TextInput(label="Server Name", placeholder="My Minecraft Server", max_length=100, required=True)
    server_desc = discord.ui.TextInput(label="Description (optional)", placeholder="A fun server for friends", max_length=200, required=False, style=discord.TextStyle.short)

    def __init__(self, bot: "MCBot", user_id: int, server_type: str, version: str, plan_key: str) -> None:
        super().__init__()
        self.bot = bot
        self.user_id = user_id
        self.server_type = server_type
        self.version = version
        self.plan_key = plan_key

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.server_name.value.strip()
        desc = self.server_desc.value.strip() if self.server_desc.value else ""
        plan = PLANS[self.plan_key]

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="\u23f3  Provisioning server...",
                description=f"**{name}** — {self.server_type.title()} {self.version}\n"
                            f"Plan: {self.plan_key.title()} ({plan['memory']} MB RAM)\n\n"
                            "This may take a moment...",
                color=discord.Color.gold(),
            ),
            view=None,
        )

        try:
            admin = self.bot.ptero_admin()
            # Find egg for server type
            nests = await admin.list_nests()
            egg_id = None
            nest_id = None
            docker_image = ""
            startup = ""

            for nest in nests:
                eggs = nest.get("relationships", {}).get("eggs", {}).get("data", [])
                for egg_data in eggs:
                    egg_attrs = egg_data.get("attributes", {})
                    egg_name = str(egg_attrs.get("name", "")).lower()
                    if self.server_type in egg_name or egg_name in self.server_type:
                        egg_id = egg_attrs["id"]
                        nest_id = nest["id"]
                        docker_image = str(egg_attrs.get("docker_image", ""))
                        startup = str(egg_attrs.get("startup", ""))
                        break
                if egg_id:
                    break

            if not egg_id or nest_id is None:
                await interaction.edit_original_response(
                    embed=discord.Embed(
                        title="\u274c  No matching egg found",
                        description=f"Could not find a Pterodactyl egg for server type '{self.server_type}'. "
                                    "Check your panel's Nests configuration.",
                        color=discord.Color.red(),
                    ),
                )
                return

            # Get egg details for environment variables
            egg_detail = await admin.get_egg(nest_id, egg_id)
            env_vars = {}
            for var_data in egg_detail.get("relationships", {}).get("variables", {}).get("data", []):
                var_attrs = var_data.get("attributes", {})
                env_name = var_attrs.get("env_variable", "")
                default = var_attrs.get("default_value", "")
                env_vars[env_name] = default

            # Override version
            version_keys = ["MINECRAFT_VERSION", "MC_VERSION", "VANILLA_VERSION", "SERVER_VERSION", "VERSION"]
            for key in version_keys:
                if key in env_vars:
                    env_vars[key] = self.version

            # Find allocation
            ptero_user_id = await admin.get_first_admin_user_id()
            _, allocation_id = await admin.find_free_allocation()

            result = await admin.create_server(
                name=name,
                user_id=ptero_user_id,
                egg_id=egg_id,
                docker_image=docker_image,
                startup=startup,
                environment=env_vars,
                allocation_id=allocation_id,
                memory=plan["memory"],
                disk=plan["disk"],
                cpu=plan["cpu"],
                description=desc,
            )

            server_id = result.get("identifier", result.get("uuid", "?"))
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="\u2705  Server Created!",
                    description=f"**{name}** has been provisioned.\n\n"
                                f"**Type:** {self.server_type.title()}\n"
                                f"**Version:** {self.version}\n"
                                f"**Plan:** {self.plan_key.title()}\n"
                                f"**ID:** `{server_id}`\n\n"
                                "Use `/mc` to view and start your new server.",
                    color=discord.Color.green(),
                ),
            )

        except PterodactylApiError as exc:
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="\u274c  Server creation failed",
                    description=str(exc),
                    color=discord.Color.red(),
                ),
            )
        except Exception as exc:
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="\u274c  Unexpected error",
                    description=f"```{exc}```",
                    color=discord.Color.red(),
                ),
            )


async def setup(bot: "MCBot") -> None:
    await bot.add_cog(ServersCog(bot))
