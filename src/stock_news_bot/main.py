"""실행 진입점.

사용 예:
  python -m stock_news_bot.main --once            # 1회만 실행 (테스트용)
  python -m stock_news_bot.main --once --force     # 장 시간 무시하고 1회 실행
  python -m stock_news_bot.main                    # 장 시간 동안 주기적으로 실행
"""
from __future__ import annotations

import argparse
import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from stock_news_bot.config import settings
from stock_news_bot.pipeline.news_pipeline import NewsPipeline, is_market_open, setup_logging

logger = logging.getLogger(__name__)


def run_forever(pipeline: NewsPipeline, force: bool) -> None:
    scheduler = BlockingScheduler(timezone=settings.market_timezone)

    def job():
        if not force and not is_market_open():
            logger.debug("장 시간 아님 - 스킵")
            return
        pipeline.run_once()

    scheduler.add_job(job, "interval", seconds=settings.polling_interval_seconds)
    logger.info(
        "파이프라인 시작 - %d초 간격, 장시간(%s~%s, %s) 동안 동작",
        settings.polling_interval_seconds,
        settings.market_start,
        settings.market_end,
        settings.market_timezone,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("종료 요청 수신")


def main() -> None:
    parser = argparse.ArgumentParser(description="뉴스 기반 호재 탐지 파이프라인")
    parser.add_argument("--once", action="store_true", help="한 번만 실행하고 종료")
    parser.add_argument(
        "--force", action="store_true", help="장 시간이 아니어도 강제로 실행 (테스트용)"
    )
    args = parser.parse_args()

    setup_logging()
    pipeline = NewsPipeline()

    try:
        if args.once:
            if not args.force and not is_market_open():
                logger.warning("현재 장 시간이 아닙니다. --force 옵션으로 강제 실행할 수 있습니다.")
                return
            records = pipeline.run_once()
            logger.info("1회 실행 완료 - %d건 처리", len(records))
        else:
            run_forever(pipeline, force=args.force)
    finally:
        pipeline.close()


if __name__ == "__main__":
    main()
