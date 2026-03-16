"""Console channel message forwarding and log viewing."""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from bot import MCBot


class ConsoleCog(commands.Cog):
    def __init__(self, bot: MCBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.bot.user or message.author.bot:
            return

        session = self.bot.active_session
        if (
            session
            and session.get("console_channel_id")
            and message.channel.id == session["console_channel_id"]
            and session.get("server_identifier")
        ):
            cmd_text = message.content.strip()
            if not cmd_text:
                return
            try:
                await self.bot.ptero_client().send_command(
                    session["server_identifier"], cmd_text,
                )
                await message.add_reaction("\u2705")
            except Exception as exc:
                await message.reply(f"\u274c Could not send command: {exc}", delete_after=10)

    @commands.Cog.listener()
    async def on_error(self, event: str, *args, **kwargs) -> None:
        print(f"Unhandled Discord error in event: {event}")
        print(traceback.format_exc())


async def setup(bot: MCBot) -> None:
    await bot.add_cog(ConsoleCog(bot))
