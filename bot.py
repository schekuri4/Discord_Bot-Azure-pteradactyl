import asyncio
import json
import os
import traceback
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv
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
active_session: dict[str, Any] | None = None  # {task, end_time, channel_id, user_id}

credential = ClientSecretCredential(
    tenant_id=AZURE_TENANT_ID,
    client_id=AZURE_CLIENT_ID,
    client_secret=AZURE_CLIENT_SECRET,
)
compute_client = ComputeManagementClient(credential, AZURE_SUBSCRIPTION_ID)

intents = discord.Intents.default()
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
        await _shutdown_vm()
        active_session = None
        if channel and isinstance(channel, discord.abc.Messageable):
            await channel.send(f"<@{user_id}> \u2705 Azure VM has been deallocated.")


def _start_session(duration_minutes: int, channel_id: int, user_id: int) -> None:
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
]


class DurationSelect(discord.ui.View):
    """Shown when user starts the VM via /startserver."""
    def __init__(self, user_id: int, channel_id: int) -> None:
        super().__init__(timeout=60)
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
    def __init__(self, user_id: int, channel_id: int, server_identifier: str, server_name: str) -> None:
        super().__init__(timeout=60)
        self.user_id = user_id
        self.channel_id = channel_id
        self.server_identifier = server_identifier
        self.server_name = server_name

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
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="\u26a0\ufe0f  Panel did not respond",
                    description=f"Azure VM is running (session: **{time_str}**) but the panel didn't come up. "
                    "Try `/mc` again in a moment.",
                    color=discord.Color.orange(),
                ),
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
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="\u274c  Could not start game server",
                    description=str(exc),
                    color=discord.Color.red(),
                ),
            )
            return

        await asyncio.sleep(3)
        try:
            resources = await _ptero_client().get_server_resources(self.server_identifier)
        except Exception:
            resources = {}
        state = str(resources.get("current_state", "unknown"))

        await interaction.edit_original_response(
            embed=discord.Embed(
                title=f"\u2705  {self.server_name} — {state}",
                description=f"Session: **{time_str}**. You'll be warned 5 min before auto-shutdown.",
                color=STATUS_COLORS.get(state, discord.Color.green()),
            ),
        )


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
    # Cancel active session timer if any
    if active_session and active_session.get("task"):
        active_session["task"].cancel()
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
    if server.get("description"):
        embed.description = server["description"]

    address = ""
    if server.get("ip") and server.get("port"):
        address = f"`{server['ip']}:{server['port']}`"
    elif server.get("ip"):
        address = f"`{server['ip']}`"

    embed.add_field(name="Status", value=f"`{state}`", inline=True)
    if address:
        embed.add_field(name="Address", value=address, inline=True)
    embed.add_field(name="ID", value=f"`{server['identifier']}`", inline=True)
    if server.get("node"):
        embed.add_field(name="Node", value=server["node"], inline=True)

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
        view = ServerActionView(server, state)
        await interaction.edit_original_response(embed=embed, view=view)


class ServerActionView(discord.ui.View):
    def __init__(self, server: dict[str, Any], state: str) -> None:
        super().__init__(timeout=120)
        self.server = server
        self.identifier = server["identifier"]

        if state in ("offline", "unknown"):
            self.add_item(PowerButton(self.identifier, "start", discord.ButtonStyle.green, "\u25B6 Start"))
        if state == "running":
            self.add_item(PowerButton(self.identifier, "restart", discord.ButtonStyle.blurple, "\U0001f504 Restart"))
            self.add_item(PowerButton(self.identifier, "stop", discord.ButtonStyle.red, "\u23F9 Stop"))
        if state in ("starting", "stopping"):
            self.add_item(PowerButton(self.identifier, "kill", discord.ButtonStyle.danger, "\u26A0 Kill"))

        self.add_item(RefreshButton(self.server))


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

        # If starting and there's no active session, show the duration picker
        # which will boot the Azure VM → wait for panel → start game server
        if self.signal == "start" and not active_session:
            view = ServerStartDurationView(
                interaction.user.id,
                interaction.channel_id or 0,
                self.identifier,
                server.get("name", "server"),
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
            await interaction.edit_original_response(embed=error_embed, view=None)
            return

        # Brief pause then refresh status
        await asyncio.sleep(2)

        try:
            resources = await _ptero_client().get_server_resources(self.identifier)
        except Exception:
            resources = {}

        state = str(resources.get("current_state", "unknown"))
        embed = _server_embed(server, state)
        new_view = ServerActionView(server, state)
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
        new_view = ServerActionView(self.server, state)
        await interaction.edit_original_response(embed=embed, view=new_view)


class McView(discord.ui.View):
    def __init__(self, servers: list[dict[str, Any]]) -> None:
        super().__init__(timeout=180)
        self.add_item(ServerSelect(servers))


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

    desc = "Select a server from the dropdown below to view details and controls."
    if from_cache:
        desc += "\n\u26a0\ufe0f *Panel unreachable — showing cached server list.*"

    embed = discord.Embed(
        title="\U0001f3ae  Minecraft Server Panel",
        description=desc,
        color=discord.Color.dark_green(),
    )
    # Clean list format
    lines: list[str] = []
    for i, s in enumerate(servers[:10], 1):
        address = f"{s['ip']}:{s['port']}" if s.get("ip") and s.get("port") else "—"
        lines.append(f"`{i}.` **{s['name']}** — `{address}`")
    embed.add_field(name="Servers", value="\n".join(lines), inline=False)

    view = McView(servers)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@client.event
async def on_ready() -> None:
    await tree.sync()
    print(f"Logged in as {client.user} and synced slash commands.")


@client.event
async def on_error(event: str, *args, **kwargs) -> None:
    print(f"Unhandled Discord error in event: {event}")
    print(traceback.format_exc())


if __name__ == "__main__":
    client.run(DISCORD_BOT_TOKEN)
