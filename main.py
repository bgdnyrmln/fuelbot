#!/usr/bin/env python
# pylint: disable=unused-argument
import json
import logging
import os
import re
from datetime import time as dtime
from pathlib import Path

from dotenv import load_dotenv
from telegram import ForceReply, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

load_dotenv()

import fuel  # noqa: E402  (after load_dotenv so env vars are read)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

SUBS_FILE = Path(os.getenv("SUBS_FILE", "subscriptions.json"))  # {chat_id: "HH:MM"}
DEFAULT_TIME = "08:00"
ASK_TIME = 0


# ---------------------------------------------------------------- subscriptions
def load_subs() -> dict[str, str]:
    try:
        return json.loads(SUBS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("Could not read subscriptions")
        return {}


def save_subs(subs: dict[str, str]) -> None:
    SUBS_FILE.write_text(json.dumps(subs), encoding="utf-8")


def remove_jobs(job_queue, chat_id: int) -> bool:
    jobs = job_queue.get_jobs_by_name(str(chat_id))
    for job in jobs:
        job.schedule_removal()
    return bool(jobs)


def add_job(job_queue, chat_id: int, hhmm: str) -> None:
    remove_jobs(job_queue, chat_id)
    h, m = map(int, hhmm.split(":"))
    job_queue.run_daily(
        send_daily,
        time=dtime(h, m, tzinfo=fuel.TZ),
        chat_id=chat_id,
        name=str(chat_id),
    )


def parse_time(text: str) -> str | None:
    text = text.strip().lower()
    if text in ("default", "d", "ok"):
        return DEFAULT_TIME
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", text)
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return f"{int(m[1]):02d}:{m[2]}"


async def send_daily(context: ContextTypes.DEFAULT_TYPE) -> None:
    await fuel.refresh_if_needed()  # no-op if cache is younger than CACHE_TTL
    await context.bot.send_message(context.job.chat_id, fuel.format_prices())


# ---------------------------------------------------------------- handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_html(
        f"👋 Hi {user.mention_html()}!\n\n"
        "I track fuel prices in Latvia ⛽ and show the cheapest station for each fuel at "
        "<b>Circle K, Virsi, Neste and Straujupite</b>.\n\n"
        "<b>What you can do:</b>\n"
        "/fuel – see the latest prices right now\n"
        "/fuelscan – get prices sent to you every day (default 08:00, or pick your own time, e.g. <code>/fuelscan 07:30</code>)\n"
        "/break – stop daily updates\n"
        "/help – show this list again\n\n"
        "💡 Prices are cached for about an hour, so replies are instant and the "
        "fuel sites don't get spammed.\n\n"
        "Author: @slashgodmoded"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/fuel - show latest cached fuel prices\n"
        "/fuelscan [HH:MM] - get prices every day at the chosen time (default 08:00)\n"
        "/break - stop daily updates"
    )


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.text == "67":
        await update.message.reply_text("stop")


async def fuel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Reads the cache; only scrapes if a provider's data is older than CACHE_TTL.
    await fuel.refresh_if_needed()
    await update.message.reply_text(fuel.format_prices())


async def _subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE, hhmm: str) -> None:
    chat_id = update.effective_chat.id
    add_job(context.job_queue, chat_id, hhmm)
    subs = load_subs()
    subs[str(chat_id)] = hhmm
    save_subs(subs)
    await update.message.reply_text(
        f"Done! You'll get fuel prices every day at {hhmm} ({fuel.TZ.key}). Use /break to stop."
    )


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.args:  # /fuelscan 07:30
        hhmm = parse_time(context.args[0])
        if hhmm:
            await _subscribe(update, context, hhmm)
            return ConversationHandler.END
        await update.message.reply_text("Invalid time. Use HH:MM, e.g. 08:00")
    await update.message.reply_text(
        f"What time should I send the daily fuel prices? ({fuel.TZ.key})\n"
        f"Reply with HH:MM (e.g. 07:30) or 'default' for {DEFAULT_TIME}. /cancel to abort."
    )
    return ASK_TIME


async def got_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    hhmm = parse_time(update.message.text)
    if not hhmm:
        await update.message.reply_text("Couldn't read that. Use HH:MM, e.g. 08:00")
        return ASK_TIME
    await _subscribe(update, context, hhmm)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


async def break_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    had_job = remove_jobs(context.job_queue, chat_id)
    subs = load_subs()
    had_sub = subs.pop(str(chat_id), None) is not None
    save_subs(subs)
    await update.message.reply_text(
        "Daily updates stopped." if (had_job or had_sub) else "No active scan to stop."
    )


async def post_init(application: Application) -> None:
    """Re-create daily jobs after a restart."""
    fuel.load_cache()
    for chat_id, hhmm in load_subs().items():
        add_job(application.job_queue, int(chat_id), hhmm)
    logger.info("Restored %d subscription(s)", len(load_subs()))


def main() -> None:
    application = Application.builder().token(os.getenv("TOKEN")).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("fuel", fuel_command))
    application.add_handler(CommandHandler("break", break_command))
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("fuelscan", scan)],
            states={ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_time)]},
            fallbacks=[CommandHandler("cancel", cancel)],
        )
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
