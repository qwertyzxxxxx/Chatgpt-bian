import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from market_hunter.config import SCHEDULER_HOUR_MY, SCHEDULER_MINUTE_MY, SCHEDULER_TZ
from market_hunter.scanner import run_us_scan

logger = logging.getLogger(__name__)


def _scan_job():
    logger.info("Scheduler triggered: running US scan")
    try:
        results = run_us_scan(notify=True)
        logger.info(f"Scheduled scan complete — {results.get('total_signals', 0)} signals")
    except Exception as e:
        logger.error(f"Scheduled scan failed: {e}", exc_info=True)
        results = {}

    # Paper fund daily run (after scan, same trading day)
    try:
        from datetime import date
        from market_hunter.paper_fund.fund import run_daily
        from market_hunter.paper_fund.db import get_fund
        if get_fund():  # only run if fund has been initialized
            scan_date = date.today().isoformat()
            run_daily(scan_date, scan_results=results, notify=True)
            logger.info("Paper fund daily run complete")
        else:
            logger.info("Paper fund not initialized — skipping paper-daily step")
    except Exception as e:
        logger.error(f"Paper fund daily run failed: {e}", exc_info=True)


def start_scheduler():
    """Start the blocking APScheduler that runs US scan once per day."""
    scheduler = BlockingScheduler(timezone=SCHEDULER_TZ)
    trigger = CronTrigger(
        hour=SCHEDULER_HOUR_MY,
        minute=SCHEDULER_MINUTE_MY,
        timezone=SCHEDULER_TZ,
    )
    scheduler.add_job(_scan_job, trigger, id="us_scan_daily", name="US Daily Scan")
    logger.info(
        f"Scheduler started — US scan runs daily at "
        f"{SCHEDULER_HOUR_MY:02d}:{SCHEDULER_MINUTE_MY:02d} {SCHEDULER_TZ}"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
