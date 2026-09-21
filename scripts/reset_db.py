"""파이프라인 SQLite DB(news_pipeline.db)를 삭제한다.

파이프라인 코드 어디에서도 이 스크립트를 호출하지 않는다 - 오직 사람이 직접 이 파일을
실행했을 때만 삭제가 일어난다. 삭제하면 지금까지 처리한 뉴스 기록, 매수 후보 판단 이력,
재시작 시 중복 방지에 쓰이는 최근 처리 이력이 전부 사라진다.

사용법:
    python scripts/reset_db.py            # 삭제 전 확인 프롬프트
    python scripts/reset_db.py --yes      # 확인 없이 바로 삭제 (스크립트/CI용)
"""
from __future__ import annotations

import argparse
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from stock_news_bot.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="파이프라인 SQLite DB 삭제")
    parser.add_argument("--yes", action="store_true", help="확인 프롬프트 없이 바로 삭제")
    args = parser.parse_args()

    db_path = settings.sqlite_path
    if not db_path.exists():
        print(f"삭제할 DB가 없습니다: {db_path}")
        return

    print(f"삭제 대상: {db_path}")
    print("주의: 지금까지 처리한 뉴스 기록, 매수 후보 판단 이력, 재시작 dedup 이력이 전부 사라집니다.")

    if not args.yes:
        answer = input("정말 삭제하시겠습니까? 계속하려면 yes를 입력하세요: ")
        if answer.strip().lower() != "yes":
            print("취소했습니다.")
            return

    db_path.unlink()
    print(f"삭제 완료: {db_path}")

    # WAL 모드로 열렸던 적이 있으면 -wal/-shm 저널 파일이 남을 수 있어 같이 정리한다.
    for suffix in ("-wal", "-shm"):
        journal = db_path.with_name(db_path.name + suffix)
        if journal.exists():
            journal.unlink()
            print(f"삭제 완료: {journal}")


if __name__ == "__main__":
    main()
