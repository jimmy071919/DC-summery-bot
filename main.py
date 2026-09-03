import asyncio
import logging
import os
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import tasks
from openai import AsyncOpenAI
from dotenv import load_dotenv


load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def config() -> tuple[str, str, ZoneInfo, time]:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required")
    try:
        timezone_name = os.getenv("TIMEZONE", "Asia/Taipei")
        local_zone = ZoneInfo(timezone_name)
        hour, minute = (int(part) for part in os.getenv("SUMMARY_TIME", "00:05").split(":", 1))
        summary_time = time(hour, minute, tzinfo=local_zone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise RuntimeError("TIMEZONE or SUMMARY_TIME is invalid") from exc
    return token, os.getenv("OPENAI_MODEL", "gpt-4o-mini"), local_zone, summary_time


def database() -> sqlite3.Connection:
    db = sqlite3.connect(os.getenv("DATABASE_PATH", "summary.db"))
    db.execute("CREATE TABLE IF NOT EXISTS settings (guild_id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL, output_id INTEGER NOT NULL)")
    return db


def get_settings() -> list[tuple[int, int, int]]:
    with database() as db:
        return db.execute("SELECT guild_id, source_id, output_id FROM settings").fetchall()


def save_settings(guild_id: int, source_id: int, output_id: int) -> None:
    with database() as db:
        db.execute(
            "INSERT INTO settings(guild_id, source_id, output_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET source_id=excluded.source_id, output_id=excluded.output_id",
            (guild_id, source_id, output_id),
        )


async def collect_messages(channel: discord.TextChannel, start: datetime, end: datetime) -> str:
    rows: list[str] = []
    permissions = channel.permissions_for(channel.guild.me)
    if not permissions.view_channel or not permissions.read_message_history:
        return ""
    async for message in channel.history(after=start, before=end, oldest_first=True):
        if message.author.bot or not message.content.strip():
            continue
        rows.append(f"[#{channel.name}] {message.author.display_name}: {message.content}")
    return "\n".join(rows)


async def summarize(client: AsyncOpenAI, model: str, messages: str, day: date) -> str:
    response = await client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "你是 Discord 社群摘要助手。請將訊息依主要話題分組，使用繁體中文摘要。"
                    "每個話題以 Markdown 標題開頭，列出重點、重要決定、待辦事項；不要捏造訊息中沒有的內容。"
                ),
            },
            {
                "role": "user",
                "content": f"請摘要 {day.isoformat()} 的 Discord 訊息，共分話題整理：\n\n{messages}",
            },
        ],
    )
    return response.output_text.strip()


def split_for_discord(text: str, limit: int = 1900) -> list[str]:
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > 0 else limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    return chunks


async def run_summary(
    bot: discord.Client, client: AsyncOpenAI, model: str, zone: ZoneInfo, guild_id: int | None = None
) -> None:
    now = datetime.now(zone)
    yesterday = now.date() - timedelta(days=1)
    start = datetime.combine(yesterday, time.min, zone).astimezone(timezone.utc)
    end = datetime.combine(now.date(), time.min, zone).astimezone(timezone.utc)
    for configured_guild_id, source_id, output_id in get_settings():
        if guild_id is not None and configured_guild_id != guild_id:
            continue
        source = bot.get_channel(source_id)
        output = bot.get_channel(output_id)
        if not isinstance(source, discord.TextChannel) or not isinstance(output, discord.TextChannel):
            log.warning("Invalid channel setting for guild %s", configured_guild_id)
            if guild_id is not None:
                raise PermissionError("Bot 找不到設定的來源或輸出頻道，請確認 Bot 已加入該伺服器且能檢視頻道")
            continue
        source_permissions = source.permissions_for(source.guild.me)
        output_permissions = output.permissions_for(output.guild.me)
        if not source_permissions.view_channel:
            raise PermissionError(f"來源頻道 #{source.name} 缺少：檢視頻道")
        if not source_permissions.read_message_history:
            raise PermissionError(f"來源頻道 #{source.name} 缺少：讀取訊息歷史記錄")
        if not output_permissions.view_channel:
            raise PermissionError(f"輸出頻道 #{output.name} 缺少：檢視頻道")
        if not output_permissions.send_messages:
            raise PermissionError(f"輸出頻道 #{output.name} 缺少：傳送訊息")
        messages = await collect_messages(source, start, end)
        if not messages:
            result = "昨天沒有可摘要的訊息。"
        else:
            result = await summarize(client, model, messages, yesterday)
        for chunk in split_for_discord(f"## {yesterday} 摘要\n{result}"):
            await output.send(chunk)


async def main() -> None:
    token, model, zone, summary_time = config()
    intents = discord.Intents.default()
    intents.message_content = True
    bot = discord.Client(intents=intents)
    tree = discord.app_commands.CommandTree(bot)
    client = AsyncOpenAI()

    @tree.command(name="summary_setup", description="設定每日摘要的來源與輸出頻道")
    @discord.app_commands.default_permissions(manage_guild=True)
    @discord.app_commands.describe(source="要記錄的文字頻道", output="要發送摘要的文字頻道")
    async def summary_setup(
        interaction: discord.Interaction,
        source: discord.abc.GuildChannel,
        output: discord.abc.GuildChannel,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("這個指令只能在伺服器內使用。", ephemeral=True)
            return
        if not isinstance(source, discord.TextChannel) or not isinstance(output, discord.TextChannel):
            await interaction.response.send_message("來源與輸出都必須是文字頻道，不能選語音頻道、分類或討論串。", ephemeral=True)
            return
        save_settings(interaction.guild_id, source.id, output.id)
        await interaction.response.send_message(
            f"已設定：讀取 {source.mention}，每日摘要發送到 {output.mention}。", ephemeral=True
        )

    @tree.command(name="summary_test", description="立即測試一次昨日摘要")
    @discord.app_commands.default_permissions(manage_guild=True)
    async def summary_test(interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("這個指令只能在伺服器內使用。", ephemeral=True)
            return
        if not any(guild_id == interaction.guild_id for guild_id, _, _ in get_settings()):
            await interaction.response.send_message("請先使用 /summary_setup 設定來源與輸出頻道。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await run_summary(bot, client, model, zone, interaction.guild_id)
        except (discord.Forbidden, PermissionError) as exc:
            await interaction.followup.send(f"權限檢查失敗：{exc}", ephemeral=True)
        except Exception:
            log.exception("Manual summary test failed")
            await interaction.followup.send("測試失敗，請查看 Bot 日誌。", ephemeral=True)
        else:
            await interaction.followup.send("測試完成，請到設定的輸出頻道查看摘要。", ephemeral=True)

    @bot.event
    async def setup_hook() -> None:
        await tree.sync()

    @tasks.loop(time=summary_time)
    async def daily_summary() -> None:
        try:
            await run_summary(bot, client, model, zone)
        except Exception:
            log.exception("Daily summary failed")

    @bot.event
    async def on_ready() -> None:
        if not daily_summary.is_running():
            daily_summary.start()
        log.info("Logged in as %s", bot.user)

    try:
        await bot.start(token)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
