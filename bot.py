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

    async def list_servers(self) -> list[dict[str, str]]:
        # Client API lists servers the authenticated user has access to.
        url = f"{self.panel_url}/api/client?per_page=100"
        servers: list[dict[str, str]] = []
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
                        if identifier:
                            servers.append(
                                {
                                    "name": name,
                                    "identifier": identifier,
                                    "uuid": uuid,
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

    async def start_server(self, identifier: str) -> None:
        # Client power endpoint uses server identifier and can start the server instance.
        url = f"{self.panel_url}/api/client/servers/{identifier}/power"
        payload = {"signal": "start"}
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), json=payload, timeout=timeout) as response:
                if response.status in (200, 202, 204):
                    return

                body = await response.text()
                if response.status in (401, 403):
                    raise PterodactylApiError(
                        "Pterodactyl rejected power control. Use a client API key (prefix ptlc_)"
                        " for /api/client endpoints, or adjust panel API permissions."
                    )
                raise PterodactylApiError(
                    f"Pterodactyl start failed ({response.status}): {body[:300]}"
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


@tree.command(name="mcservers", description="List servers from your Pterodactyl panel")
async def mcservers(interaction: discord.Interaction) -> None:
    if not await _check_auth(interaction):
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    try:
        servers = await _ptero_client().list_servers()
    except Exception as exc:
        await interaction.followup.send(f"Could not fetch Pterodactyl servers: {exc}", ephemeral=True)
        return

    if not servers:
        await interaction.followup.send("No Pterodactyl servers found.", ephemeral=True)
        return

    lines = [
        f"{index}. `{server['name']}` - `{server['identifier']}`"
        for index, server in enumerate(servers[:25], start=1)
    ]
    await interaction.followup.send(
        "Use `/startmcserver` and pick one from autocomplete.\n\n" + "\n".join(lines),
        ephemeral=True,
    )


async def _ptero_server_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if not _is_authorized(interaction):
        return []

    try:
        servers = await _ptero_client().list_servers()
    except Exception:
        return []

    query = current.lower().strip()
    filtered: list[dict[str, str]] = []
    for server in servers:
        haystack = f"{server['name']} {server['identifier']} {server['uuid']}".lower()
        if not query or query in haystack:
            filtered.append(server)

    return [
        app_commands.Choice(
            name=f"{server['name']} ({server['identifier']})"[:100],
            value=server["identifier"],
        )
        for server in filtered[:25]
    ]


@tree.command(name="startmcserver", description="Start a selected Pterodactyl server")
@app_commands.describe(server_identifier="Choose a server identifier")
@app_commands.autocomplete(server_identifier=_ptero_server_autocomplete)
async def startmcserver(interaction: discord.Interaction, server_identifier: str) -> None:
    if not await _check_auth(interaction):
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    try:
        await _ptero_client().start_server(server_identifier)
    except Exception as exc:
        await interaction.followup.send(
            f"Could not start `{server_identifier}`: {exc}",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"Start signal sent for `{server_identifier}`.",
        ephemeral=True,
    )


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
