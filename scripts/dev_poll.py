"""Run the bot on your laptop: poll Telegram and feed taps to the real handlers.

    python -m scripts.dev_poll              # listen for button taps
    python -m scripts.dev_poll --source     # send a fresh pick list first (ignores the 44h gap)

Uses exactly the handle_update() the webhook uses, so what you test here is what
runs on Cloud Run -- without a public URL.

USE A SEPARATE DEV BOT (its own token in .env). A bot receives updates through a
webhook OR getUpdates, never both: this script deletes the webhook, which would
silently cut the production service off from its bot.
"""

import argparse
import logging

from groundtruth import handlers, telegram

_POLL_SECONDS = 30


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll Telegram and run the real handlers locally.")
    parser.add_argument("--source", action="store_true", help="send a fresh pick list first")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="  · %(message)s")
    logging.getLogger("groundtruth").setLevel(logging.INFO)

    me = telegram.call("getMe")
    telegram.call("deleteWebhook")
    print(f"Polling as @{me['username']} (webhook removed). Ctrl+C to stop.")

    if args.source:
        print("Source job:", handlers.source_job(force=True))

    offset = None
    while True:
        updates = telegram.call(
            "getUpdates",
            offset=offset,
            timeout=_POLL_SECONDS,
            allowed_updates=["callback_query"],
        )
        for update in updates:
            offset = update["update_id"] + 1
            data = (update.get("callback_query") or {}).get("data")
            print(f"tap: {data}")
            handlers.handle_update(update)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
