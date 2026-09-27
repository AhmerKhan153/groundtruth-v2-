"""Run the source job once and exit. The scheduled Container Apps Job's command.

    python -m scripts.run_source            # respects the 44h spacing
    python -m scripts.run_source --force    # ignore it

The schedule fires daily; source_job() skips itself unless the last delivered
run is at least 44h old, which gives an every-other-day cadence. A non-zero exit
marks the execution failed, so the job's retry runs it again -- safe, because
the run is only recorded after the pick list is delivered.
"""

import argparse
import json
import logging
import sys

from groundtruth import handlers


def main() -> int:
    parser = argparse.ArgumentParser(description="Source stories and send the pick list once.")
    parser.add_argument("--force", action="store_true", help="ignore the 44h spacing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("groundtruth").setLevel(logging.INFO)
    try:
        result = handlers.source_job(force=args.force)
    except Exception:
        logging.exception("source job failed")
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
