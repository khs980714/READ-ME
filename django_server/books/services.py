"""
도서 데이터 수집 서비스
- 알라딘 Open API (TTB): 썸네일, 소개, ISBN, 목차 (1차)
- YES24 스크래핑: 소개, 목차 (2차 fallback)
- 교보문고 스크래핑: 소개, 목차 (3차 fallback)
- FastAPI 모델 서버: 난이도 분류, 임베딩 생성
중복 방지: BookList 단위로 데이터 수집 여부를 판단하여 불필요한 API 호출을 방지합니다.
"""

import logging
import re
import time
import urllib.parse

import httpx
import requests
from bs4 import BeautifulSoup
from django.conf import settings

logger = logging.getLogger(__name__)

# ── 판차 / 연도 패턴 ──────────────────────────────────────────

_EDITION_RE = re.compile(
    r"(?:"
    r"\s*[\(\[（［][^\)\]）］]*?(?:판|편|개정|증보|완전개정|전면개정)\w*[\)\]）］]"  # (제2판), (심화편) 등
    r"|\s+개정증보판"                          # 개정증보판 (괄호 없음)
    r"|\s+(?:전면개정|완전개정|개정)\d*판"     # 개정판, 개정2판
    r"|\s+제\d+판"                             # 제2판, 제3판
    r"|\s+\d+판\b"                             # 2판, 3판
    r"|\s+\d+권$"                              # 1권, 2권 (끝)
    r"|\s+부록$"                               # 부록 (끝)
    r"|\s+기출(?:문제집|공략)$"               # 기출문제집, 기출공략 (끝)
    r"|\s+(?:이론|실기)편\s+\d+$"             # 이론편 1, 실기편 2 (끝)
    r")",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"20[2-3]\d")


def extract_edition_info(title: str) -> tuple[str, str]:
    """(edition_str, base_title) 반환. 패턴 없으면 ('', title)."""
    m = _EDITION_RE.search(title)
    if not m:
        return "", title
    edition = m.group().strip()
    base = (title[: m.start()] + title[m.end() :]).strip()
    return edition, base


def extract_publication_year(title: str, description: str = "") -> int | None:
    """제목(1차) → 설명(2차) 순으로 연도 추출. 없으면 None."""
    m = _YEAR_RE.search(title)
    if m:
        return int(m.group())
    if description:
        m = _YEAR_RE.search(description)
        if m:
            return int(m.group())
    return None


def _split_description_toc(text: str) -> tuple[str, str]:
    """설명 텍스트에서 목차 섹션 분리.

    '목차', '차례' 등 섹션 헤더를 기준으로 설명 / 목차로 분리합니다.
    반환: (description, toc) — toc가 없으면 빈 문자열.
    """
    if not text:
        return text, ""

    _TOC_MARKERS = [
        r"(?:^|\n)\s*(?:■\s*|◎\s*|▶\s*|【\s*)?목\s*차(?:\s*】)?\s*(?:\n|$)",
        r"(?:^|\n)\s*차\s*례\s*(?:\n|$)",
        r"(?:^|\n)\s*\[\s*목\s*차\s*\]\s*(?:\n|$)",
        r"(?:^|\n)\s*<\s*목\s*차\s*>\s*(?:\n|$)",
    ]

    for pattern in _TOC_MARKERS:
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            desc_part = text[: m.start()].strip()
            toc_part = text[m.start() :].strip()
            if desc_part and len(toc_part) > 20:
                return desc_part[:2000], toc_part[:3000]

    return text, ""


# ── 알라딘 Open API ───────────────────────────────────────────

ALADIN_SEARCH_URL = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
ALADIN_LOOKUP_URL = "http://www.aladin.co.kr/ttb/api/ItemLookUp.aspx"


def fetch_aladin_book_info(title: str, max_results: int = 5) -> tuple[dict | None, list[dict]]:
    """알라딘 Open API(TTB)로 도서 정보 조회.

    반환: (primary_info, candidates)
      primary_info: 첫 번째 결과의 thumbnail/description/isbn/toc, 없으면 None
      candidates:   최대 max_results개의 후보 목록 (도서 선택 팝오버용)

    ALADIN_TTB_KEY 미설정 시 (None, []) 반환.
    """
    ttb_key = getattr(settings, "ALADIN_TTB_KEY", "")
    if not ttb_key:
        return None, []
    try:
        # 1단계: 도서 검색 (max_results개 반환)
        resp = requests.get(
            ALADIN_SEARCH_URL,
            params={
                "ttbkey": ttb_key,
                "Query": _normalize_title(title),
                "QueryType": "Title",
                "MaxResults": max_results,
                "SearchTarget": "Book",
                "output": "js",
                "Version": "20131101",
            },
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("item", [])
        if not items:
            return None, []

        # 후보 목록 구성
        candidates = [
            {
                "title":         it.get("title", ""),
                "author":        it.get("author", ""),
                "publisher":     it.get("publisher", ""),
                "thumbnail_url": it.get("cover", ""),
                "isbn":          it.get("isbn13", ""),
                "item_id":       str(it.get("itemId", "")),
            }
            for it in items
        ]

        item = items[0]
        result: dict = {
            "thumbnail_url": item.get("cover", ""),
            "description":   item.get("description", "")[:2000],
            "isbn":          item.get("isbn13", ""),
        }

        # 2단계: ItemId로 목차(toc) 조회
        item_id = item.get("itemId")
        if item_id:
            try:
                lookup_resp = requests.get(
                    ALADIN_LOOKUP_URL,
                    params={
                        "ttbkey": ttb_key,
                        "itemIdType": "ItemId",
                        "ItemId": item_id,
                        "output": "js",
                        "Version": "20131101",
                        "OptResult": "toc",
                    },
                    timeout=10,
                )
                lookup_resp.raise_for_status()
                lookup_items = lookup_resp.json().get("item", [])
                if lookup_items:
                    sub_info = lookup_items[0].get("subInfo", {})
                    toc = sub_info.get("toc", "")
                    if toc:
                        result["toc"] = toc[:3000]
            except Exception as exc:
                logger.warning("알라딘 TOC 조회 실패 (%s): %s", title, exc)

        primary = result if any(v for v in result.values()) else None
        return primary, candidates
    except Exception as exc:
        logger.warning("알라딘 API 실패 (%s): %s", title, exc)
        return None, []


def fetch_aladin_by_item_id(item_id: str) -> dict | None:
    """알라딘 ItemId로 상세 도서 정보 조회 (도서 선택 후 적용용)."""
    ttb_key = getattr(settings, "ALADIN_TTB_KEY", "")
    if not ttb_key:
        return None
    try:
        resp = requests.get(
            ALADIN_LOOKUP_URL,
            params={
                "ttbkey": ttb_key,
                "itemIdType": "ItemId",
                "ItemId": item_id,
                "output": "js",
                "Version": "20131101",
                "OptResult": "toc",
            },
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("item", [])
        if not items:
            return None
        item = items[0]
        sub_info = item.get("subInfo", {})
        result = {
            "thumbnail_url": item.get("cover", ""),
            "description":   item.get("description", "")[:2000],
            "isbn":          item.get("isbn13", ""),
            "toc":           sub_info.get("toc", "")[:3000],
        }
        return result if any(v for v in result.values()) else None
    except Exception as exc:
        logger.warning("알라딘 아이템 조회 실패 (%s): %s", item_id, exc)
        return None


# ── YES24 스크래핑 ────────────────────────────────────────────

def fetch_yes24_book_info(title: str) -> dict | None:
    """YES24 검색 결과에서 도서 설명 + 목차 스크래핑."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        search_url = (
            "https://www.yes24.com/Product/Search"
            f"?query={urllib.parse.quote(_normalize_title(title))}&domain=BOOK"
        )
        resp = requests.get(search_url, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        link_tag = soup.select_one(".itemName a")
        if not link_tag:
            return None
        product_url = "https://www.yes24.com" + link_tag["href"]

        time.sleep(0.5)
        resp2 = requests.get(product_url, headers=headers, timeout=10)
        soup2 = BeautifulSoup(resp2.text, "lxml")

        result: dict = {}

        # 도서 소개
        intro_tag = soup2.select_one("#infoset_introduce .infoSetCont_wrap")
        if not intro_tag:
            intro_tag = soup2.select_one(".Wrrapper.infoSetCont_wrap")
        if intro_tag:
            text = intro_tag.get_text(separator="\n", strip=True)
            if text:
                result["description"] = text[:2000]

        # 목차
        toc_tag = soup2.select_one("#infoset_toc .infoSetCont_wrap")
        if not toc_tag:
            toc_tag = soup2.select_one("#infoset_toc")
        if toc_tag:
            text = toc_tag.get_text(separator="\n", strip=True)
            if text:
                result["toc"] = text[:3000]

        # 썸네일
        thumb_tag = soup2.select_one(".gd_img img")
        if thumb_tag and thumb_tag.get("src"):
            result["thumbnail_url"] = thumb_tag["src"]

        return result if result else None
    except Exception as exc:
        logger.warning("YES24 수집 실패 (%s): %s", title, exc)
        return None


# ── 교보문고 스크래핑 ─────────────────────────────────────────

def fetch_kyobo_book_info(title: str) -> dict | None:
    """교보문고 검색 결과에서 도서 설명 + 목차 스크래핑."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        search_url = (
            "https://search.kyobobook.co.kr/search"
            f"?keyword={urllib.parse.quote(_normalize_title(title))}&target=BOOK"
        )
        resp = requests.get(search_url, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        link_tag = soup.select_one(".prod_name a")
        if not link_tag:
            return None
        product_url = link_tag.get("href", "")
        if not product_url.startswith("http"):
            product_url = "https://product.kyobobook.co.kr" + product_url

        time.sleep(0.5)
        resp2 = requests.get(product_url, headers=headers, timeout=10)
        soup2 = BeautifulSoup(resp2.text, "lxml")

        result: dict = {}

        # 도서 소개
        intro_tag = soup2.select_one(".intro_bottom")
        if not intro_tag:
            intro_tag = soup2.select_one("[data-tab-cont='introduce']")
        if intro_tag:
            text = intro_tag.get_text(separator="\n", strip=True)
            if text:
                result["description"] = text[:2000]

        # 목차
        toc_tag = soup2.select_one("[data-tab-cont='contents']")
        if not toc_tag:
            toc_tag = soup2.select_one(".book_contents_inner")
        if toc_tag:
            text = toc_tag.get_text(separator="\n", strip=True)
            if text:
                result["toc"] = text[:3000]

        return result if result else None
    except Exception as exc:
        logger.warning("교보문고 수집 실패 (%s): %s", title, exc)
        return None


# ── 멀티소스 통합 수집 ────────────────────────────────────────

def fetch_book_info_with_fallback(
    title: str, author: str = "", return_candidates: bool = False
) -> dict | tuple[dict, list[dict]]:
    """도서 정보 수집 — 우선순위: 알라딘 API → YES24 → 교보문고.

    각 소스에서 비어있는 필드를 채워가며 수집합니다.
    description에 목차 섹션이 포함된 경우 자동으로 분리합니다.

    return_candidates=True이면 (collected, aladin_candidates) 튜플 반환.
    """
    collected: dict = {"thumbnail_url": "", "description": "", "isbn": "", "toc": ""}

    # 1차: 알라딘 API (후보 목록 포함)
    aladin_info, candidates = fetch_aladin_book_info(title)
    if aladin_info:
        for key in ("thumbnail_url", "isbn", "toc"):
            if aladin_info.get(key):
                collected[key] = aladin_info[key]
        if aladin_info.get("description"):
            # description에서 목차 분리
            desc, toc_from_desc = _split_description_toc(aladin_info["description"])
            collected["description"] = desc
            if toc_from_desc and not collected["toc"]:
                collected["toc"] = toc_from_desc

    # 2차: YES24 (description 또는 toc 없을 때)
    if not collected["description"] or not collected["toc"]:
        yes24_info = fetch_yes24_book_info(title)
        if yes24_info:
            if yes24_info.get("description") and not collected["description"]:
                desc, toc_from_desc = _split_description_toc(yes24_info["description"])
                collected["description"] = desc
                if toc_from_desc and not collected["toc"]:
                    collected["toc"] = toc_from_desc
            if yes24_info.get("toc") and not collected["toc"]:
                collected["toc"] = yes24_info["toc"]
            if yes24_info.get("thumbnail_url") and not collected["thumbnail_url"]:
                collected["thumbnail_url"] = yes24_info["thumbnail_url"]

    # 3차: 교보문고
    if not collected["description"] or not collected["toc"]:
        kyobo_info = fetch_kyobo_book_info(title)
        if kyobo_info:
            if kyobo_info.get("description") and not collected["description"]:
                desc, toc_from_desc = _split_description_toc(kyobo_info["description"])
                collected["description"] = desc
                if toc_from_desc and not collected["toc"]:
                    collected["toc"] = toc_from_desc
            if kyobo_info.get("toc") and not collected["toc"]:
                collected["toc"] = kyobo_info["toc"]

    if return_candidates:
        return collected, candidates
    return collected


# 내부 backward-compat alias
_fetch_book_info_with_fallback = fetch_book_info_with_fallback


# ── Naver Book Search API (도서 추가 팝오버 검색용) ───────────

NAVER_BOOK_URL = "https://openapi.naver.com/v1/search/book.json"


def _naver_headers():
    return {
        "X-Naver-Client-Id":     getattr(settings, "NAVER_CLIENT_ID", ""),
        "X-Naver-Client-Secret": getattr(settings, "NAVER_CLIENT_SECRET", ""),
    }


def search_naver_books(query: str, display: int = 5) -> list[dict]:
    """Naver Book Search API — 여러 결과 반환 (도서 추가 검색 팝오버용)."""
    try:
        resp = requests.get(
            NAVER_BOOK_URL,
            headers=_naver_headers(),
            params={"query": query, "display": display},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        results = []
        for item in items:
            raw_author = _strip_html(item.get("author", ""))
            author_str = raw_author.replace("^", ", ")
            results.append({
                "title":         _strip_html(item.get("title", "")),
                "author":        author_str,
                "publisher":     _strip_html(item.get("publisher", "")),
                "thumbnail_url": item.get("image", ""),
                "description":   _strip_html(item.get("description", "")),
                "isbn":          item.get("isbn", "").split()[-1] if item.get("isbn") else "",
            })
        return results
    except Exception as exc:
        logger.warning("Naver API 검색 오류 (%s): %s", query, exc)
        return []


# ── 유틸 ─────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _normalize_title(title: str) -> str:
    """검색용 제목 정규화 — '(개정판)', '(2판)' 등 판형 표현 제거."""
    normalized = re.sub(
        r"[\(\[（［][^\)\]）］]*?(개정|증보|완전|전면|개편|판|edition)[^\)\]）］]*?[\)\]）］]",
        "", title, flags=re.IGNORECASE,
    )
    return normalized.strip()


# ── FastAPI 모델 서버 호출 ────────────────────────────────────

def classify_difficulty(title: str, description: str, reviews: list[str]) -> str | None:
    """FastAPI /embed/classify 호출 → 난이도 문자열 반환."""
    try:
        resp = httpx.post(
            f"{settings.MODEL_SERVER_URL}/embed/classify",
            json={"title": title, "description": description, "reviews": reviews},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("difficulty")
    except Exception as exc:
        logger.warning("난이도 분류 실패 (%s): %s", title, exc)
        return None


def classify_category(title: str, description: str) -> list[str]:
    """FastAPI /embed/classify-category 호출 → 카테고리 이름 목록 반환."""
    try:
        resp = httpx.post(
            f"{settings.MODEL_SERVER_URL}/embed/classify-category",
            json={"title": title, "description": description},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("categories", [])
    except Exception as exc:
        logger.warning("카테고리 분류 실패 (%s): %s", title, exc)
        return []


def generate_embedding(book_list_id: int, title: str, description: str) -> bool:
    """FastAPI /embed/book 호출 → book_embeddings 적재."""
    try:
        resp = httpx.post(
            f"{settings.MODEL_SERVER_URL}/embed/book",
            json={"book_list_id": book_list_id, "title": title, "description": description},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("임베딩 생성 실패 (book_list_id=%s): %s", book_list_id, exc)
        return False


# ── 통합 파이프라인 ───────────────────────────────────────────

def run_booklist_pipeline(
    book_list, progress_callback=None, return_candidates: bool = False
) -> list[dict] | None:
    """도서 데이터 수집 파이프라인 (BookList 단위 중복 방지):

    1. 알라딘 API → YES24 → 교보문고 순으로 fallback 수집
       (description·isbn·thumbnail·toc 중 비어있는 필드만 채움)
    2. LLM 난이도 분류 (difficulty 없을 때만, description 기반)
    3. 임베딩 생성 (embedding 없을 때만)

    return_candidates=True이면 알라딘 후보 목록을 반환합니다 (없으면 []).
    """
    from .models import BookEmbedding

    author_name = book_list.get_author_display()
    update_fields = []
    aladin_candidates: list[dict] = []

    def _log(msg):
        if progress_callback:
            progress_callback(msg)
        else:
            logger.info(msg)

    # 1) 수집이 필요한 필드가 하나라도 비어있으면 멀티소스 수집
    needs_collect = (
        not book_list.description
        or not book_list.isbn
        or not book_list.thumbnail_url
        or not book_list.toc
    )
    if needs_collect:
        _log("도서 정보 수집 중 (알라딘 → YES24 → 교보문고)...")
        info, aladin_candidates = fetch_book_info_with_fallback(
            book_list.title, author_name, return_candidates=True
        )
        if info.get("thumbnail_url") and not book_list.thumbnail_url:
            book_list.thumbnail_url = info["thumbnail_url"]
            update_fields.append("thumbnail_url")
        if info.get("description") and not book_list.description:
            book_list.description = info["description"]
            update_fields.append("description")
        if info.get("isbn") and not book_list.isbn:
            book_list.isbn = info["isbn"]
            update_fields.append("isbn")
        if info.get("toc") and not book_list.toc:
            book_list.toc = info["toc"]
            update_fields.append("toc")

    # 2) 난이도 분류 — difficulty가 없고 description이 있을 때만 실행
    if not book_list.difficulty and book_list.description:
        _log("난이도 분류 중...")
        difficulty = classify_difficulty(book_list.title, book_list.description, [])
        if difficulty and difficulty in ("입문", "초급", "중급", "고급"):
            book_list.difficulty = difficulty
            update_fields.append("difficulty")
    elif not book_list.difficulty and not book_list.description:
        _log("난이도 분류 생략 — description 없음")

    if update_fields:
        book_list.save(update_fields=update_fields)

    # 3) 임베딩 생성 — 이미 존재하면 건너뜀
    try:
        embedding_exists = BookEmbedding.objects.filter(book_list=book_list).exists()
    except Exception:
        embedding_exists = False

    if not embedding_exists and book_list.description:
        _log("임베딩 생성 중...")
        generate_embedding(book_list.pk, book_list.title, book_list.description)
    else:
        _log("임베딩 이미 존재 — 건너뜀")

    if return_candidates:
        return aladin_candidates
    return None


def run_book_pipeline(
    book, progress_callback=None, return_candidates: bool = False
) -> list[dict] | None:
    """Book 인스턴스 기반 파이프라인 — BookList 단위로 위임 (하위 호환)."""
    return run_booklist_pipeline(book.book_list, progress_callback, return_candidates)


def collect_book_data(book_list, force: bool = False) -> dict:
    """개별 도서 데이터 수집 (force=True이면 기존 데이터 덮어쓰기).

    반환: {"updated_fields": [...], "thumbnail": bool, "description": bool, "toc": bool, "difficulty": str}
    """
    update_fields = []
    info = fetch_book_info_with_fallback(book_list.title, book_list.author.name)

    if info.get("thumbnail_url") and (force or not book_list.thumbnail_url):
        book_list.thumbnail_url = info["thumbnail_url"]
        update_fields.append("thumbnail_url")
    if info.get("description") and (force or not book_list.description):
        book_list.description = info["description"]
        update_fields.append("description")
    if info.get("isbn") and (force or not book_list.isbn):
        book_list.isbn = info["isbn"]
        update_fields.append("isbn")
    if info.get("toc") and (force or not book_list.toc):
        book_list.toc = info["toc"]
        update_fields.append("toc")

    if update_fields:
        book_list.save(update_fields=update_fields)

    # 난이도 분류 (description 있을 때)
    if book_list.description and (force or not book_list.difficulty):
        difficulty = classify_difficulty(book_list.title, book_list.description, [])
        if difficulty and difficulty in ("입문", "초급", "중급", "고급"):
            book_list.difficulty = difficulty
            book_list.save(update_fields=["difficulty"])
            if "difficulty" not in update_fields:
                update_fields.append("difficulty")

    return {
        "updated_fields": update_fields,
        "thumbnail":    bool(book_list.thumbnail_url),
        "description":  bool(book_list.description),
        "toc":          bool(book_list.toc),
        "difficulty":   book_list.difficulty,
    }


def refresh_embedding(book_list) -> bool:
    """임베딩 강제 재생성 (description 없으면 생략)."""
    if not book_list.description:
        logger.info("임베딩 재생성 생략 — description 없음 (book_list_id=%s)", book_list.pk)
        return False
    logger.info("임베딩 재생성 시작 (book_list_id=%s)", book_list.pk)
    return generate_embedding(book_list.pk, book_list.title, book_list.description)
