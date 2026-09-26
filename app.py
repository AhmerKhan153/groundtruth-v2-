"""Groundtruth entrypoint.

Runs the Telegram bot plus two scheduled jobs:

    source_news_job      -> periodically sources ranked tech news and sends you a
                            pick list on Telegram
    publish_approved_job -> periodically posts approved drafts to LinkedIn

You drive the middle (pick a story, approve/reject/rewrite the draft) from Telegram.
State lives in MongoDB, so the bot is safe to restart at any time.
"""

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
for path in (ROOT_DIR, SRC_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application, CallbackQueryHandler

from config import TELEGRAM_BOT_TOKEN
from integration.telegram_handler import callback_handler
from scheduler.jobs import source_news_job, publish_approved_job

# How often to source news and to publish approved drafts (minutes).
SOURCE_INTERVAL_MINUTES = 180
PUBLISH_INTERVAL_MINUTES = 10


async def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(callback_handler))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(source_news_job, "interval", minutes=SOURCE_INTERVAL_MINUTES)
    scheduler.add_job(publish_approved_job, "interval", minutes=PUBLISH_INTERVAL_MINUTES)
    scheduler.start()

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    print(
        f"Bot running. Sourcing news every {SOURCE_INTERVAL_MINUTES}m, "
        f"publishing approved drafts every {PUBLISH_INTERVAL_MINUTES}m."
    )

    # Kick off one sourcing cycle immediately so you don't wait for the first tick.
    asyncio.create_task(source_news_job())

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
