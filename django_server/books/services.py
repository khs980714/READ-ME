"""
도서 데이터 수집 서비스
- Naver Book Search API: 썸네일, 소개, ISBN
- 리뷰 스크래핑: 도서 리뷰 텍스트
- FastAPI 모델 서버: 난이도 분류, 임베딩 생성
중복 방지: BookList 단위로 데이터 수집 여부를 판단하여 불필요한 API 호출을 방지합니다.
"""

import logging
import re
import time

import httpx
import requests
from bs4 import BeautifulSoup
from django.conf import settings

logger = logging.getLogger(__name__)

NAVER_BOOK_URL = "https://openapi.naver.com/v1/search/book.json"
NAVER_HEADERS = {
    "X-Naver-Client-Id": settings.NAVER_CLIENT_ID,
    "X-Naver-Client-Secret": settings.NAVER_CLIENT_SECRET,
}


# ── Naver Book Search API ─────────────────────────────────────

def fetch_naver_book_info(title: str, author: str = "") -> dict | None:
    """Naver Book Search API로 도서 정보 조회."""
    query = f"{_normalize_title(title)} {author}".strip()
    try:
        resp = requests.get(
            NAVER_BOOK_URL,
            headers=NAVER_HEADERS,
            params={"query": query, "display": 1},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return None
        item = items[0]
        return {
            "thumbnail_url": item.get("image", ""),
            "description": _strip_html(item.get("description", "")),
            "isbn": item.get("isbn", "").split()[-1] if item.get("isbn") else "",
            "published_at": _parse_pubdate(item.get("pubdate", "")),
        }
    except Exception as exc:
        logger.warning("Naver API 오류 (%s): %s", title, exc)
        return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _normalize_title(title: str) -> str:
    """검색용 제목 정규화 — '(개정판)', '(2판)', '[개정증보판]' 등 부제 제거."""
    # 괄호·대괄호 안 '판', '개정', '증보', '완전', '전면', '개편' 등 판형 표현 제거
    normalized = re.sub(r"[\(\[（［][^\)\]）］]*?(개정|증보|완전|전면|개편|판|edition)[^\)\]）］]*?[\)\]）］]", "", title, flags=re.IGNORECASE)
    return normalized.strip()


def _parse_pubdate(pubdate: str):
    """'20240101' → date object."""
    from datetime import date
    if len(pubdate) == 8 and pubdate.isdigit():
        try:
            return date(int(pubdate[:4]), int(pubdate[4:6]), int(pubdate[6:8]))
        except ValueError:
            pass
    return None


# ── 리뷰 스크래핑 (예스24) ───────────────────────────────────

def scrape_reviews(title: str, max_reviews: int = 10) -> list[str]:
    """예스24 검색 결과 페이지에서 독자 리뷰를 스크래핑."""
    reviews = []
    try:
        search_url = f"https://www.yes24.com/Product/Search?query={requests.utils.quote(_normalize_title(title))}&domain=BOOK"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(search_url, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        link_tag = soup.select_one(".itemName a")
        if not link_tag:
            return []
        product_url = "https://www.yes24.com" + link_tag["href"]

        time.sleep(0.5)
        resp2 = requests.get(product_url, headers=headers, timeout=10)
        soup2 = BeautifulSoup(resp2.text, "lxml")

        for tag in soup2.select(".reviewInfoBot .txtReview")[:max_reviews]:
            text = tag.get_text(strip=True)
            if text:
                reviews.append(text)
    except Exception as exc:
        logger.warning("리뷰 스크래핑 실패 (%s): %s", title, exc)
    return reviews


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

def run_book_pipeline(book, progress_callback=None) -> None:
    """
    도서 데이터 수집 파이프라인 (BookList 단위 중복 방지):
    1. Naver API → description·isbn·thumbnail 중 하나라도 비어있으면 호출,
                   이미 채워진 필드는 덮어쓰지 않음
    2. 리뷰 스크래핑
    3. LLM 난이도 분류 (difficulty 없을 때만)
    4. 임베딩 생성 (embedding 없을 때만)
    """
    from .models import BookEmbedding

    book_list = book.book_list
    author_name = book_list.get_author_display()
    update_fields = []

    def _log(msg):
        if progress_callback:
            progress_callback(msg)
        else:
            logger.info(msg)

    # 1) Naver API — 수집이 필요한 필드가 하나라도 비어있으면 호출
    #    이미 입력된 필드(description 포함)는 덮어쓰지 않음
    needs_naver = (
        not book_list.description
        or not book_list.isbn
        or not book_list.thumbnail_url
    )
    if needs_naver:
        _log("Naver API 조회 중...")
        info = fetch_naver_book_info(book_list.title, author_name)
        if info:
            if info.get("thumbnail_url") and not book_list.thumbnail_url:
                book_list.thumbnail_url = info["thumbnail_url"]
                update_fields.append("thumbnail_url")
            if info.get("description") and not book_list.description:
                book_list.description = info["description"]
                update_fields.append("description")
            if info.get("isbn") and not book_list.isbn:
                book_list.isbn = info["isbn"]
                update_fields.append("isbn")
            if info.get("published_at") and not book_list.published_at:
                book_list.published_at = info["published_at"]
                update_fields.append("published_at")

    # 2) 리뷰 스크래핑
    reviews = []
    if not book_list.difficulty:
        _log("리뷰 스크래핑 중...")
        reviews = scrape_reviews(book_list.title)

    # 3) 난이도 분류 — difficulty가 없고 description이 있을 때만 실행
    #    description 없이 제목만으로 분류하면 결과가 부정확하므로 건너뜀
    if not book_list.difficulty and book_list.description:
        _log("난이도 분류 중...")
        difficulty = classify_difficulty(book_list.title, book_list.description, reviews)
        if difficulty and difficulty in ("입문", "초급", "중급", "고급"):
            book_list.difficulty = difficulty
            update_fields.append("difficulty")
    elif not book_list.difficulty and not book_list.description:
        _log("난이도 분류 생략 — description 없음")

    if update_fields:
        book_list.save(update_fields=update_fields)

    # 4) 임베딩 생성 — 이미 존재하면 건너뜀
    try:
        embedding_exists = BookEmbedding.objects.filter(book_list=book_list).exists()
    except Exception:
        embedding_exists = False

    if not embedding_exists:
        _log("임베딩 생성 중...")
        generate_embedding(book_list.pk, book_list.title, book_list.description)
    else:
        _log("임베딩 이미 존재 — 건너뜀")


def refresh_embedding(book_list) -> bool:
    """
    도서 설명(description)이 직접 입력·수정된 경우 임베딩을 강제 재생성합니다.
    run_book_pipeline과 달리 기존 embedding 존재 여부와 관계없이 항상 재생성합니다.
    description이 비어있으면 실행하지 않습니다.
    """
    if not book_list.description:
        logger.info("임베딩 재생성 생략 — description 없음 (book_list_id=%s)", book_list.pk)
        return False
    logger.info("임베딩 재생성 시작 (book_list_id=%s)", book_list.pk)
    return generate_embedding(book_list.pk, book_list.title, book_list.description)
