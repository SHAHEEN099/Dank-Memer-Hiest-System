import discord
from discord.ext import commands
from discord import app_commands
import logging
import json
import os
import re
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from io import BytesIO

import aiohttp  # Asynchronous HTTP client
import cv2      # OpenCV
import numpy as np

# -------------------------------------------------------------------------
# 1) STORAGE DIRECTORY & CONFIG FILE
# -------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
os.makedirs(STORAGE_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(STORAGE_DIR, "heist_calc_config.json")

# -------------------------------------------------------------------------
# 2) LOGGER INITIALIZATION
# -------------------------------------------------------------------------
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

# -------------------------------------------------------------------------
# 3) UTILITY FUNCTIONS
# -------------------------------------------------------------------------
def parse_currency_value(raw: str) -> Optional[float]:
    """Parses strings like '⏣ 5', '⏣ 1.5M', etc. and returns a float."""
    if not raw:
        return None
    text = raw.replace("⏣", "").replace(",", "").strip()
    match = re.match(r"([\d\.]+)([kKmMbB]?)", text)
    if not match:
        logger.debug("parse_currency_value: No match in '%s'", raw)
        return None
    num_str, suffix = match.groups()
    suffix = suffix.upper()
    try:
        base_val = float(num_str)
    except ValueError as e:
        logger.error("parse_currency_value: cannot convert '%s' to float: %s", num_str, e)
        return None
    if suffix == "K":
        base_val *= 1_000
    elif suffix == "M":
        base_val *= 1_000_000
    elif suffix == "B":
        base_val *= 1_000_000_000
    return base_val

def parse_plain_number(raw: str) -> Optional[float]:
    """Parses a plain numeric value (like '2' from '2 users got the payout')."""
    if not raw:
        return None
    text = raw.replace(",", "").strip()
    match = re.match(r"([\d\.]+)", text)
    if not match:
        logger.debug("parse_plain_number: No numeric match in '%s'", raw)
        return None
    try:
        return float(match.group(1))
    except ValueError as e:
        logger.error("parse_plain_number: error converting '%s': %s", match.group(1), e)
        return None

def format_number(val: float) -> str:
    """Formats a float with commas, removing trailing .00 if present."""
    formatted = f"{val:,.2f}"
    if formatted.endswith(".00"):
        return formatted[:-3]
    return formatted.rstrip("0").rstrip(".")

def abbreviate_number(val: float) -> str:
    """Abbreviates a float (K, M, B). Example: 1234 -> 1.23K, 1.23M, etc."""
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

# -------------------------------------------------------------------------
# 4) HEIST CALCULATOR COG
# -------------------------------------------------------------------------
DANK_MEMER_ID = 270904126974590976

class HeistCalculatorCog(commands.Cog):
    """
    A cog that detects Dank Memer heist payout messages. If configured,
    it replies to the Dank Memer message with either a text embed or a
    more compact OpenCV-based image featuring the server icon plus a 
    black background with rounded corners behind the text.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config_data: Dict[str, Dict[str, Any]] = {}
        self.load_config()
        self.processed_messages = set()
        # Icon cache: {guild_id: {"icon": <OpenCV image array>, "timestamp": datetime}}
        self.icon_cache: Dict[int, Dict[str, Any]] = {}

    # -------------------- CONFIG --------------------
    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            logger.info("[HeistCalc] No config file found; starting with defaults.")
            self.config_data = {}
            return
        try:
            with open(CONFIG_FILE, "r") as f:
                self.config_data = json.load(f)
            logger.info("[HeistCalc] Loaded config from '%s'.", CONFIG_FILE)
        except Exception as e:
            logger.error("[HeistCalc] Error loading config: %s", e)
            self.config_data = {}

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.config_data, f, indent=4)
            logger.info("[HeistCalc] Config saved to '%s'.", CONFIG_FILE)
        except Exception as e:
            logger.error("[HeistCalc] Error saving config: %s", e)

    def get_guild_config(self, guild_id: int) -> Dict[str, Any]:
        """Retrieves or initializes config for a guild."""
        str_gid = str(guild_id)
        if str_gid not in self.config_data:
            self.config_data[str_gid] = {}
        if "template" not in self.config_data[str_gid]:
            self.config_data[str_gid]["template"] = "text"
        return self.config_data[str_gid]

    async def reload_config(self):
        self.load_config()
        logger.info("[HeistCalc] Configuration reloaded in memory.")

    # -------------------- ICON FETCHING --------------------
    async def fetch_server_icon(self, guild: discord.Guild) -> np.ndarray:
        """
        Asynchronously fetches the server icon as an OpenCV (NumPy) image array,
        cached for 10 minutes.
        """
        now = datetime.now(timezone.utc)
        entry = self.icon_cache.get(guild.id)
        if entry and (now - entry["timestamp"]).total_seconds() < 600:
            logger.debug("Using cached icon for guild %s", guild.id)
            return entry["icon"]

        icon_url = guild.icon.url if guild.icon else None
        if not icon_url:
            logger.debug("No icon for guild %s; using blank image.", guild.id)
            blank = np.zeros((256, 256, 3), dtype=np.uint8)
            blank[:] = (50, 50, 50)
            self.icon_cache[guild.id] = {"icon": blank, "timestamp": now}
            return blank

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(icon_url) as resp:
                    if resp.status != 200:
                        raise Exception(f"HTTP {resp.status} error fetching icon.")
                    data = await resp.read()
                    np_arr = np.frombuffer(data, np.uint8)
                    icon_img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                    if icon_img is None:
                        raise Exception("cv2.imdecode returned None.")
                    logger.debug("Fetched new icon for guild %s", guild.id)
                    self.icon_cache[guild.id] = {"icon": icon_img, "timestamp": now}
                    return icon_img
        except Exception as e:
            logger.error("Error fetching server icon for guild %s: %s", guild.id, e)
            fallback = np.zeros((256, 256, 3), dtype=np.uint8)
            fallback[:] = (50, 50, 50)
            self.icon_cache[guild.id] = {"icon": fallback, "timestamp": now}
            return fallback

    async def refresh_icon_cache(self, guild: discord.Guild):
        """Forces a refresh of the icon cache for the specified guild."""
        if guild.id in self.icon_cache:
            del self.icon_cache[guild.id]
        await self.fetch_server_icon(guild)
        logger.debug("Forced icon cache refresh for guild %s", guild.id)

    # -------------------- HELPER: ROUNDED RECTANGLE --------------------
    @staticmethod
    def draw_rounded_rectangle(img: np.ndarray, top_left: tuple, bottom_right: tuple, color: tuple, radius: int):
        """
        Draws a filled rounded rectangle on the given image.
        :param img: The target image.
        :param top_left: (x1, y1) coordinates of the top-left corner.
        :param bottom_right: (x2, y2) coordinates of the bottom-right corner.
        :param color: Color tuple (B, G, R).
        :param radius: The corner radius.
        """
        x1, y1 = top_left
        x2, y2 = bottom_right
        # Ensure the radius does not exceed half the rectangle's dimensions.
        radius = int(min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
        # Draw four circles for the rounded corners
        cv2.circle(img, (x1 + radius, y1 + radius), radius, color, -1)
        cv2.circle(img, (x2 - radius, y1 + radius), radius, color, -1)
        cv2.circle(img, (x1 + radius, y2 - radius), radius, color, -1)
        cv2.circle(img, (x2 - radius, y2 - radius), radius, color, -1)
        # Draw rectangles to connect the circles
        cv2.rectangle(img, (x1 + radius, y1), (x2 - radius, y2), color, -1)
        cv2.rectangle(img, (x1, y1 + radius), (x2, y2 - radius), color, -1)

    # -----------------------------------------------------------------
    # SINGLE SLASH COMMAND: /heist_calculate
    # -----------------------------------------------------------------
    @app_commands.command(name="heist_calculate", description="Toggle the advanced heist calculator in one command.")
    @app_commands.describe(
        enable="Enable (True) or disable (False) the calculator",
        full_server="If True, toggles for entire server; if False, toggles only this channel",
        template="Template to use: 'text' or 'image'"
    )
    async def heist_calculate_cmd(
        self,
        interaction: discord.Interaction,
        enable: bool = True,
        full_server: bool = False,
        template: Optional[str] = "text"
    ):
        """
        A single slash command to configure the calculator:
          - enable: True/False
          - full_server: True/False (toggles entire server vs. just this channel)
          - template: "text" or "image" (default "text")
        """
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Admin permissions required.", ephemeral=True)
        if template not in ["text", "image"]:
            return await interaction.response.send_message("Invalid template. Choose 'text' or 'image'.", ephemeral=True)

        cfg = self.get_guild_config(interaction.guild_id)
        if full_server:
            cfg["global"] = enable
        else:
            cfg[str(interaction.channel_id)] = enable
        cfg["template"] = template
        self.save_config()

        scope_str = "the entire server" if full_server else f"this channel ({interaction.channel.mention})"
        state_str = "enabled" if enable else "disabled"
        await interaction.response.send_message(
            f"Heist Calculator is now **{state_str}** for **{scope_str}** using **{template}** template.",
            ephemeral=True
        )

    # -------------------- PREFIX COMMANDS --------------------
    @commands.group(name="heist_cal", invoke_without_command=True)
    async def heist_cal(self, ctx: commands.Context):
        """Prefix-based command group for the Heist Calculator."""
        await ctx.send("Use `!heist_cal help` for commands.")

    @heist_cal.command(name="help")
    async def heist_cal_help(self, ctx: commands.Context):
        help_text = (
            "**Heist Calculator Commands:**\n"
            "`!heist_cal toggle <True/False> [template]` - Toggle in this channel.\n"
            "`!heist_cal global <True/False>` - Toggle globally.\n"
            "`!heist_cal reload` - Reload config from disk.\n"
            "`!heist_cal refresh_icon` - Refresh the server icon cache.\n"
            "Also see the new slash command `/heist_calculate` for an all-in-one approach."
        )
        await ctx.send(help_text)

    @heist_cal.command(name="toggle")
    async def heist_cal_toggle(self, ctx: commands.Context, enable: bool, template: Optional[str] = "text"):
        if not ctx.author.guild_permissions.administrator:
            return await ctx.send("Admin perms required.")
        if template not in ["text", "image"]:
            return await ctx.send("Template must be 'text' or 'image'.")

        cfg = self.get_guild_config(ctx.guild.id)
        cfg[str(ctx.channel.id)] = enable
        cfg["template"] = template
        self.save_config()

        state_str = "enabled" if enable else "disabled"
        await ctx.send(f"Heist Calculator is now **{state_str}** in {ctx.channel.mention} with `{template}` template.")

    @heist_cal.command(name="global")
    async def heist_cal_global(self, ctx: commands.Context, enable: bool):
        if not ctx.author.guild_permissions.administrator:
            return await ctx.send("Admin perms required.")
        cfg = self.get_guild_config(ctx.guild.id)
        cfg["global"] = enable
        self.save_config()
        state_str = "enabled" if enable else "disabled"
        await ctx.send(f"Global Heist Calculator is now **{state_str}** for this server.")

    @heist_cal.command(name="reload")
    async def heist_cal_reload(self, ctx: commands.Context):
        await self.reload_config()
        await ctx.send("Heist Calculator configuration reloaded.")

    @heist_cal.command(name="refresh_icon")
    async def heist_cal_refresh_icon(self, ctx: commands.Context):
        await self.refresh_icon_cache(ctx.guild)
        await ctx.send("Server icon cache refreshed.")

    # -------------------- IMAGE GENERATION (OpenCV) --------------------
    async def generate_heist_image(self, guild: discord.Guild, payout_count: int, each_amount: float) -> discord.File:
        """
        Uses OpenCV to overlay text onto a 256×256 version of the server icon:
          <payout_count> user(s)
          each got: <abbreviate_number(each_amount)>

        The background behind the text is now drawn with rounded corners.
        """
        # 1) Fetch or create fallback for the icon
        try:
            icon_img = await self.fetch_server_icon(guild)
        except Exception as e:
            logger.error("generate_heist_image: error fetching server icon: %s", e)
            icon_img = np.zeros((256, 256, 3), dtype=np.uint8)
            icon_img[:] = (50, 50, 50)

        # 2) Resize to 256×256
        try:
            icon_img = cv2.resize(icon_img, (256, 256), interpolation=cv2.INTER_AREA)
        except Exception as e:
            logger.error("generate_heist_image: error resizing: %s", e)

        # 3) Apply a gradient darkening only to the bottom 30% of the image
        h, w = icon_img.shape[:2]
        dark_zone_height = int(h * 0.3)
        for i in range(dark_zone_height):
            alpha = np.linspace(0.7, 0, dark_zone_height)[i]
            icon_img[h - dark_zone_height + i, :] = (icon_img[h - dark_zone_height + i, :] * alpha).astype(np.uint8)

        # 4) Prepare text lines
        short_amount = abbreviate_number(each_amount)
        line1 = f"{payout_count} user(s)"
        line2 = f"each got: {short_amount}"

        # 5) Font config
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        thickness = 1
        color_white = (230, 230, 230)
        gap = 5  # gap between lines

        # Measure text sizes
        (w1, h1), _ = cv2.getTextSize(line1, font, font_scale, thickness)
        (w2, h2), _ = cv2.getTextSize(line2, font, font_scale, thickness)
        total_height = h1 + h2 + gap

        # Bottom margin (10 px from bottom)
        bottom_margin = 35
        start_y = 256 - total_height - bottom_margin
        center_x = 128

        # Define the rounded rectangle bounding box with extra padding
        rect_width = max(w1, w2) + 92
        rect_height = total_height + 35
        rect_left = int(center_x - rect_width / 2)
        rect_top = start_y - 5
        rect_right = rect_left + rect_width
        rect_bottom = rect_top + rect_height

        # 6) Draw a filled rounded rectangle as background (with a chosen corner radius)
        corner_radius = 10  # Adjust this value as desired
        self.draw_rounded_rectangle(icon_img, (rect_left, rect_top), (rect_right, rect_bottom), (0, 0, 0), corner_radius)

        # 7) Draw text lines centered on the rounded rectangle
        line1_x = int(center_x - w1 / 2)
        line1_y = start_y + h1
        cv2.putText(icon_img, line1, (line1_x, line1_y), font, font_scale, color_white, thickness)
        line2_x = int(center_x - w2 / 2)
        line2_y = line1_y + h2 + gap
        cv2.putText(icon_img, line2, (line2_x, line2_y), font, font_scale, color_white, thickness)

        # 8) Encode the image to PNG and return as discord.File
        success, buf = cv2.imencode(".png", icon_img)
        if not success:
            logger.error("generate_heist_image: cv2.imencode failed.")
            return discord.File(BytesIO(), filename="error.png")
        file_data = BytesIO(buf.tobytes())
        return discord.File(file_data, filename="heist_payout.png")

    # -------------------- ON MESSAGE LISTENER --------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Detects "amazing job everybody" from Dank Memer, extracts total coins and number of users,
        calculates per-person payout, and replies to the Dank Memer message with either a text embed or an OpenCV-generated image.
        """
        # Avoid double-processing
        if message.id in self.processed_messages:
            return
        self.processed_messages.add(message.id)

        # Must be in a guild and from Dank Memer
        if not message.guild or message.author.id != DANK_MEMER_ID:
            return

        cfg = self.get_guild_config(message.guild.id)
        channel_toggle = cfg.get(str(message.channel.id), False)
        global_toggle = cfg.get("global", False)
        if not (channel_toggle or global_toggle):
            return  # Not enabled

        # Combine message content and embed text (if any)
        content = message.content
        if message.embeds:
            last_embed = message.embeds[-1]
            content += f"\n{last_embed.title or ''}\n{last_embed.description or ''}"

        if "amazing job everybody" not in content.lower():
            return

        # Parse total coins and number of users
        lines = content.strip().splitlines()
        total_pattern = re.compile(r"racked up a total.*?⏣\s*([\d,\.kmKMbB]+)", re.IGNORECASE)
        payout_pat = re.compile(r"([\d,\.]+)\s+user(?:s)? got the payout", re.IGNORECASE)

        total_coins = None
        payout_count = 0
        for line in lines:
            clean_line = re.sub(r"[`\*]+", "", line).strip()
            m_tot = total_pattern.search(clean_line)
            if m_tot:
                val = parse_currency_value(f"⏣ {m_tot.group(1)}")
                if val is not None:
                    total_coins = val
            m_pay = payout_pat.search(clean_line)
            if m_pay:
                num_val = parse_plain_number(m_pay.group(1))
                if num_val is not None:
                    payout_count = int(num_val)

        if total_coins is None or payout_count == 0:
            logger.debug("HeistCalc: Found 'amazing job' message but no numeric data.")
            return

        each_amount = total_coins / payout_count
        template_choice = cfg.get("template", "text")

        # Build fallback text embed
        embed = discord.Embed(title="Heist Payouts", color=discord.Color.from_rgb(114, 137, 218))
        embed.description = (f"{payout_count} Person Got: ⏣ {format_number(each_amount)} "
                             f"**({abbreviate_number(each_amount)})**")
        embed.set_footer(text="Toggle with /heist_calculate or !heist_cal toggle")

        # Reply directly to the Dank Memer message
        if template_choice == "image":
            try:
                heist_file = await self.generate_heist_image(message.guild, payout_count, each_amount)
                await message.reply(file=heist_file)
                logger.info("HeistCalc: Image response sent (guild %s, channel %s).",
                            message.guild.id, message.channel.id)
            except Exception as e:
                logger.error("HeistCalc: Error generating image: %s", e)
                await message.reply(embed=embed)
        else:
            await message.reply(embed=embed)
            logger.info("HeistCalc: Text embed sent (guild %s, channel %s).",
                        message.guild.id, message.channel.id)

# -------------------------------------------------------------------------
# 5) COG SETUP
# -------------------------------------------------------------------------
async def setup(bot: commands.Bot):
    """Loads the HeistCalculatorCog into the bot with a center-aligned, darker style and one slash command."""
    await bot.add_cog(HeistCalculatorCog(bot))
    logger.info("[HeistCalc] Cog loaded with single slash command (/heist_calculate), prefix commands, and advanced image logic.")
