import asyncio
import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv
from mcstatus import JavaServer
from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient


load_dotenv()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


DISCORD_BOT_TOKEN = _require_env("DISCORD_BOT_TOKEN")
AZURE_TENANT_ID = _require_env("AZURE_TENANT_ID")
AZURE_CLIENT_ID = _require_env("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = _require_env("AZURE_CLIENT_SECRET")
AZURE_SUBSCRIPTION_ID = _require_env("AZURE_SUBSCRIPTION_ID")
AZURE_RESOURCE_GROUP = _require_env("AZURE_RESOURCE_GROUP")
AZURE_VM_NAME = _require_env("AZURE_VM_NAME")
PTERODACTYL_PANEL_URL = os.getenv("PTERODACTYL_PANEL_URL")
PTERODACTYL_API_KEY = os.getenv("PTERODACTYL_API_KEY")

SERVER_CACHE_FILE = Path(__file__).parent / "server_cache.json"

# Active timed session state
# Keys: task, duration, channel_id, user_id, console_channel_id, server_identifier, guild_id
active_session: dict[str, Any] | None = None

credential = ClientSecretCredential(
    tenant_id=AZURE_TENANT_ID,
    client_id=AZURE_CLIENT_ID,
    client_secret=AZURE_CLIENT_SECRET,
)
compute_client = ComputeManagementClient(credential, AZURE_SUBSCRIPTION_ID)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


class PterodactylApiError(Exception):
    pass


class PterodactylApi:
    def __init__(self, panel_url: str, api_key: str) -> None:
        self.panel_url = panel_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "Application/vnd.pterodactyl.v1+json",
            "Content-Type": "application/json",
        }

    async def list_servers(self) -> list[dict[str, Any]]:
        # Client API lists servers with allocation (IP/port) and status info.
        url = f"{self.panel_url}/api/client?per_page=100"
        servers: list[dict[str, Any]] = []
        timeout = aiohttp.ClientTimeout(total=10)

        async with aiohttp.ClientSession() as session:
            while url:
                async with session.get(url, headers=self._headers(), timeout=timeout) as response:
                    body = await response.text()
                    if response.status >= 400:
                        raise PterodactylApiError(
                            f"Pterodactyl list failed ({response.status}): {body[:300]}"
                        )

                    payload = await response.json()
                    for item in payload.get("data", []):
                        attrs: dict[str, Any] = item.get("attributes", {})
                        name = str(attrs.get("name", "unknown"))
                        identifier = str(attrs.get("identifier", ""))
                        uuid = str(attrs.get("uuid", ""))
                        description = str(attrs.get("description", "") or "")
                        node = str(attrs.get("node", ""))
                        is_suspended = bool(attrs.get("is_suspended", False))

                        # Allocation info (IP + port)
                        relationships = attrs.get("relationships", {})
                        allocs = relationships.get("allocations", {}).get("data", [])
                        ip = ""
                        port = ""
                        if allocs:
                            alloc_attrs = allocs[0].get("attributes", {})
                            ip = str(alloc_attrs.get("ip_alias") or alloc_attrs.get("ip", ""))
                            port = str(alloc_attrs.get("port", ""))

                        if identifier:
                            servers.append(
                                {
                                    "name": name,
                                    "identifier": identifier,
                                    "uuid": uuid,
                                    "description": description,
                                    "node": node,
                                    "ip": ip,
                                    "port": port,
                                    "suspended": is_suspended,
                                }
                            )

                    next_url = (
                        payload.get("meta", {})
                        .get("pagination", {})
                        .get("links", {})
                        .get("next")
                    )
                    url = next_url

        return servers

    async def get_server_resources(self, identifier: str) -> dict[str, Any]:
        url = f"{self.panel_url}/api/client/servers/{identifier}/resources"
        timeout = aiohttp.ClientTimeout(total=15)

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(), timeout=timeout) as response:
                if response.status >= 400:
                    return {"current_state": "unknown"}
                payload = await response.json()
                return payload.get("attributes", {})

    async def send_power_signal(self, identifier: str, signal: str) -> None:
        url = f"{self.panel_url}/api/client/servers/{identifier}/power"
        payload = {"signal": signal}
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), json=payload, timeout=timeout) as response:
                if response.status in (200, 202, 204):
                    return

                body = await response.text()
                if response.status in (401, 403):
                    raise PterodactylApiError(
                        "Pterodactyl rejected power control. Check API key permissions."
                    )
                raise PterodactylApiError(
                    f"Pterodactyl power signal failed ({response.status}): {body[:300]}"
                )

    async def send_command(self, identifier: str, command: str) -> None:
        url = f"{self.panel_url}/api/client/servers/{identifier}/command"
        payload = {"command": command}
        timeout = aiohttp.ClientTimeout(total=15)

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), json=payload, timeout=timeout) as response:
                if response.status in (200, 202, 204):
                    return
                body = await response.text()
                raise PterodactylApiError(
                    f"Console command failed ({response.status}): {body[:300]}"
                )


def _ptero_client() -> PterodactylApi:
    if not PTERODACTYL_PANEL_URL or not PTERODACTYL_API_KEY:
        raise RuntimeError(
            "Missing Pterodactyl config. Set PTERODACTYL_PANEL_URL and PTERODACTYL_API_KEY in .env"
        )
    return PterodactylApi(PTERODACTYL_PANEL_URL, PTERODACTYL_API_KEY)


def _is_authorized(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    perms = interaction.user.guild_permissions  # type: ignore[union-attr]
    return perms.administrator


def _vm_power_state() -> str:
    instance_view = compute_client.virtual_machines.instance_view(
        AZURE_RESOURCE_GROUP,
        AZURE_VM_NAME,
    )
    statuses = instance_view.statuses
    if statuses is None:
        return "Unknown"

    for status in statuses:
        if status.code and status.code.startswith("PowerState/"):
            return status.display_status or status.code
    return "Unknown"


async def _check_auth(interaction: discord.Interaction) -> bool:
    if _is_authorized(interaction):
        return True
    if interaction.response.is_done():
        await interaction.followup.send("You are not authorized to run this command.", ephemeral=True)
    else:
        await interaction.response.send_message("You are not authorized to run this command.", ephemeral=True)
    return False


# ── Local server cache ───────────────────────────────────────────────

def _save_server_cache(servers: list[dict[str, Any]]) -> None:
    SERVER_CACHE_FILE.write_text(json.dumps(servers, indent=2), encoding="utf-8")


def _load_server_cache() -> list[dict[str, Any]] | None:
    if SERVER_CACHE_FILE.exists():
        try:
            data = json.loads(SERVER_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return None


async def _fetch_servers_with_cache() -> tuple[list[dict[str, Any]], bool]:
    """Return (servers, from_cache). Tries API first, falls back to cache."""
    try:
        servers = await _ptero_client().list_servers()
        _save_server_cache(servers)
        return servers, False
    except Exception:
        cached = _load_server_cache()
        if cached:
            return cached, True
        raise


# ── Timed session helpers ────────────────────────────────────────────

async def _shutdown_vm() -> str:
    """Deallocate the Azure VM and return new state."""
    compute_client.virtual_machines.begin_deallocate(
        AZURE_RESOURCE_GROUP, AZURE_VM_NAME
    ).result()
    return _vm_power_state()


async def _session_timer(duration_minutes: int, channel_id: int, user_id: int) -> None:
    """Background task that warns before expiry and auto-shuts down the VM."""
    global active_session
    warn_at = max(duration_minutes - 5, 0)  # warn 5 min before end

    # Sleep until warning time
    if warn_at > 0:
        await asyncio.sleep(warn_at * 60)

    channel = client.get_channel(channel_id)
    remaining = duration_minutes - warn_at
    if channel and isinstance(channel, discord.abc.Messageable):
        view = ExtendSessionView(user_id)
        await channel.send(
            f"<@{user_id}> \u26a0\ufe0f Your server session ends in **{remaining} minute(s)**! "
            "Press **Extend** below to add more time, or the VM will shut down automatically.",
            view=view,
        )

    # Sleep the remaining time
    await asyncio.sleep(remaining * 60)

    # Check if session was extended (task would have been replaced)
    if active_session and active_session.get("task") is asyncio.current_task():
        if channel and isinstance(channel, discord.abc.Messageable):
            await channel.send(
                f"<@{user_id}> \u23f0 Session expired. Shutting down the Azure VM..."
            )
        await _cleanup_console_channel()
        await _shutdown_vm()
        active_session = None
        if channel and isinstance(channel, discord.abc.Messageable):
            await channel.send(f"<@{user_id}> \u2705 Azure VM has been deallocated.")


async def _create_console_channel(
    guild: discord.Guild, server_name: str, server_identifier: str,
    user: discord.Member | discord.User | None = None,
) -> discord.TextChannel | None:
    """Create an admin-only text channel for server console I/O."""
    # Sanitize name for Discord channel (lowercase, hyphens, max 100)
    channel_name = f"console-{server_name.lower().replace(' ', '-')[:80]}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    # Grant access to every role that has administrator
    for role in guild.roles:
        if role.permissions.administrator:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    # Also grant access to the user who started the session (server owner may not have an admin role)
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


async def _cleanup_console_channel() -> None:
    """Delete the console channel if it exists."""
    global active_session
    if not active_session:
        return
    ch_id = active_session.get("console_channel_id")
    if ch_id:
        ch = client.get_channel(ch_id)
        if ch:
            try:
                await ch.delete(reason="Server session ended.")
            except Exception:
                pass
        active_session["console_channel_id"] = None


def _start_session(
    duration_minutes: int,
    channel_id: int,
    user_id: int,
    guild_id: int | None = None,
    server_identifier: str | None = None,
    console_channel_id: int | None = None,
) -> None:
    global active_session
    # Cancel any existing session
    if active_session and active_session.get("task"):
        active_session["task"].cancel()

    loop = asyncio.get_event_loop()
    task = loop.create_task(_session_timer(duration_minutes, channel_id, user_id))
    active_session = {
        "task": task,
        "duration": duration_minutes,
        "channel_id": channel_id,
        "user_id": user_id,
        "guild_id": guild_id,
        "server_identifier": server_identifier,
        "console_channel_id": console_channel_id,
        "started_at": time.time(),
    }


class ExtendSessionView(discord.ui.View):
    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=300)
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
        _start_session(extra, interaction.channel_id or 0, self.user_id)
        await interaction.response.send_message(
            f"\u2705 Session extended by **{extra} minutes**. New timer started.",
            ephemeral=False,
        )
        self.stop()


def _session_remaining_str() -> str:
    """Return human-readable remaining time for the active session."""
    if not active_session or "started_at" not in active_session:
        return "No active session"
    elapsed = time.time() - active_session["started_at"]
    total = active_session["duration"] * 60
    remaining = max(total - elapsed, 0)
    mins = int(remaining // 60)
    if mins >= 60:
        h, m = divmod(mins, 60)
        return f"{h}h {m}m remaining"
    return f"{mins}m remaining"


async def _wait_for_panel(max_wait: int = 180, interval: int = 10) -> bool:
    """Poll the Pterodactyl panel until it responds or max_wait seconds pass."""
    elapsed = 0
    while elapsed < max_wait:
        try:
            await _ptero_client().list_servers()
            return True
        except Exception:
            await asyncio.sleep(interval)
            elapsed += interval
    return False


DURATION_OPTIONS = [
    discord.SelectOption(label="30 minutes", value="30"),
    discord.SelectOption(label="1 hour", value="60"),
    discord.SelectOption(label="2 hours", value="120"),
    discord.SelectOption(label="4 hours", value="240"),
    discord.SelectOption(label="Custom...", value="custom", description="Enter your own duration"),
]


class CustomDurationModal(discord.ui.Modal, title="Custom Duration"):
    """Modal that asks for a custom number of minutes."""
    minutes_input = discord.ui.TextInput(
        label="Duration in minutes",
        placeholder="e.g. 45",
        min_length=1,
        max_length=4,
        required=True,
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


async def _enrich_servers(servers: list[dict[str, Any]]) -> None:
    """Fetch Pterodactyl state and Minecraft query info for each server in parallel."""
    ptero = _ptero_client()

    async def _enrich_one(s: dict[str, Any]) -> None:
        try:
            resources = await ptero.get_server_resources(s["identifier"])
            s["state"] = str(resources.get("current_state", "unknown"))
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


class DurationSelect(discord.ui.View):
    """Shown when user starts the VM via /startserver."""
    def __init__(self, user_id: int, channel_id: int) -> None:
        super().__init__(timeout=120)
        self.user_id = user_id
        self.channel_id = channel_id

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="How long do you need the server?",
        options=DURATION_OPTIONS,
        row=0,
    )
    async def duration_callback(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:  # type: ignore[type-arg]
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the person who triggered this can choose.", ephemeral=True)
            return

        if select.values[0] == "custom":
            async def _run_custom(modal_interaction: discord.Interaction, minutes: int) -> None:
                await modal_interaction.response.defer(ephemeral=True)
                await modal_interaction.edit_original_response(
                    content=f"\u23f3 Starting Azure VM for **{minutes} minutes**...",
                    view=None,
                )
                compute_client.virtual_machines.begin_start(
                    AZURE_RESOURCE_GROUP, AZURE_VM_NAME
                ).result()
                _start_session(minutes, self.channel_id, self.user_id)
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
            content=f"\u23f3 Starting Azure VM for **{duration} minutes**...",
            view=None,
        )
        compute_client.virtual_machines.begin_start(
            AZURE_RESOURCE_GROUP, AZURE_VM_NAME
        ).result()

        _start_session(duration, self.channel_id, self.user_id)

        hours, mins = divmod(duration, 60)
        time_str = f"{hours}h {mins}m" if hours else f"{mins}m"
        await interaction.edit_original_response(
            content=f"\u2705 Azure VM started! Session length: **{time_str}**. "
            "You will be warned 5 minutes before auto-shutdown.",
            view=None,
        )


class ServerStartDurationView(discord.ui.View):
    """Duration picker shown when starting a game server from /mc.
    Starts Azure VM → waits for panel → starts the Pterodactyl server."""
    def __init__(self, user_id: int, channel_id: int, server_identifier: str, server_name: str,
                 server: dict[str, Any] | None = None, all_servers: list[dict[str, Any]] | None = None) -> None:
        super().__init__(timeout=120)
        self.user_id = user_id
        self.channel_id = channel_id
        self.server_identifier = server_identifier
        self.server_name = server_name
        self.server = server or {"name": server_name, "identifier": server_identifier}
        self.all_servers = all_servers or []

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="How long do you need the server?",
        options=DURATION_OPTIONS,
        row=0,
    )
    async def duration_callback(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:  # type: ignore[type-arg]
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the person who triggered this can choose.", ephemeral=True)
            return

        if select.values[0] == "custom":
            async def _run_custom(modal_interaction: discord.Interaction, minutes: int) -> None:
                await modal_interaction.response.edit_message(
                    embed=discord.Embed(
                        title=f"\u23f3  Starting Azure VM...",
                        description="Booting the underlying VM. This may take a minute.",
                        color=discord.Color.gold(),
                    ),
                    view=None,
                )
                await self._do_start(modal_interaction, minutes)
            await interaction.response.send_modal(CustomDurationModal(_run_custom))
            return

        duration = int(select.values[0])
        hours, mins = divmod(duration, 60)
        time_str = f"{hours}h {mins}m" if hours else f"{mins}m"

        # Step 1 — Start Azure VM
        await interaction.response.edit_message(
            embed=discord.Embed(
                title=f"\u23f3  Starting Azure VM ({time_str})...",
                description="Booting the underlying VM. This may take a minute.",
                color=discord.Color.gold(),
            ),
            view=None,
        )
        await self._do_start(interaction, duration)

    async def _do_start(self, interaction: discord.Interaction, duration: int) -> None:
        hours, mins = divmod(duration, 60)
        time_str = f"{hours}h {mins}m" if hours else f"{mins}m"

        compute_client.virtual_machines.begin_start(
            AZURE_RESOURCE_GROUP, AZURE_VM_NAME
        ).result()

        _start_session(duration, self.channel_id, self.user_id)

        # Step 2 — Wait for Pterodactyl panel
        await interaction.edit_original_response(
            embed=discord.Embed(
                title="\u23f3  Waiting for game panel to come online...",
                description="Azure VM is up. Waiting for the Pterodactyl panel to respond.",
                color=discord.Color.gold(),
            ),
        )
        panel_up = await _wait_for_panel()
        if not panel_up:
            nav = discord.ui.View(timeout=900)
            if self.all_servers:
                nav.add_item(BackButton(self.all_servers))
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="\u26a0\ufe0f  Panel did not respond",
                    description=f"Azure VM is running (session: **{time_str}**) but the panel didn't come up. "
                    "Try `/mc` again in a moment.",
                    color=discord.Color.orange(),
                ),
                view=nav if self.all_servers else None,
            )
            return

        # Step 3 — Start the game server
        await interaction.edit_original_response(
            embed=discord.Embed(
                title=f"\u23f3  Starting {self.server_name}...",
                description="Panel is up. Sending start signal to the game server.",
                color=discord.Color.gold(),
            ),
        )
        try:
            await _ptero_client().send_power_signal(self.server_identifier, "start")
        except Exception as exc:
            nav = discord.ui.View(timeout=900)
            if self.all_servers:
                nav.add_item(BackButton(self.all_servers))
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="\u274c  Could not start game server",
                    description=str(exc),
                    color=discord.Color.red(),
                ),
                view=nav if self.all_servers else None,
            )
            return

        await asyncio.sleep(3)
        try:
            resources = await _ptero_client().get_server_resources(self.server_identifier)
        except Exception:
            resources = {}
        state = str(resources.get("current_state", "unknown"))

        # Create console channel
        console_ch: discord.TextChannel | None = None
        if interaction.guild:
            try:
                console_ch = await _create_console_channel(
                    interaction.guild, self.server_name, self.server_identifier,
                    user=interaction.user,
                )
            except Exception as exc:
                print(f"Failed to create console channel: {exc}")

        # Update session with console channel + server info
        if active_session:
            active_session["server_identifier"] = self.server_identifier
            active_session["guild_id"] = interaction.guild_id
            if console_ch:
                active_session["console_channel_id"] = console_ch.id

        embed = _server_embed(self.server, state)
        if console_ch:
            embed.add_field(name="\U0001f5a5\ufe0f Console", value=console_ch.mention, inline=False)
        embed.set_footer(text=f"Session: {time_str} \u2022 Auto-shutdown enabled")

        action_view = ServerActionView(self.server, state, self.all_servers)
        await interaction.edit_original_response(embed=embed, view=action_view)


class QuickStartDurationView(discord.ui.View):
    """Duration picker when VM is already running. Starts game server and resets timer."""
    def __init__(self, user_id: int, channel_id: int, server_identifier: str, server_name: str,
                 server: dict[str, Any] | None = None, all_servers: list[dict[str, Any]] | None = None) -> None:
        super().__init__(timeout=120)
        self.user_id = user_id
        self.channel_id = channel_id
        self.server_identifier = server_identifier
        self.server_name = server_name
        self.server = server or {"name": server_name, "identifier": server_identifier}
        self.all_servers = all_servers or []

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="How long do you need the server?",
        options=DURATION_OPTIONS,
        row=0,
    )
    async def duration_callback(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:  # type: ignore[type-arg]
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the person who triggered this can choose.", ephemeral=True)
            return

        if select.values[0] == "custom":
            async def _run_custom(modal_interaction: discord.Interaction, minutes: int) -> None:
                await modal_interaction.response.edit_message(
                    embed=discord.Embed(
                        title=f"\u23f3  Starting {self.server_name}...",
                        description="Sending start signal to the game server.",
                        color=discord.Color.gold(),
                    ),
                    view=None,
                )
                await self._do_start(modal_interaction, minutes)
            await interaction.response.send_modal(CustomDurationModal(_run_custom))
            return

        duration = int(select.values[0])
        hours, mins = divmod(duration, 60)
        time_str = f"{hours}h {mins}m" if hours else f"{mins}m"

        await interaction.response.edit_message(
            embed=discord.Embed(
                title=f"\u23f3  Starting {self.server_name}...",
                description="Sending start signal to the game server.",
                color=discord.Color.gold(),
            ),
            view=None,
        )
        await self._do_start(interaction, duration)

    async def _do_start(self, interaction: discord.Interaction, duration: int) -> None:
        hours, mins = divmod(duration, 60)
        time_str = f"{hours}h {mins}m" if hours else f"{mins}m"

        # Reset timer
        _start_session(duration, self.channel_id, self.user_id,
                        guild_id=interaction.guild_id,
                        server_identifier=self.server_identifier)

        try:
            await _ptero_client().send_power_signal(self.server_identifier, "start")
        except Exception as exc:
            nav = discord.ui.View(timeout=900)
            if self.all_servers:
                nav.add_item(BackButton(self.all_servers))
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="\u274c  Could not start game server",
                    description=str(exc),
                    color=discord.Color.red(),
                ),
                view=nav if self.all_servers else None,
            )
            return

        await asyncio.sleep(3)
        try:
            resources = await _ptero_client().get_server_resources(self.server_identifier)
        except Exception:
            resources = {}
        state = str(resources.get("current_state", "unknown"))

        # Create console channel
        console_ch: discord.TextChannel | None = None
        if interaction.guild:
            try:
                console_ch = await _create_console_channel(
                    interaction.guild, self.server_name, self.server_identifier,
                    user=interaction.user,
                )
            except Exception as exc:
                print(f"Failed to create console channel: {exc}")

        if active_session:
            active_session["server_identifier"] = self.server_identifier
            active_session["guild_id"] = interaction.guild_id
            if console_ch:
                active_session["console_channel_id"] = console_ch.id

        embed = _server_embed(self.server, state)
        if console_ch:
            embed.add_field(name="\U0001f5a5\ufe0f Console", value=console_ch.mention, inline=False)
        embed.set_footer(text=f"Session: {time_str} \u2022 Auto-shutdown enabled")

        action_view = ServerActionView(self.server, state, self.all_servers)
        await interaction.edit_original_response(embed=embed, view=action_view)


@tree.command(name="statusserver", description="Show Azure VM power status")
async def statusserver(interaction: discord.Interaction) -> None:
    if not await _check_auth(interaction):
        return

    await interaction.response.defer(thinking=True)
    state = _vm_power_state()
    await interaction.followup.send(f"`{AZURE_VM_NAME}` status: `{state}`")


@tree.command(name="startserver", description="Start the Azure VM with a timed session")
async def startserver(interaction: discord.Interaction) -> None:
    if not await _check_auth(interaction):
        return

    view = DurationSelect(interaction.user.id, interaction.channel_id or 0)
    await interaction.response.send_message(
        "\u23f1\ufe0f Choose how long you need the Azure VM:",
        view=view,
        ephemeral=True,
    )


@tree.command(name="stopserver", description="Stop (deallocate) the Azure VM")
async def stopserver(interaction: discord.Interaction) -> None:
    global active_session
    if not await _check_auth(interaction):
        return

    await interaction.response.defer(thinking=True)
    # Cancel active session timer and clean up console channel
    if active_session:
        if active_session.get("task"):
            active_session["task"].cancel()
        await _cleanup_console_channel()
        active_session = None

    compute_client.virtual_machines.begin_deallocate(
        AZURE_RESOURCE_GROUP,
        AZURE_VM_NAME,
    ).result()
    state = _vm_power_state()
    await interaction.followup.send(f"Stopped `{AZURE_VM_NAME}` (deallocated). Current status: `{state}`")


STATUS_COLORS = {
    "running": discord.Color.green(),
    "starting": discord.Color.gold(),
    "stopping": discord.Color.orange(),
    "offline": discord.Color.red(),
    "unknown": discord.Color.greyple(),
}

STATUS_EMOJI = {
    "running": "\U0001f7e2",   # green circle
    "starting": "\U0001f7e1",  # yellow circle
    "stopping": "\U0001f7e0",  # orange circle
    "offline": "\U0001f534",   # red circle
}


def _server_embed(server: dict[str, Any], state: str) -> discord.Embed:
    color = STATUS_COLORS.get(state, STATUS_COLORS["unknown"])
    emoji = STATUS_EMOJI.get(state, "\u2753")
    embed = discord.Embed(
        title=f"{emoji}  {server['name']}",
        color=color,
    )
    desc_parts = []
    if active_session:
        desc_parts.append(f"\u23f1\ufe0f **{_session_remaining_str()}**")
    if server.get("description"):
        desc_parts.append(server["description"])
    if desc_parts:
        embed.description = "\n".join(desc_parts)

    address = ""
    if server.get("ip") and server.get("port"):
        address = f"`{server['ip']}:{server['port']}`"
    elif server.get("ip"):
        address = f"`{server['ip']}`"

    embed.add_field(name="Status", value=f"{emoji} `{state}`", inline=True)
    if address:
        embed.add_field(name="Address", value=address, inline=True)
    embed.add_field(name="ID", value=f"`{server['identifier']}`", inline=True)
    if server.get("node"):
        embed.add_field(name="Node", value=server["node"], inline=True)

    embed.timestamp = discord.utils.utcnow()
    return embed


class ServerSelect(discord.ui.Select["McView"]):
    def __init__(self, servers: list[dict[str, Any]]) -> None:
        options = [
            discord.SelectOption(
                label=s["name"][:100],
                value=s["identifier"],
                description=f"ID: {s['identifier']}"[:100],
            )
            for s in servers[:25]
        ]
        super().__init__(placeholder="Select a server...", options=options, row=0)
        self.servers = {s["identifier"]: s for s in servers}
        self.servers_list = servers

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        identifier = self.values[0]
        server = self.servers.get(identifier)
        if not server:
            await interaction.response.send_message("Server not found.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            resources = await _ptero_client().get_server_resources(identifier)
        except Exception:
            resources = {}

        state = str(resources.get("current_state", "unknown"))
        embed = _server_embed(server, state)
        view = ServerActionView(server, state, self.servers_list)
        await interaction.edit_original_response(embed=embed, view=view)


class ServerActionView(discord.ui.View):
    def __init__(self, server: dict[str, Any], state: str, all_servers: list[dict[str, Any]] | None = None) -> None:
        super().__init__(timeout=900)
        self.server = server
        self.identifier = server["identifier"]
        self.all_servers = all_servers or []

        if state in ("offline", "unknown"):
            self.add_item(PowerButton(self.identifier, "start", discord.ButtonStyle.green, "\u25B6 Start"))
        if state == "running":
            self.add_item(PowerButton(self.identifier, "restart", discord.ButtonStyle.blurple, "\U0001f504 Restart"))
            self.add_item(PowerButton(self.identifier, "stop", discord.ButtonStyle.red, "\u23F9 Stop"))
        if state in ("starting", "stopping"):
            self.add_item(PowerButton(self.identifier, "kill", discord.ButtonStyle.danger, "\u26A0 Kill"))

        self.add_item(RefreshButton(self.server))
        if self.all_servers:
            self.add_item(BackButton(self.all_servers))


class PowerButton(discord.ui.Button["ServerActionView"]):
    def __init__(self, identifier: str, signal: str, style: discord.ButtonStyle, label: str) -> None:
        super().__init__(style=style, label=label, row=1)
        self.identifier = identifier
        self.signal = signal

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        server = self.view.server if self.view else {}

        # Always show duration picker when starting a server
        if self.signal == "start":
            all_servers = self.view.all_servers if self.view else []
            if active_session:
                # VM already running — just pick duration, start server, reset timer
                view = QuickStartDurationView(
                    interaction.user.id,
                    interaction.channel_id or 0,
                    self.identifier,
                    server.get("name", "server"),
                    server=server,
                    all_servers=all_servers,
                )
                await interaction.response.edit_message(
                    embed=discord.Embed(
                        title="\u23f1\ufe0f  Choose session duration",
                        description="The VM is already running. Pick how long you need the server.",
                        color=discord.Color.blurple(),
                    ),
                    view=view,
                )
            else:
                # VM is off — boot VM → wait for panel → start server
                view = ServerStartDurationView(
                    interaction.user.id,
                    interaction.channel_id or 0,
                    self.identifier,
                    server.get("name", "server"),
                    server=server,
                    all_servers=all_servers,
                )
                await interaction.response.edit_message(
                    embed=discord.Embed(
                        title="\u23f1\ufe0f  Choose session duration",
                        description="The Azure VM will start, then the game server will boot automatically.",
                        color=discord.Color.blurple(),
                    ),
                    view=view,
                )
            return

        signal_labels = {"start": "Starting", "stop": "Stopping", "restart": "Restarting", "kill": "Killing"}
        action_label = signal_labels.get(self.signal, self.signal.title())

        # Immediately show feedback
        pending_embed = discord.Embed(
            title=f"\u23f3  {action_label} {server.get('name', 'server')}...",
            description="Please wait, this may take a moment.",
            color=discord.Color.gold(),
        )
        await interaction.response.edit_message(embed=pending_embed, view=None)

        try:
            await _ptero_client().send_power_signal(self.identifier, self.signal)
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
                nav.add_item(BackButton(all_servers))
            await interaction.edit_original_response(embed=error_embed, view=nav if all_servers else None)
            return

        # Brief pause then refresh status
        await asyncio.sleep(2)

        try:
            resources = await _ptero_client().get_server_resources(self.identifier)
        except Exception:
            resources = {}

        state = str(resources.get("current_state", "unknown"))

        # After stop/kill: check if any server is still running — if not, shut down Azure VM
        if self.signal in ("stop", "kill") and active_session:
            try:
                all_srv = await _ptero_client().list_servers()
                any_running = False
                for s in all_srv:
                    try:
                        res = await _ptero_client().get_server_resources(s["identifier"])
                        s_state = str(res.get("current_state", "offline"))
                    except Exception:
                        s_state = "offline"
                    if s_state in ("running", "starting"):
                        any_running = True
                        break

                if not any_running:
                    # No servers running — auto-shutdown Azure VM
                    embed = _server_embed(server, state)
                    embed.add_field(
                        name="\U0001f6d1 Auto-shutdown",
                        value="No game servers are running. Deallocating Azure VM...",
                        inline=False,
                    )
                    await interaction.edit_original_response(embed=embed, view=None)

                    if active_session.get("task"):
                        active_session["task"].cancel()
                    await _cleanup_console_channel()
                    await _shutdown_vm()
                    active_session = None

                    embed = _server_embed(server, state)
                    embed.add_field(
                        name="\u2705 VM Deallocated",
                        value="All game servers were offline — Azure VM has been shut down to save costs.",
                        inline=False,
                    )
                    all_servers = self.view.all_servers if self.view else []
                    nav = discord.ui.View(timeout=900)
                    if all_servers:
                        nav.add_item(BackButton(all_servers))
                    await interaction.edit_original_response(embed=embed, view=nav if all_servers else None)
                    return
            except Exception:
                pass  # If check fails, just show normal result

        embed = _server_embed(server, state)
        new_view = ServerActionView(server, state, self.view.all_servers if self.view else [])
        await interaction.edit_original_response(embed=embed, view=new_view)


class RefreshButton(discord.ui.Button["ServerActionView"]):
    def __init__(self, server: dict[str, Any]) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="\U0001f504 Refresh", row=1)
        self.server = server

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            resources = await _ptero_client().get_server_resources(self.server["identifier"])
        except Exception:
            resources = {}

        state = str(resources.get("current_state", "unknown"))
        embed = _server_embed(self.server, state)
        new_view = ServerActionView(self.server, state, self.view.all_servers if self.view else [])
        await interaction.edit_original_response(embed=embed, view=new_view)


class BackButton(discord.ui.Button["ServerActionView"]):
    def __init__(self, servers: list[dict[str, Any]]) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="\u25c0 Back", row=2)
        self.servers = servers

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        # Re-enrich servers for fresh status
        try:
            await _enrich_servers(self.servers)
        except Exception:
            pass
        embed = _mc_list_embed(self.servers)
        view = McView(self.servers)
        await interaction.response.edit_message(embed=embed, view=view)


class McView(discord.ui.View):
    def __init__(self, servers: list[dict[str, Any]]) -> None:
        super().__init__(timeout=900)
        self.add_item(ServerSelect(servers))


def _mc_list_embed(servers: list[dict[str, Any]], from_cache: bool = False) -> discord.Embed:
    """Build the main /mc server list embed with session timer."""
    desc = "Select a server from the dropdown below to view details and controls."
    if from_cache:
        desc += "\n\u26a0\ufe0f *Panel unreachable \u2014 showing cached server list.*"

    embed = discord.Embed(
        title="\U0001f3ae  Minecraft Server Panel",
        description=desc,
        color=discord.Color.dark_green(),
    )

    if active_session:
        timer_str = _session_remaining_str()
        embed.add_field(name="\u23f1\ufe0f Session", value=f"**{timer_str}**", inline=False)
    else:
        embed.add_field(name="\u23f1\ufe0f Session", value="No active session", inline=False)

    lines: list[str] = []
    for i, s in enumerate(servers[:10], 1):
        state = s.get("state", "unknown")
        emoji = STATUS_EMOJI.get(state, "\u26ab")
        address = f"{s['ip']}:{s['port']}" if s.get("ip") and s.get("port") else "\u2014"

        parts = [f"`{i}.` {emoji} **{s['name']}**"]
        if state == "running":
            players = f"{s.get('players_online', '?')}/{s.get('players_max', '?')}"
            parts.append(f"\U0001f465 {players}")
            if s.get("mc_version"):
                parts.append(f"v{s['mc_version']}")
        else:
            parts.append("Offline")
        parts.append(f"`{address}`")
        lines.append(" \u2014 ".join(parts))
    embed.add_field(name="Servers", value="\n".join(lines) or "No servers", inline=False)

    embed.timestamp = discord.utils.utcnow()
    return embed


@tree.command(name="mc", description="Manage your Minecraft servers")
async def mc(interaction: discord.Interaction) -> None:
    if not await _check_auth(interaction):
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    try:
        servers, from_cache = await _fetch_servers_with_cache()
    except Exception as exc:
        await interaction.followup.send(f"Could not fetch servers: {exc}", ephemeral=True)
        return

    if not servers:
        await interaction.followup.send("No servers found on the panel.", ephemeral=True)
        return

    # Auto-start a 15-minute session if VM is reachable but no timer is set
    if not active_session and not from_cache:
        _start_session(15, interaction.channel_id or 0, interaction.user.id,
                        guild_id=interaction.guild_id)

    # Enrich servers with status, player count, and version
    if not from_cache:
        try:
            await _enrich_servers(servers)
        except Exception:
            pass

    embed = _mc_list_embed(servers, from_cache)
    view = McView(servers)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@client.event
async def on_ready() -> None:
    await tree.sync()
    print(f"Logged in as {client.user} and synced slash commands.")


@client.event
async def on_message(message: discord.Message) -> None:
    # Ignore bot's own messages
    if message.author == client.user or message.author.bot:
        return

    # Check if this message is in the active console channel
    if (
        active_session
        and active_session.get("console_channel_id")
        and message.channel.id == active_session["console_channel_id"]
        and active_session.get("server_identifier")
    ):
        cmd_text = message.content.strip()
        if not cmd_text:
            return
        try:
            await _ptero_client().send_command(
                active_session["server_identifier"], cmd_text
            )
            await message.add_reaction("\u2705")
        except Exception as exc:
            await message.reply(f"\u274c Could not send command: {exc}", delete_after=10)


@client.event
async def on_error(event: str, *args, **kwargs) -> None:
    print(f"Unhandled Discord error in event: {event}")
    print(traceback.format_exc())


if __name__ == "__main__":
    client.run(DISCORD_BOT_TOKEN)
