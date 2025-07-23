import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import datetime
import logging

# Set up logging
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

class HeistCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channel_permission_backups = {}
        self.added_roles = {}

    ### Prefix Command ###
    @commands.command(name="heist_start", description="Start a Dank Memer heist with advanced view-locking.")
    @commands.guild_only()
    async def heist_start_prefix(self, ctx, amount: str, roles: str = None, ping: bool = False):
        await self.start_heist(ctx=ctx, amount=amount, roles=roles, ping=ping, interaction=None)

    ### Slash Command ###
    @app_commands.command(name="heist_start", description="Start a Dank Memer heist with advanced view-locking.")
    @app_commands.guild_only()
    async def heist_start_slash(self, interaction: discord.Interaction, amount: str, roles: str = None, ping: bool = False):
        await self.start_heist(ctx=None, amount=amount, roles=roles, ping=ping, interaction=interaction)

    ### Common Heist Start Function ###
    async def start_heist(self, ctx=None, interaction=None, amount: str = None, roles: str = None, ping: bool = False):
        # Determine the source of the command
        if interaction:
            channel = interaction.channel
            guild = interaction.guild
            author = interaction.user
        else:
            channel = ctx.channel
            guild = ctx.guild
            author = ctx.author

        # Parse roles
        role_ids = [int(role_id.strip("<@&>")) for role_id in roles.split()] if roles else []
        allowed_roles = [guild.get_role(role_id) for role_id in role_ids if guild.get_role(role_id)]

        if not allowed_roles:
            message = "❌ No valid roles provided. Please mention at least one role."
            if interaction:
                await interaction.response.send_message(message, ephemeral=True)
            else:
                await ctx.send(message)
            return

        # Backup current permissions
        permission_backup = {
            role.id: channel.overwrites_for(role)
            for role in guild.roles if not channel.overwrites_for(role).is_empty()
        }
        self.channel_permission_backups[channel.id] = permission_backup
        self.added_roles[channel.id] = []

        # Lock channel permissions
        await self.lock_channel_permissions(channel, permission_backup, allowed_roles)

        # Prepare embed
        role_mentions = " ".join([role.mention for role in allowed_roles])
        embed = discord.Embed(
            title="💰 Heist Starting",
            description=(
                f"{author.mention} will start a heist with the Amount: **{amount}**\n\n"
                f"**<:KingCrypto:1274677685766852609> Required Roles:**\n> {role_mentions}\n\n"
                "*Run the heist command using* `/serverevents run bankrob` in this channel to start."
            ),
            color=discord.Color.gold(),
            timestamp=datetime.datetime.utcnow()
        )
        embed.set_footer(text="The view-lock will be reverted automatically after the heist ends.")
        embed.set_thumbnail(
            url="https://cdn.discordapp.com/avatars/270904126974590976/a_24778db4737114253ac3b30f45f1979f.gif?size=1024"
        )

        # Send response
        if interaction:
            await interaction.response.send_message(embed=embed)
            if ping:
                await channel.send(" ".join([role.mention for role in allowed_roles]))
            await channel.send(f"/serverevents run serverbankrob quantity:{amount}")
        else:
            await ctx.send(embed=embed)
            if ping:
                await channel.send(" ".join([role.mention for role in allowed_roles]))
            await channel.send(f"/serverevents run serverbankrob quantity:{amount}")

        logger.info(f"Heist started in channel {channel.name} ({channel.id}) with roles: {allowed_roles}")

    ### Heist Help Command (Prefix) ###
    @commands.command(name="heist_help", description="Get help on how to use heist commands.")
    async def heist_help_prefix(self, ctx):
        await self.send_heist_help(ctx=ctx, interaction=None)

    ### Heist Help Command (Slash) ###
    @app_commands.command(name="heist_help", description="Get help on how to use heist commands.")
    async def heist_help_slash(self, interaction: discord.Interaction):
        await self.send_heist_help(ctx=None, interaction=interaction)

    ### Common Help Function ###
    async def send_heist_help(self, ctx=None, interaction=None):
        embed = discord.Embed(
            title="📝 Heist Commands Help",
            description="Here are the commands you can use for the heist:",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Start Heist Command",
            value=(
                "**Prefix Command:** `!heist_start <amount> <roles> [ping]`\n"
                "**Slash Command:** `/heist_start amount:<amount> roles:<roles> ping:<ping>`\n\n"
                "**Parameters:**\n"
                "`<amount>` - The amount for the heist.\n"
                "`<roles>` - Mention the roles allowed to participate.\n"
                "`[ping]` - Optional. Whether to ping the roles (`True` or `False`).\n\n"
                "Example:\n"
                "`!heist_start 1000000 @HeistRole True`"
            ),
            inline=False
        )
        embed.set_footer(text="Make sure to have the correct permissions.")
        embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1271907062024245309/1300714163987021856/memer.png")

        if interaction:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)

    ### on_message Listener ###
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Process only messages in guild channels where a heist is active
        if not message.guild or message.channel.id not in self.channel_permission_backups:
            return

        # Ignore messages from the bot itself
        if message.author.id == self.bot.user.id:
            return

        # --- Heist Join Detection ---
        join_detected = False
        join_keywords = [
            "They're trying to break into server's bank!",
            "Time is up to join the heist, let's go.",
            "users are teaming up to rob server's bank"
        ]
        if any(keyword in message.content for keyword in join_keywords):
            join_detected = True
        elif message.embeds:
            for embed in message.embeds:
                description = embed.description or ""
                if any(keyword in description for keyword in join_keywords):
                    join_detected = True
                    break
        if not join_detected and message.components:
            for action_row in message.components:
                for component in action_row.children:
                    if getattr(component, "label", "").upper() == "JOIN BANKROB":
                        join_detected = True
                        break
                if join_detected:
                    break

        if join_detected:
            embed = discord.Embed(
                title="🔒 View Lock Engaged",
                description="Auto view lock engaged, join the heist!",
                color=discord.Color.gold()
            )
            await message.channel.send(embed=embed)

        # --- Heist End Detection ---
        end_keywords = [
            "Amazing job everybody",
            "racked up a total of",
            "payouts have been distributed",
            "everyone survived",
            "users got fined",
            "Server is not popular enough and didn't get enough people to rob its bank."
        ]
        end_detected = False

        if any(keyword in message.content for keyword in end_keywords):
            end_detected = True
        elif message.embeds:
            for embed in message.embeds:
                description = embed.description or ""
                if any(keyword in description for keyword in end_keywords):
                    end_detected = True
                    break

        if end_detected:
            await self.end_heist(message.channel)

    ### Helper Functions ###
    async def lock_channel_permissions(self, channel, permission_backup, allowed_roles):
        for role_id, permissions in permission_backup.items():
            role = channel.guild.get_role(role_id)
            if role in allowed_roles:
                await self.set_permission_with_retry(
                    channel, role, view_channel=True, read_message_history=True, send_messages=False
                )
            else:
                await self.set_permission_with_retry(channel, role, view_channel=False)

        for role in allowed_roles:
            if role.id not in permission_backup:
                await self.set_permission_with_retry(
                    channel, role, view_channel=True, read_message_history=True, send_messages=False
                )
                self.added_roles[channel.id].append(role.id)

    async def set_permission_with_retry(self, channel, role, **permissions):
        try:
            await channel.set_permissions(role, **permissions)
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = int(e.response.headers.get("Retry-After", 1))
                logger.warning(f"Rate-limited. Retrying after {retry_after} seconds.")
                await asyncio.sleep(retry_after)
                await self.set_permission_with_retry(channel, role, **permissions)
            else:
                raise e

    async def end_heist(self, channel: discord.TextChannel):
        if channel.id not in self.channel_permission_backups:
            return

        permission_backup = self.channel_permission_backups.pop(channel.id)
        for role_id, permissions in permission_backup.items():
            role = channel.guild.get_role(role_id)
            if role:
                await self.set_permission_with_retry(channel, role, overwrite=permissions)

        for role_id in self.added_roles.pop(channel.id, []):
            role = channel.guild.get_role(role_id)
            if role:
                await channel.set_permissions(role, overwrite=None)

        embed = discord.Embed(
            title="🔓 Heist Ended - View-Lock Disabled!",
            description="The channel's permissions have been restored. 🎉\nEveryone can now view the channel again.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(
            url="https://media.discordapp.net/attachments/1271907062024245309/1300714163987021856/memer.png"
        )
        embed.set_footer(text="Hope you got some great payouts!")
        await channel.send(embed=embed)

        logger.info(f"Permissions restored for channel {channel.name} ({channel.id}).")

async def setup(bot):
    await bot.add_cog(HeistCog(bot))
