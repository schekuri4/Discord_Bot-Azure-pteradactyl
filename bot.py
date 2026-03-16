import os
import traceback
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
DISCORD_ADMIN_USER_ID = os.getenv("DISCORD_ADMIN_USER_ID")
PTERODACTYL_PANEL_URL = os.getenv("PTERODACTYL_PANEL_URL")
PTERODACTYL_API_KEY = os.getenv("PTERODACTYL_API_KEY")

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
        timeout = aiohttp.ClientTimeout(total=30)

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
    if not DISCORD_ADMIN_USER_ID:
        return True
    return str(interaction.user.id) == DISCORD_ADMIN_USER_ID


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


@tree.command(name="statusserver", description="Show Azure VM power status")
async def statusserver(interaction: discord.Interaction) -> None:
    if not await _check_auth(interaction):
        return

    await interaction.response.defer(thinking=True)
    state = _vm_power_state()
    await interaction.followup.send(f"`{AZURE_VM_NAME}` status: `{state}`")


@tree.command(name="startserver", description="Start the Azure VM")
async def startserver(interaction: discord.Interaction) -> None:
    if not await _check_auth(interaction):
        return

    await interaction.response.defer(thinking=True)
    compute_client.virtual_machines.begin_start(
        AZURE_RESOURCE_GROUP,
        AZURE_VM_NAME,
    ).result()
    state = _vm_power_state()
    await interaction.followup.send(f"Started `{AZURE_VM_NAME}`. Current status: `{state}`")


@tree.command(name="stopserver", description="Stop (deallocate) the Azure VM")
async def stopserver(interaction: discord.Interaction) -> None:
    if not await _check_auth(interaction):
        return

    await interaction.response.defer(thinking=True)
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

        await interaction.response.defer(ephemeral=True)

        try:
            await _ptero_client().send_power_signal(self.identifier, self.signal)
        except Exception as exc:
            await interaction.edit_original_response(
                content=f"Failed to send `{self.signal}`: {exc}",
                embed=None,
                view=None,
            )
            return

        # Refresh status after action
        try:
            resources = await _ptero_client().get_server_resources(self.identifier)
        except Exception:
            resources = {}

        state = str(resources.get("current_state", "unknown"))
        server = self.view.server if self.view else {}
        embed = _server_embed(server, state)
        new_view = ServerActionView(server, state)
        await interaction.edit_original_response(
            content=f"`{self.signal}` signal sent.",
            embed=embed,
            view=new_view,
        )


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
        servers = await _ptero_client().list_servers()
    except Exception as exc:
        await interaction.followup.send(f"Could not fetch servers: {exc}", ephemeral=True)
        return

    if not servers:
        await interaction.followup.send("No servers found on the panel.", ephemeral=True)
        return

    embed = discord.Embed(
        title="\U0001f3ae  Minecraft Server Panel",
        description="Select a server from the dropdown below to view details and controls.",
        color=discord.Color.dark_green(),
    )
    for s in servers[:10]:
        address = f"`{s['ip']}:{s['port']}`" if s.get("ip") and s.get("port") else "N/A"
        embed.add_field(
            name=s["name"],
            value=f"ID: `{s['identifier']}`\nAddress: {address}",
            inline=True,
        )

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
