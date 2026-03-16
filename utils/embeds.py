"""Reusable embed builders for the Minecraft server bot."""

from __future__ import annotations

import time
from typing import Any

import discord

STATUS_COLORS = {
    "running": discord.Color.green(),
    "starting": discord.Color.gold(),
    "stopping": discord.Color.orange(),
    "offline": discord.Color.red(),
    "unknown": discord.Color.greyple(),
}

STATUS_EMOJI = {
    "running": "\U0001f7e2",
    "starting": "\U0001f7e1",
    "stopping": "\U0001f7e0",
    "offline": "\U0001f534",
}


def server_embed(server: dict[str, Any], state: str, session_info: str | None = None) -> discord.Embed:
    """Build a detail embed for one server."""
    color = STATUS_COLORS.get(state, STATUS_COLORS["unknown"])
    emoji = STATUS_EMOJI.get(state, "\u2753")
    embed = discord.Embed(title=f"{emoji}  {server['name']}", color=color)

    desc_parts: list[str] = []
    if session_info:
        desc_parts.append(f"\u23f1\ufe0f **{session_info}**")
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

    # Player info if available
    if server.get("players_online") is not None:
        embed.add_field(
            name="Players",
            value=f"{server['players_online']}/{server.get('players_max', '?')}",
            inline=True,
        )
    if server.get("mc_version"):
        embed.add_field(name="Version", value=server["mc_version"], inline=True)

    embed.timestamp = discord.utils.utcnow()
    return embed


def server_list_embed(
    servers: list[dict[str, Any]],
    session_info: str | None = None,
    from_cache: bool = False,
) -> discord.Embed:
    """Build the main /mc server list embed."""
    desc = "Select a server from the dropdown below to view details and controls."
    if from_cache:
        desc += "\n\u26a0\ufe0f *Panel unreachable — showing cached server list.*"

    embed = discord.Embed(
        title="\U0001f3ae  Minecraft Server Panel",
        description=desc,
        color=discord.Color.dark_green(),
    )

    if session_info:
        embed.add_field(name="\u23f1\ufe0f Session", value=f"**{session_info}**", inline=False)
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


def backup_embed(backups: list[dict[str, Any]], server_name: str) -> discord.Embed:
    """Build a backup list embed."""
    embed = discord.Embed(
        title=f"\U0001f4be  Backups — {server_name}",
        color=discord.Color.blue(),
    )
    if not backups:
        embed.description = "No backups found."
        return embed

    lines: list[str] = []
    for i, b in enumerate(backups[:10], 1):
        name = b.get("name") or "Unnamed"
        uuid = b.get("uuid", "?")[:8]
        size_mb = (b.get("bytes", 0) or 0) / 1_048_576
        completed = b.get("is_successful", False)
        status = "\u2705" if completed else "\u23f3"
        created = b.get("created_at", "?")[:10]
        lines.append(f"`{i}.` {status} **{name}** — {size_mb:.1f} MB — {created} (`{uuid}`)")

    embed.description = "\n".join(lines)
    embed.timestamp = discord.utils.utcnow()
    return embed


def mod_list_embed(files: list[dict[str, Any]], server_name: str, directory: str) -> discord.Embed:
    """Build a mod/plugin file list embed."""
    label = "Plugins" if "plugin" in directory.lower() else "Mods"
    embed = discord.Embed(
        title=f"\U0001f9e9  {label} — {server_name}",
        color=discord.Color.purple(),
    )
    if not files:
        embed.description = f"No {label.lower()} found in `{directory}`."
        return embed

    lines: list[str] = []
    for i, f in enumerate(files[:20], 1):
        name = f.get("name", "?")
        size_mb = (f.get("size", 0) or 0) / 1_048_576
        modified = str(f.get("modified_at", ""))[:10]
        is_disabled = name.endswith(".disabled")
        status = "\u26ab" if is_disabled else "\U0001f7e2"
        lines.append(f"`{i}.` {status} `{name}` — {size_mb:.1f} MB — {modified}")

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Directory: {directory}")
    embed.timestamp = discord.utils.utcnow()
    return embed
