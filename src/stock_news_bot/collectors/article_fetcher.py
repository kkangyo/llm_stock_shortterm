"""1차 필터가 종목을 특정하지 못했을 때, 기사 본문을 가져와 재판단에 쓰기 위한 헬퍼.

RSS의 summary가 비어있는 경우가 많아(특히 한국경제 - 실측 확인함) 제목만으로는
정보가 부족한 경우가 많다. 본문을 가져와 다시 판단시키면 제목에 없는 종목명을
찾아낼 수 있는 경우가 많지만, 매 뉴스마다 매번 스크래핑하면 폴링 주기에 부담이 되므로
호출하는 쪽(pipeline)에서 "1차 필터가 못 잡았을 때만" 선별적으로 불러야 한다.

trafilatura로 뽑은 본문에는 한국경제 특유의 메뉴/저작권 안내 문구가 섞여 나오는 걸
실측으로 확인해서 그 부분만 제거한다. 다른 언론사를 추가하면 이 정리 로직도 손봐야
할 수 있다.
"""
from __future__ import annotations

import logging
import re

import requests

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_REQUEST_TIMEOUT = 10
_MAX_CHARS = 1500  # LLM에 넘길 본문 길이 상한 (프롬프트 비용/속도 고려)
_MIN_USABLE_CHARS = 50  # 정리 후에도 이보다 짧으면 실질적 내용이 없다고 판단

_WHITESPACE_RE = re.compile(r"\s+")
# 한국경제 기사 페이지에서 trafilatura가 본문과 함께 뽑아내는 메뉴/안내 문구.
_BOILERPLATE_LINES = {"기사 스크랩", "댓글", "공유", "글자크기", "프린트", "-"}
_SEO_NUDGE_LINE = "Google 검색에서 한국경제 기사를 더 자주 볼 수 있습니다."
# "한경 프리미엄9의 모든 콘텐츠는 한국경제신문의 저작물로 저작권법의 보호를 받습니다..."
# 형태의 저작권 안내 문단 - 본문 뒤에 항상 붙어 나와서 통째로 잘라낸다.
_COPYRIGHT_MARKER_RE = re.compile(r"한경\s*프리미엄\S*\s*의\s*모든\s*콘텐츠는")

_trafilatura_missing_warned = False


def fetch_article_text(url: str) -> str | None:
    """기사 본문을 최대한 뽑아본다. 실패하거나 정리 후 내용이 너무 짧으면 None."""
    global _trafilatura_missing_warned
    try:
        import trafilatura
    except ImportError:
        if not _trafilatura_missing_warned:
            logger.warning(
                "trafilatura가 설치되어 있지 않아 본문 보강을 건너뜁니다. "
                "'pip install trafilatura'로 설치하세요."
            )
            _trafilatura_missing_warned = True
        return None

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        logger.debug("기사 본문 요청 실패: %s", url)
        return None

    extracted = trafilatura.extract(resp.text, favor_recall=False)
    if not extracted:
        return None

    cleaned = _clean(extracted)
    if len(cleaned) < _MIN_USABLE_CHARS:
        return None

    return cleaned[:_MAX_CHARS]


def _clean(text: str) -> str:
    # 저작권 안내 문단은 항상 실제 본문 뒤에 붙어 나오므로 그 지점부터 통째로 잘라낸다.
    match = _COPYRIGHT_MARKER_RE.search(text)
    if match:
        text = text[: match.start()]

    lines = [line.strip() for line in text.splitlines()]
    lines = [
        line
        for line in lines
        if line and line not in _BOILERPLATE_LINES and line != _SEO_NUDGE_LINE
    ]
    return _WHITESPACE_RE.sub(" ", " ".join(lines)).strip()
