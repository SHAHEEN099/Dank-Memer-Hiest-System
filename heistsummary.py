import discord
from discord.ext import commands
from discord import app_commands
import re
import asyncio
from typing import Optional, List, Tuple
from datetime import datetime, timezone
import logging

# -------------------------------------------------------------------------
# LOGGER INITIALIZATION
# -------------------------------------------------------------------------
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

# -------------------------------------------------------------------------
# HELPER FUNCTIONS (parsing, etc.)
# -------------------------------------------------------------------------
def parse_currency_value(raw: str) -> Optional[float]:
    """
    Parses strings like '⏣ 5', '⏣ 1.5M', etc. and returns a float.
    """
    if not raw:
        return None
    text = raw.replace("⏣", "").replace(",", "").strip()
    match = re.match(r"([\d\.]+)([kKmMbB]?)", text)
    if not match:
        return None
    num_str, suffix = match.groups()
    suffix = suffix.upper()
    try:
        base_val = float(num_str)
    except ValueError:
        return None

    if suffix == "K":
        base_val *= 1_000
    elif suffix == "M":
        base_val *= 1_000_000
    elif suffix == "B":
        base_val *= 1_000_000_000
    return base_val

def parse_plain_number(raw: str) -> Optional[float]:
    """
    Parses a plain numeric value (like '2' from '2 users got the payout').
    """
    if not raw:
        return None
    text = raw.replace(",", "").strip()
    match = re.match(r"([\d\.]+)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None

def format_number(val: float) -> str:
    """
    Formats a float with commas, removing trailing .00 if present.
    E.g. 1500 -> "1,500"
    """
    formatted = f"{val:,.2f}"
    if formatted.endswith(".00"):
        return formatted[:-3]
    return formatted.rstrip("0").rstrip(".")

def abbreviate_number(val: float) -> str:
    """
    Abbreviates a float (K, M, B). Example: 1234 -> 1.23K, 1.23M, etc.
    """
    if val >= 1_000_000_000:
        s = f"{val/1_000_000_000:.2f}"
        return (s[:-3] if s.endswith(".00") else s) + "B"
    elif val >= 1_000_000:
        s = f"{val/1_000_000:.2f}"
        return (s[:-3] if s.endswith(".00") else s) + "M"
    elif val >= 1_000:
        s = f"{val/1_000:.2f}"
        return (s[:-3] if s.endswith(".00") else s) + "K"
    else:
        return format_number(val)

DANK_MEMER_ID = 270904126974590976

# -------------------------------------------------------------------------
# THE COG
# -------------------------------------------------------------------------
class HeistSummaryCog(commands.Cog):
    """
    A cog that provides a summary command (slash & prefix) to scan the last
    100 messages in the current channel for Dank Memer payout messages,
    then label each found message with a custom name.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------------------------------------------------------------
    # UTILITY: SCAN LAST 100 MESSAGES FOR HEIST PAYOUTS
    # ---------------------------------------------------------------------
    async def get_recent_heist_messages(self, channel: discord.TextChannel, limit: int) -> List[Tuple[discord.Message, float, int]]:
        """
        Reads up to the last 100 messages in 'channel' (newest first),
        finds messages from Dank Memer that contain the "amazing job everybody"
        text, and extracts (message, total_coins, payout_count).

        Returns a list of up to 'limit' such messages (in chronological order),
        but we search from newest to oldest.
        """
        found = []
        count_pattern = re.compile(r"([\d,\.]+)\s+user(?:s)? got the payout", re.IGNORECASE)
        total_pattern = re.compile(r"racked up a total.*?⏣\s*([\d,\.kmKMbB]+)", re.IGNORECASE)

        messages = []
        async for msg in channel.history(limit=100, oldest_first=False):
            messages.append(msg)

        for msg in messages:
            if msg.author.id != DANK_MEMER_ID:
                continue

            # Combine content and embed text
            content = msg.content
            if msg.embeds:
                last_embed = msg.embeds[-1]
                content += f"\n{last_embed.title or ''}\n{last_embed.description or ''}"

            if "amazing job everybody" not in content.lower():
                continue

            lines = content.strip().splitlines()
            total_coins = None
            payout_count = 0

            for line in lines:
                clean_line = re.sub(r"[`\*]+", "", line).strip()

                m_tot = total_pattern.search(clean_line)
                if m_tot:
                    val = parse_currency_value("⏣ " + m_tot.group(1))
                    if val is not None:
                        total_coins = val

                m_pay = count_pattern.search(clean_line)
                if m_pay:
                    num_val = parse_plain_number(m_pay.group(1))
                    if num_val is not None:
                        payout_count = int(num_val)

            if total_coins is not None and payout_count > 0:
                found.append((msg, total_coins, payout_count))

            if len(found) >= limit:
                break

        found.reverse()
        return found

    # ---------------------------------------------------------------------
    # BUILD SUMMARY TEXT (for plain text output)
    # ---------------------------------------------------------------------
    def build_heist_summary_text(self, items: List[Tuple[str, str, int, float]], emoji: str) -> str:
        """
        Given a list of (heist_name, message_link, payout_count, each_got),
        build a bullet-point style summary text.
        Only the abbreviated value is shown for each_got.
        """
        lines = []
        for heist_name, link, count, each_got in items:
            each_abbrev = abbreviate_number(each_got)
            lines.append(f"{emoji} **[{heist_name}]({link}) - {count} payouts** each got: **⏣ {each_abbrev}**")
        return "\n".join(lines)

    # ---------------------------------------------------------------------
    # SLASH COMMAND
    # ---------------------------------------------------------------------
    @app_commands.command(
        name="heist_summary",
        description="Scan messages for heist summaries and calculate payouts."
    )
    @app_commands.describe(
        heists_count="Number of heists to scan for (1-5).",
        heist_names="Names of the heists, separated by / or , (in order).",
        emoji="Emoji or symbol to use in place of the bullet (optional, default is '-').",
        embedded="Set to true to post as an embed, false for plain text (default: false)."
    )
    async def heist_summary_slash(
        self,
        interaction: discord.Interaction,
        heists_count: int,
        heist_names: str,
        emoji: Optional[str] = "-",
        embedded: Optional[bool] = False
    ):
        """
        /heist_summary heists_count:3 heist_names:"No Req heists/go heists/booster heists" emoji:":fire:" embedded:true
        1) Scans the last 100 messages in the channel.
        2) Finds up to 'heists_count' Dank Memer payout messages.
        3) Labels each with the names you supply in heist_names.
        4) Posts a summary as plain text (with a header) or as an embed based on the 'embedded' flag.
        """
        if heists_count < 1 or heists_count > 5:
            return await interaction.response.send_message("heists_count must be between 1 and 5.", ephemeral=True)

        possible_delimiters = ["/", ","]
        tmp = heist_names
        for delim in possible_delimiters:
            tmp = tmp.replace(delim, ",")
        name_list = [n.strip() for n in tmp.split(",") if n.strip()]

        channel = interaction.channel
        found = await self.get_recent_heist_messages(channel, heists_count)
        if not found:
            return await interaction.response.send_message("No heist payouts found in the last 100 messages.", ephemeral=True)

        while len(name_list) < len(found):
            name_list.append("Unnamed Heist")

        summary_data = []
        for i, (msg, total_coins, payout_count) in enumerate(found):
            link = f"https://discord.com/channels/{msg.guild.id}/{msg.channel.id}/{msg.id}"
            each_amount = total_coins / payout_count
            summary_data.append((name_list[i], link, payout_count, each_amount))

        summary_text = self.build_heist_summary_text(summary_data, emoji)
        server_name = interaction.guild.name if interaction.guild else "Server"

        if embedded:
            embed = discord.Embed(
                title=f"{server_name}'s Heist Summary",
                description=summary_text,
                color=discord.Color.blurple()
            )
            embed.set_footer(text="Use /heist_summary.")
            await interaction.response.send_message("Heist summary posted successfully!", ephemeral=True)
            await channel.send(embed=embed)
        else:
            # Build a plain text message with a header line
            header = f"**{server_name}'s Heist Summary**\n"
            full_text = header + summary_text
            await interaction.response.send_message("Heist summary posted successfully!", ephemeral=True)
            await channel.send(full_text)

    # ---------------------------------------------------------------------
    # PREFIX COMMAND
    # ---------------------------------------------------------------------
    @commands.command(name="heist_summary")
    async def heist_summary_prefix(self, ctx: commands.Context, heists_count: int, *, heist_names: str):
        """
        Usage (example):
          !heist_summary 3 No Req heists/go heists/booster heists
        The output is in plain text with a header.
        After processing, the command message is deleted.
        """
        if heists_count < 1 or heists_count > 5:
            return await ctx.send("heists_count must be between 1 and 5.")

        possible_delimiters = ["/", ","]
        tmp = heist_names
        for delim in possible_delimiters:
            tmp = tmp.replace(delim, ",")
        name_list = [n.strip() for n in tmp.split(",") if n.strip()]

        channel = ctx.channel
        found = await self.get_recent_heist_messages(channel, heists_count)
        if not found:
            try:
                await ctx.message.delete()
            except Exception:
                pass
            return await ctx.send("No heist payouts found in the last 100 messages.")

        while len(name_list) < len(found):
            name_list.append("Unnamed Heist")

        summary_data = []
        for i, (msg, total_coins, payout_count) in enumerate(found):
            link = f"https://discord.com/channels/{msg.guild.id}/{msg.channel.id}/{msg.id}"
            each_amount = total_coins / payout_count
            summary_data.append((name_list[i], link, payout_count, each_amount))

        # Use a fixed default emoji for the prefix command; no input is taken.
        default_emoji = "-"
        summary_text = self.build_heist_summary_text(summary_data, default_emoji)
        server_name = ctx.guild.name if ctx.guild else "Server"
        header = f"**{server_name}'s Heist Summary**\n"
        full_text = header + summary_text

        try:
            await ctx.message.delete()
        except Exception:
            pass
        await ctx.send(full_text)

# -------------------------------------------------------------------------
# COG SETUP
# -------------------------------------------------------------------------
async def setup(bot: commands.Bot):
    """
    Load this cog. E.g.:
       bot.load_extension("heist_summary_cog")
    or
       await bot.load_extension("heist_summary_cog")
    """
    await bot.add_cog(HeistSummaryCog(bot))
    logger.info("[HeistSummary] Cog loaded.")
