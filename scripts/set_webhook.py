"""Point the bot's webhook at the deployed service, or show / remove it.

    python -m scripts.set_webhook https://<app>.<region>.azurecontainerapps.io
    python -m scripts.set_webhook --info
    python -m scripts.set_webhook --delete

Registers <base>/telegram with TELEGRAM_WEBHOOK_SECRET (Telegram sends it back in
X-Telegram-Bot-Api-Secret-Token on every call), accepts button taps only, and
drops updates queued while no webhook was set.
"""

import argparse
import json

from groundtruth import telegram
from groundtruth.config import TELEGRAM_WEBHOOK_SECRET


def set_webhook(base_url: str) -> None:
    if not TELEGRAM_WEBHOOK_SECRET:
        raise SystemExit("TELEGRAM_WEBHOOK_SECRET is not set; the service would refuse every update.")
    telegram.call(
        "setWebhook",
        url=base_url.rstrip("/") + "/telegram",
        secret_token=TELEGRAM_WEBHOOK_SECRET,
        allowed_updates=["callback_query"],
        drop_pending_updates=True,
        max_connections=5,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url", nargs="?", help="service URL, e.g. https://groundtruth.<...>.azurecontainerapps.io")
    parser.add_argument("--info", action="store_true", help="show the current webhook")
    parser.add_argument("--delete", action="store_true", help="remove the webhook")
    args = parser.parse_args()

    if args.delete:
        telegram.call("deleteWebhook")
        print("Webhook removed.")
    elif args.base_url:
        set_webhook(args.base_url)
        print(f"Webhook set to {args.base_url.rstrip('/')}/telegram")
    elif not args.info:
        parser.error("give a base URL, --info or --delete")
    print(json.dumps(telegram.call("getWebhookInfo"), indent=2))


if __name__ == "__main__":
    main()
