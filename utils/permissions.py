"""Permission checks for Discord interactions."""

from __future__ import annotations

import discord


def is_authorized(interaction: discord.Interaction) -> bool:
    """Return True if the user has administrator permissions in the guild."""
    if interaction.guild is None:
        return False
    perms = interaction.user.guild_permissions  # type: ignore[union-attr]
    return perms.administrator


async def check_auth(interaction: discord.Interaction) -> bool:
    """Check authorization and send denial message if unauthorized."""
    if is_authorized(interaction):
        return True
    msg = "You are not authorized to run this command."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)
    return False
