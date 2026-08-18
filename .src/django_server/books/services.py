"""
도서 데이터 수집 서비스
- 교보문고 스크래핑: 썸네일, 소개, 저자, 출판사, 목차
- FastAPI 모델 서버: 난이도 분류, 임베딩 생성
중복 방지: BookList 단위로 데이터 수집 여부를 판단하여 불필요한 API 호출을 방지합니다.
"""

import logging
import posixpath
import re
import time
import urllib.parse
from urllib.parse import urlparse

import httpx
import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.files.base import ContentFile

from .models import BookList, Category

logger = logging.getLogger(__name__)


_CONTENT_TYPE_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/png":  ".png",
    "image/webp": ".webp",
    "image/gif":  ".gif",
}


def save_thumbnail(book_list, url: str) -> bool:
    """URL에서 이미지를 다운로드하여 book_list.thumbnail 필드에 저장합니다.

    저장만 수행하고 model.save()는 호출하지 않습니다 — 호출 측에서 update_fields에
    'thumbnail'을 포함하여 저장하세요.

    Content-Type이 image/* 가 아니면 저장하지 않고 False를 반환합니다.
    """
    if not url:
        return False
    try:
        resp = requests.get(
            url, timeout=10,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
            allow_redirects=True,
        )
        resp.raise_for_status()

        # Content-Type 검증 — HTML 오류 페이지나 비이미지 응답이 저장되는 것을 방지
        content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            logger.warning(
                "썸네일 Content-Type 오류 — 이미지가 아님 "
                "(book_list_id=%s, content_type=%s, url=%s)",
                book_list.pk, content_type, url,
            )
            return False

        # 확장자: URL 경로 우선, 없으면 Content-Type으로 결정
        ext = posixpath.splitext(urlparse(url).path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = _CONTENT_TYPE_TO_EXT.get(content_type, ".jpg")

        filename = f"{book_list.pk}{ext}"
        book_list.thumbnail.save(filename, ContentFile(resp.content), save=False)
        return True
    except Exception as exc:
        logger.warning("썸네일 다운로드 실패 (book_list_id=%s): %s", book_list.pk, exc)
        return False


def bulk_refresh_thumbnails(queryset, on_item=None) -> tuple[int, int]:
    """주어진 BookList 쿼리셋의 썸네일을 일괄 재다운로드합니다.

    기존 파일을 먼저 삭제한 뒤 thumbnail_url에서 재다운로드합니다 (덮어쓰기 보장).
    다운로드 실패 + 기존 파일이 삭제된 경우 DB 필드도 함께 비워 일치시킵니다.
    on_item(book_list, success: bool)이 주어지면 각 항목 처리 후 호출됩니다
    (management command의 진행 로그 출력 등에 사용).

    반환: (성공 건수, 실패 건수)
    """
    ok = fail = 0
    for bl in queryset.iterator():
        if bl.thumbnail:
            bl.thumbnail.delete(save=False)
        success = save_thumbnail(bl, bl.thumbnail_url)
        if success:
            bl.save(update_fields=["thumbnail"])
            ok += 1
        else:
            if not bl.thumbnail.name:
                bl.save(update_fields=["thumbnail"])
            fail += 1
        if on_item:
            on_item(bl, success)
    return ok, fail

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
                return desc_part[:2000], toc_part[:8000]

    return text, ""


# ── 교보문고 스크래핑 ─────────────────────────────────────────

# product.kyobobook.co.kr은 CloudFront 뒤에 있어 Referer/Accept 헤더가 없으면
# User-Agent가 정상이어도 빈 응답(200, Content-Length 0)을 반환한다.
_KYOBO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://www.kyobobook.co.kr/",
}


def fetch_kyobo_book_info(title: str) -> dict | None:
    """교보문고 검색 결과 중 첫 번째 후보의 상세 정보를 스크래핑."""
    candidates = fetch_kyobo_candidates(title, max_results=1)
    if not candidates:
        return None
    time.sleep(0.5)
    return fetch_kyobo_book_detail(candidates[0]["href"])


def fetch_kyobo_book_detail(href: str) -> dict | None:
    """교보문고 상세 페이지 링크(product.kyobobook.co.kr/detail/...)에서 도서 정보 수집.

    수집 항목: 썸네일, 도서명, 부제, 저자, 출판사, 출판 연도, 설명, 목차.
    목차는 도서에 따라 교보문고에 데이터가 없을 수 있어 없으면 생략한다.
    """
    try:
        resp = requests.get(href, headers=_KYOBO_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        result: dict = {}

        # 도서명
        title_tag = soup.select_one(".prod_title")
        if title_tag:
            result["title"] = title_tag.get_text(strip=True)

        # 부제(간단 소개 문구)
        subtitle_tag = soup.select_one("#bookSimpleIntro")
        if subtitle_tag:
            subtitle_text = subtitle_tag.get_text(strip=True)
            if subtitle_text:
                result["subtitle"] = subtitle_text

        # 저자/역자 — 여러 명인 경우 쉼표로 연결
        author_info = soup.select_one("#author-info")
        if author_info:
            authors = [a.get_text(strip=True) for a in author_info.select("a")]
            if authors:
                result["author"] = ", ".join(authors)

        # 출판사 + 출판 연도
        publisher_info = soup.select_one("#publisher-info")
        if publisher_info:
            pub_link = publisher_info.select_one("a")
            if pub_link:
                result["publisher"] = pub_link.get_text(strip=True)
            year_match = re.search(r"(\d{4})년", publisher_info.get_text())
            if year_match:
                result["publication_year"] = int(year_match.group(1))

        # 썸네일
        thumb_tag = soup.select_one('img[alt$="대표 이미지"]')
        if thumb_tag and thumb_tag.get("src"):
            result["thumbnail_url"] = thumb_tag["src"]

        # 도서 소개
        intro_tag = soup.select_one("#bookDescription")
        if not intro_tag:
            intro_tag = soup.select_one(".intro_bottom") or soup.select_one("[data-tab-cont='introduce']")
        if intro_tag:
            text = intro_tag.get_text(separator="\n", strip=True)
            if text:
                result["description"] = text[:2000]

        # 목차 — 상세 페이지 HTML에는 스켈레톤만 내려오고(클라이언트에서 뷰포트에
        # 들어올 때 렌더링), 실제 텍스트는 내부 컴포넌트 API에서 별도로 받아온다.
        item_id = href.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        if item_id:
            try:
                comp_resp = requests.get(
                    f"https://product.kyobobook.co.kr/api/gw/pdt/v2/product/component/{item_id}/middle",
                    headers=_KYOBO_HEADERS, timeout=10,
                )
                comp_resp.raise_for_status()
                toc_text = comp_resp.json().get("data", {}).get("middle", {}).get("contentTableList")
                if toc_text:
                    result["toc"] = toc_text.strip()
            except Exception as exc:
                logger.warning("교보문고 목차 API 수집 실패 (%s): %s", item_id, exc)

        return result if result else None
    except Exception as exc:
        logger.warning("교보문고 상세 수집 실패 (%s): %s", href, exc)
        return None


# ── 교보문고 검색 (후보 수집) ─────────────────────────────────

def fetch_kyobo_candidates(title: str, max_results: int = 10) -> list[dict]:
    """교보문고 검색 결과에서 후보 도서 목록 수집.

    수집 항목: 도서명, 링크(href), 저자(subtitle에 담음), 판차 정보(edition_info)
    전자책(ebook-product.kyobobook.co.kr)은 제외하고 종이책만 대상으로 한다.

    검색 결과는 `.prod_item` 중 추천 캐러셀(`.swiper-container`) 하위가 아닌
    항목만 유효하다 — 캐러셀 쪽 `.prod_item`은 실제 검색 결과가 아니다.
    """
    try:
        search_url = (
            "https://search.kyobobook.co.kr/search"
            f"?keyword={urllib.parse.quote(_normalize_title(title))}"
        )
        resp = requests.get(search_url, headers=_KYOBO_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        candidates = []
        for item in soup.select(".prod_item"):
            if item.find_parent(class_="swiper-container"):
                continue

            link_tag = item.select_one("a.prod_link[href]") or item.select_one('a[href*="/detail/"]')
            if not link_tag:
                continue
            href = link_tag.get("href", "")
            if urlparse(href).netloc != "product.kyobobook.co.kr":
                continue

            name_tag = item.select_one(".prod_name_group a.prod_info")
            if not name_tag:
                continue
            category_tag = name_tag.select_one(".prod_category")
            if category_tag:
                category_tag.extract()
            title_text = name_tag.get_text(strip=True)
            if not title_text:
                continue

            author_tag = item.select_one(".prod_author_info a.author")
            edition_tag = item.select_one(".prod_desc_info .prod_desc.normal")

            candidates.append({
                "title":        title_text,
                "href":         href,
                "subtitle":     author_tag.get_text(strip=True) if author_tag else "",
                "edition_info": edition_tag.get_text(strip=True) if edition_tag else "",
            })
            if len(candidates) >= max_results:
                break

        return candidates
    except Exception as exc:
        logger.warning("교보문고 후보 수집 실패 (%s): %s", title, exc)
        return []


# ── URL 기반 수집 (수정 폼 수집 버튼용) ──────────────────────────

def clean_toc_artifacts(text: str) -> str:
    """목차 텍스트에서 스크래핑 시 남는 UI 잔여 문자(접어보기 등)를 제거하고 정리합니다."""
    text = re.sub(r'(?m)^\s*(미리보기|펼쳐보기|접기|접어보기)\s*$', '', text)
    text = re.sub(r'\s*(접어보기)\s*', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def scrape_from_url(url: str) -> dict:
    """링크를 분석해 소스를 판별하고 도서 정보를 수집합니다.

    반환 형태:
        {"status": "ok",          "data": {...}}   — 수집 성공
        {"status": "unsupported"}                   — 미지원 소스
        {"status": "error",       "message": str}  — 수집 실패
    """
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().lstrip("www.")

    if "kyobobook.co.kr" in host:
        data = fetch_kyobo_book_detail(url)
        if data:
            return {"status": "ok", "data": data}
        return {"status": "error", "message": "교보문고 페이지에서 데이터를 가져오지 못했습니다."}

    return {"status": "unsupported"}


# ── 통합 수집 ─────────────────────────────────────────────────

def fetch_book_info(
    title: str, author: str = "", return_candidates: bool = False
) -> dict | tuple[dict, list[dict]]:
    """교보문고에서 도서 정보 수집.

    description에 목차 섹션이 포함된 경우 자동으로 분리합니다.
    return_candidates=True이면 (collected, candidates) 튜플 반환.
    """
    collected: dict = {"thumbnail_url": "", "description": "", "toc": ""}

    candidates = fetch_kyobo_candidates(title)
    if candidates:
        info = fetch_kyobo_book_detail(candidates[0]["href"])
        if info:
            if info.get("thumbnail_url"):
                collected["thumbnail_url"] = info["thumbnail_url"]
            if info.get("description"):
                desc, toc_from_desc = _split_description_toc(info["description"])
                collected["description"] = desc
                if toc_from_desc:
                    collected["toc"] = toc_from_desc
            if info.get("toc") and not collected["toc"]:
                collected["toc"] = info["toc"]

    if return_candidates:
        return collected, candidates
    return collected


# ── 유틸 ─────────────────────────────────────────────────────

def normalize_book_code(code: str) -> str:
    """도서 코드 정규화. 숫자만 입력되면 'D-nnn' 형식으로 변환합니다.

    이미 접두사(D-, 하이픈 등)가 포함되어 있으면 그대로 반환합니다.
    """
    code = code.strip()
    if code.isdigit():
        return f"D-{code}"
    return code


def parse_book_codes(raw: str) -> list[str]:
    """쉼표로 구분된 도서 코드 문자열을 정규화된 코드 목록으로 변환합니다.

    각 항목의 앞뒤 공백을 제거하고 normalize_book_code를 적용하며,
    중복 코드는 입력 순서를 유지한 채 제거합니다.
    """
    codes = [normalize_book_code(c) for c in raw.split(",") if c.strip()]
    return list(dict.fromkeys(codes))


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


# ── 도서 추가/수정 폼 공용 헬퍼 (book_add / book_edit에서 공유) ─────

def extract_book_form(request) -> dict:
    """도서 추가/수정 폼의 POST 데이터를 파싱합니다."""
    pub_year_str = request.POST.get("publication_year", "").strip()
    return {
        "title":             request.POST.get("title", "").strip(),
        "subtitle":          request.POST.get("subtitle", "").strip(),
        "edition":           request.POST.get("edition", "").strip(),
        "author_name":       request.POST.get("author", "").strip(),
        "publisher_name":    request.POST.get("publisher", "").strip(),
        "difficulty":        request.POST.get("difficulty", ""),
        "publication_year":  int(pub_year_str) if pub_year_str.isdigit() else None,
        "thumbnail_url":     request.POST.get("thumbnail_url", "").strip(),
        "description":       request.POST.get("description", "").strip(),
        "toc":               request.POST.get("toc", "").strip(),
        "source_url":        request.POST.get("source_url", "").strip(),
        "category_ids":      request.POST.getlist("categories"),
        "is_active":         request.POST.get("is_active") == "on",
        "confirm_link":      request.POST.get("confirm_link") == "true",
    }


def find_duplicate_booklist(title, edition, author, publisher, exclude_pk: int | None = None):
    """동일한 (title, edition, author, publisher) BookList가 있으면 반환합니다 (없으면 None).

    exclude_pk를 지정하면 해당 pk의 BookList는 검색 대상에서 제외합니다
    (수정 화면에서 "자기 자신과의 중복"을 중복으로 취급하지 않기 위함).
    """
    qs = BookList.objects.filter(title=title, edition=edition, author=author, publisher=publisher)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def auto_classify_book(
    title: str, description: str, difficulty: str, category_ids: list, difficulties
) -> tuple[str, list]:
    """난이도·카테고리가 미선택이고 도서 소개가 있으면 LLM으로 자동 분류합니다.

    이미 선택된 값이 있으면 그대로 반환합니다 (자동 분류로 덮어쓰지 않음).
    """
    if not difficulty and description:
        classified_difficulty = classify_difficulty(title, description, [])
        if classified_difficulty in dict(difficulties):
            difficulty = classified_difficulty
    if not category_ids and description:
        classified_names = classify_category(title, description)
        if classified_names:
            category_ids = list(
                Category.objects.filter(name__in=classified_names).values_list("id", flat=True)
            )
    return difficulty, category_ids


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

    1. 교보문고 스크래핑 (description·thumbnail·toc 중 비어있는 필드만 채움)
    2. LLM 난이도 분류 (difficulty 없을 때만, description 기반)
    3. 임베딩 생성 (embedding 없을 때만)

    return_candidates=True이면 교보문고 후보 목록을 반환합니다 (없으면 []).
    """
    from .models import BookEmbedding

    author_name = book_list.get_author_display()
    update_fields = []
    candidates: list[dict] = []

    def _log(msg):
        if progress_callback:
            progress_callback(msg)
        else:
            logger.info(msg)

    # 1) 수집이 필요한 필드가 하나라도 비어있으면 수집
    needs_collect = (
        not book_list.description
        or not book_list.thumbnail_url
        or not book_list.toc
    )
    if needs_collect:
        _log("도서 정보 수집 중 (교보문고)...")
        info, candidates = fetch_book_info(
            book_list.title, author_name, return_candidates=True
        )
        if info.get("thumbnail_url") and not book_list.thumbnail_url:
            book_list.thumbnail_url = info["thumbnail_url"]
            update_fields.append("thumbnail_url")
        if info.get("description") and not book_list.description:
            book_list.description = info["description"]
            update_fields.append("description")
        if info.get("toc") and not book_list.toc:
            book_list.toc = info["toc"]
            update_fields.append("toc")

    # 썸네일 이미지 저장 — URL은 있지만 저장된 파일이 없는 경우
    if book_list.thumbnail_url and not book_list.thumbnail:
        _log("썸네일 이미지 저장 중...")
        if save_thumbnail(book_list, book_list.thumbnail_url):
            update_fields.append("thumbnail")

    # 2) 난이도 분류 — difficulty가 없고 description이 있을 때만 실행
    if not book_list.difficulty and book_list.description:
        _log("난이도 분류 중...")
        difficulty = classify_difficulty(book_list.title, book_list.description, [])
        if difficulty and difficulty in BookList.Difficulty.values:
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
        return candidates
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
    info = fetch_book_info(book_list.title, book_list.author.name)

    if info.get("thumbnail_url") and (force or not book_list.thumbnail_url):
        book_list.thumbnail_url = info["thumbnail_url"]
        update_fields.append("thumbnail_url")
    if info.get("description") and (force or not book_list.description):
        book_list.description = info["description"]
        update_fields.append("description")
    if info.get("toc") and (force or not book_list.toc):
        book_list.toc = info["toc"]
        update_fields.append("toc")

    # 썸네일 이미지 저장 — URL은 있지만 저장된 파일이 없는 경우 (force 시 재다운로드)
    if book_list.thumbnail_url and (force or not book_list.thumbnail):
        if save_thumbnail(book_list, book_list.thumbnail_url):
            update_fields.append("thumbnail")

    if update_fields:
        book_list.save(update_fields=update_fields)

    # 난이도 분류 (description 있을 때)
    if book_list.description and (force or not book_list.difficulty):
        difficulty = classify_difficulty(book_list.title, book_list.description, [])
        if difficulty and difficulty in BookList.Difficulty.values:
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
