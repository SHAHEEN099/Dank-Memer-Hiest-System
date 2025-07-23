import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
import datetime
import re
import json
import os

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# ─────────────────────────────────────────────────────────────────────
# Helper: Parse time string (e.g., "10s", "5m", "1h30m", "2d") into seconds.
# ─────────────────────────────────────────────────────────────────────
def parse_time_string(time_str: str) -> int:
    pattern = re.compile(r'(\d+)([smhd])', re.IGNORECASE)
    matches = pattern.findall(time_str)
    if not matches:
        raise ValueError("Invalid time format. Use '10s', '5m', '1h30m', '2d', etc.")
    total_seconds = 0
    for (value, unit) in matches:
        val = int(value)
        unit = unit.lower()
        if unit == 's':
            total_seconds += val
        elif unit == 'm':
            total_seconds += val * 60
        elif unit == 'h':
            total_seconds += val * 3600
        elif unit == 'd':
            total_seconds += val * 86400
    return total_seconds

# ─────────────────────────────────────────────────────────────────────
# Data model for ephemeral channel data.
# ─────────────────────────────────────────────────────────────────────
class SpreeChannelData:
    def __init__(self, end_time: datetime.datetime, message_id: int, base_seconds: int):
        self.end_time = end_time
        self.message_id = message_id
        self.base_seconds = base_seconds
        self.dismissed = False

    def to_dict(self):
        return {
            "end_time": self.end_time.isoformat(),
            "message_id": self.message_id,
            "base_seconds": self.base_seconds,
            "dismissed": self.dismissed
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        obj = cls(
            end_time=datetime.datetime.fromisoformat(data["end_time"]),
            message_id=data["message_id"],
            base_seconds=data["base_seconds"]
        )
        obj.dismissed = data.get("dismissed", False)
        return obj

# ─────────────────────────────────────────────────────────────────────
# View with two buttons: Extend 1h (green) and Dismiss (gray)
# ─────────────────────────────────────────────────────────────────────
class ExtendDismissView(discord.ui.View):
    def __init__(self, cog, channel_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id

    @discord.ui.button(label="Extend 1h", style=discord.ButtonStyle.success)
    async def extend_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = self.cog.ephemeral_channels.get(self.channel_id)
        if not data or data.dismissed:
            return await interaction.response.send_message("Auto-delete is already dismissed or ended.", ephemeral=True)
        # Extend by 1 hour (3600 seconds)
        data.end_time += datetime.timedelta(hours=1)
        data.base_seconds += 3600
        channel = interaction.guild.get_channel(self.channel_id)
        if channel:
            try:
                msg = await channel.fetch_message(data.message_id)
                new_embed = self.cog.build_spree_embed(data.end_time, channel.guild, dismissed=False)
                await msg.edit(embed=new_embed, view=self)
            except Exception as e:
                logger.warning(f"Failed to update embed on extend: {e}")
        await interaction.response.send_message("✅ Extended channel auto-delete by 1 hour.", ephemeral=True)
        self.cog.save_data()

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.secondary)
    async def dismiss_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = self.cog.ephemeral_channels.get(self.channel_id)
        if not data or data.dismissed:
            return await interaction.response.send_message("Auto-delete is already dismissed or ended.", ephemeral=True)
        data.dismissed = True
        channel = interaction.guild.get_channel(self.channel_id)
        if channel:
            try:
                msg = await channel.fetch_message(data.message_id)
                new_embed = self.cog.build_spree_embed(data.end_time, channel.guild, dismissed=True)
                await msg.edit(embed=new_embed, view=None)
            except Exception as e:
                logger.warning(f"Failed to update embed on dismiss: {e}")
        await interaction.response.send_message("✅ Auto-delete dismissed for this channel.", ephemeral=True)
        self.cog.save_data()

# ─────────────────────────────────────────────────────────────────────
# Main Spree Cog
# ─────────────────────────────────────────────────────────────────────
class SpreeCog(commands.Cog):
    """
    This cog manages ephemeral (temporary) channels in designated categories.
    It supports:
      • Adding/removing monitored categories via slash commands with custom deletion times.
      • When a new channel is created in a monitored category, an embed is posted showing:
           "This channel will be deleted <t:END:R>"
         with two buttons: Extend 1h (green) and Dismiss (gray).
      • A background task checks every 30 seconds and deletes channels when their timer expires.
      • If dismissed, the embed shows strikethrough text indicating the channel will not be auto-deleted.
    Data is persisted in a JSON file ("spree_data.json") so settings survive bot restarts.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ephemeral_categories = {}  # dict {category_id (int): default_seconds (int)}
        self.ephemeral_channels = {}    # dict {channel_id (int): SpreeChannelData}
        self.data_file = "/home/container/cogs/cogs2/storage/spree_data.json"
        self.load_data()
        self.update_embeds_loop.start()

    def cog_unload(self):
        self.update_embeds_loop.cancel()
        self.save_data()

    # ──────────────────────────────────────────────────────────────────
    # Persistent Storage
    # ──────────────────────────────────────────────────────────────────
    def save_data(self):
        data = {
            "ephemeral_categories": {str(k): v for k, v in self.ephemeral_categories.items()},
            "ephemeral_channels": {str(k): v.to_dict() for k, v in self.ephemeral_channels.items()}
        }
        try:
            with open(self.data_file, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving spree data: {e}")

    def load_data(self):
        if not os.path.isfile(self.data_file):
            return
        try:
            with open(self.data_file, "r") as f:
                data = json.load(f)
            self.ephemeral_categories = {int(k): v for k, v in data.get("ephemeral_categories", {}).items()}
            self.ephemeral_channels = {int(k): SpreeChannelData.from_dict(v) for k, v in data.get("ephemeral_channels", {}).items()}
        except Exception as e:
            logger.error(f"Error loading spree data: {e}")

    # ──────────────────────────────────────────────────────────────────
    # Background Loop: Update embeds and delete expired channels.
    # ──────────────────────────────────────────────────────────────────
    @tasks.loop(seconds=30)
    async def update_embeds_loop(self):
        now = discord.utils.utcnow()
        to_delete = []
        for ch_id, data in self.ephemeral_channels.items():
            if data.dismissed:
                continue
            if data.end_time <= now:
                to_delete.append(ch_id)
                continue
            channel = self.bot.get_channel(ch_id)
            if channel:
                try:
                    msg = await channel.fetch_message(data.message_id)
                    new_embed = self.build_spree_embed(data.end_time, channel.guild, dismissed=False)
                    await msg.edit(embed=new_embed)
                except Exception as e:
                    logger.warning(f"Failed to update ephemeral embed for channel {ch_id}: {e}")
        for ch_id in to_delete:
            self.ephemeral_channels.pop(ch_id, None)
            self.save_data()
            channel = self.bot.get_channel(ch_id)
            if channel:
                try:
                    await channel.delete(reason="Ephemeral channel auto-deleted.")
                except Exception as e:
                    logger.warning(f"Failed to delete ephemeral channel {ch_id}: {e}")

    @update_embeds_loop.before_loop
    async def before_update_embeds_loop(self):
        await self.bot.wait_until_ready()
        logger.info("SpreeCog: update_embeds_loop started.")

    # ──────────────────────────────────────────────────────────────────
    # Build ephemeral embed
    # ──────────────────────────────────────────────────────────────────
    def build_spree_embed(self, end_time: datetime.datetime, guild: discord.Guild, dismissed: bool) -> discord.Embed:
        now = discord.utils.utcnow()
        ts = int(end_time.timestamp())
        if dismissed:
            embed = discord.Embed(
                title="Temporary Channel",
                description=f"~~This channel will be deleted <t:{ts}:R>~~\n**This channel will NOT be deleted**",
                color=discord.Color.red(),
                timestamp=now
            )
        else:
            embed = discord.Embed(
                title="⏰ Temporary Channel",
                description=f"This channel will be deleted <t:{ts}:R>.",
                color=discord.Color.gold(),
                timestamp=now
            )
        if guild.icon:
            embed.set_footer(text="Extend or dismiss auto-delete below.", icon_url=guild.icon.url)
        else:
            embed.set_footer(text="Extend or dismiss auto-delete below.")
        return embed

    # ──────────────────────────────────────────────────────────────────
    # Slash Commands for Ephemeral Categories
    # ──────────────────────────────────────────────────────────────────
    @app_commands.command(name="spree_categories_add", description="Monitor a category for ephemeral channels with a custom delete time.")
    @app_commands.describe(
        category="Select a category to monitor",
        del_time="Default auto-delete time (e.g. '10s', '5m', '1h', '1h30m')"
    )
    async def spree_categories_add(self, interaction: discord.Interaction, category: discord.CategoryChannel, del_time: str):
        try:
            seconds = parse_time_string(del_time)
            self.ephemeral_categories[category.id] = seconds
            self.save_data()
            await interaction.response.send_message(
                f"✅ Category **{category.name}** is now monitored. Default auto-delete: {del_time}.",
                ephemeral=True
            )
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    @app_commands.command(name="spree_categories_remove", description="Stop monitoring a category for ephemeral channels.")
    @app_commands.describe(
        category="Select a category to stop monitoring"
    )
    async def spree_categories_remove(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        if category.id in self.ephemeral_categories:
            self.ephemeral_categories.pop(category.id)
            self.save_data()
            await interaction.response.send_message(
                f"✅ Category **{category.name}** is no longer monitored.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Category **{category.name}** was not monitored.",
                ephemeral=True
            )

    @app_commands.command(name="spree_help", description="Show help info for spree ephemeral channels.")
    async def spree_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Spree Channels Help",
            description=(
                "Commands:\n"
                "`/spree_categories_add <category> <del_time>`: Mark a category as ephemeral.\n"
                "  Example: `/spree_categories_add category:#temp del_time:1h30m`\n"
                "`/spree_categories_remove <category>`: Remove ephemeral monitoring from a category.\n\n"
                "When a new text channel is created in a monitored category, the bot posts an embed with:\n"
                "- A relative timestamp: `<t:END_TIMESTAMP:R>` (auto-updating countdown).\n"
                "- Two buttons: **Extend 1h** (green) to add one hour, **Dismiss** (gray) to cancel auto-delete.\n"
                "If dismissed, the embed shows strikethrough text and the channel will not be auto-deleted."
            ),
            color=discord.Color.blurple()
        )
        if interaction.guild and interaction.guild.icon:
            embed.set_footer(text="Spree ephemeral channels system", icon_url=interaction.guild.icon.url)
        else:
            embed.set_footer(text="Spree ephemeral channels system")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ──────────────────────────────────────────────────────────────────
    # Event: On channel create in monitored categories
    # ──────────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if not isinstance(channel, discord.TextChannel):
            return
        if channel.category_id not in self.ephemeral_categories:
            return

        default_seconds = self.ephemeral_categories[channel.category_id]
        end_time = discord.utils.utcnow() + datetime.timedelta(seconds=default_seconds)

        try:
            embed = self.build_spree_embed(end_time, channel.guild, dismissed=False)
            view = ExtendDismissView(self, channel.id)
            msg = await channel.send(embed=embed, view=view)
            self.ephemeral_channels[channel.id] = SpreeChannelData(end_time, msg.id, default_seconds)
            self.save_data()
        except Exception as e:
            logger.warning(f"Failed to send ephemeral embed in channel {channel.id}: {e}")

    # ──────────────────────────────────────────────────────────────────
    # Optional prefix debug commands
    # ──────────────────────────────────────────────────────────────────
    @commands.command(name="spree_cat_add", help="Prefix: Add ephemeral monitoring to a category with a time string.")
    @commands.has_guild_permissions(manage_channels=True)
    async def spree_cat_add_cmd(self, ctx: commands.Context, category: discord.CategoryChannel, *, del_time: str):
        try:
            seconds = parse_time_string(del_time)
            self.ephemeral_categories[category.id] = seconds
            self.save_data()
            await ctx.send(f"✅ Category **{category.name}** is now ephemeral with default time {del_time}.")
        except ValueError as e:
            await ctx.send(f"❌ {e}")

    @commands.command(name="spree_cat_remove", help="Prefix: Remove ephemeral monitoring from a category.")
    @commands.has_guild_permissions(manage_channels=True)
    async def spree_cat_remove_cmd(self, ctx: commands.Context, category: discord.CategoryChannel):
        if category.id in self.ephemeral_categories:
            self.ephemeral_categories.pop(category.id)
            self.save_data()
            await ctx.send(f"✅ Category **{category.name}** is no longer ephemeral.")
        else:
            await ctx.send(f"❌ Category **{category.name}** was not ephemeral.")

async def setup(bot: commands.Bot):
    await bot.add_cog(SpreeCog(bot))
