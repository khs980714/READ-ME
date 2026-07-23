"""
도서 데이터 수집 서비스
- 알라딘 Open API (TTB): 썸네일, 소개, ISBN, 목차 (1차)
- YES24 스크래핑: 소개, 목차 (2차 fallback)
- 교보문고 스크래핑: 소개, 목차 (3차 fallback)
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

logger = logging.getLogger(__name__)


_CONTENT_TYPE_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/png":  ".png",
    "image/webp": ".webp",
    "image/gif":  ".gif",
}


def _save_thumbnail(book_list, url: str) -> bool:
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
      primary_info: 첫 번째 결과의 thumbnail/description/toc, 없으면 None
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
                "item_id":       str(it.get("itemId", "")),
            }
            for it in items
        ]

        item = items[0]
        result: dict = {
            "thumbnail_url": item.get("cover", ""),
            "description":   item.get("description", "")[:2000],
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
        soup = BeautifulSoup(resp.text, "html.parser")

        link_tag = soup.select_one(".itemName a")
        if not link_tag:
            return None
        product_url = "https://www.yes24.com" + link_tag["href"]

        time.sleep(0.5)
        resp2 = requests.get(product_url, headers=headers, timeout=10)
        soup2 = BeautifulSoup(resp2.text, "html.parser")

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
    """교보문고 검색 결과에서 도서 설명 + 목차 스크래핑."""
    try:
        search_url = (
            "https://search.kyobobook.co.kr/search"
            f"?keyword={urllib.parse.quote(_normalize_title(title))}&target=BOOK"
        )
        resp = requests.get(search_url, headers=_KYOBO_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        link_tag = soup.select_one(".prod_name a")
        if not link_tag:
            return None
        product_url = link_tag.get("href", "")
        if not product_url.startswith("http"):
            product_url = "https://product.kyobobook.co.kr" + product_url

        time.sleep(0.5)
        return fetch_kyobo_book_detail(product_url)
    except Exception as exc:
        logger.warning("교보문고 수집 실패 (%s): %s", title, exc)
        return None


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


# ── YES24 1차 검색 (후보 수집) ────────────────────────────────

def fetch_yes24_candidates(title: str) -> list[dict]:
    """YES24 검색 결과에서 후보 도서 목록 수집 (1차).

    수집 항목: 도서명(gd_name), 링크(href), 부제(gd_nameE), 개정 정보(feature)
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        search_url = (
            "https://www.yes24.com/Product/Search"
            f"?query={urllib.parse.quote(_normalize_title(title))}&domain=BOOK"
        )
        resp = requests.get(search_url, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        candidates = []
        for name_tag in soup.select("a.gd_name")[:10]:
            raw_href = name_tag.get("href", "")
            if not raw_href:
                continue
            href = (
                "https://www.yes24.com" + raw_href
                if raw_href.startswith("/")
                else raw_href
            )
            title_text = name_tag.get_text(strip=True)

            # 부모 컨테이너에서 부제·개정 정보 탐색
            container = name_tag.parent
            for _ in range(6):
                if container is None:
                    break
                if container.select_one(".gd_nameE") or container.select_one(".feature"):
                    break
                container = container.parent

            subtitle = ""
            edition_info = ""
            if container:
                sub_tag  = container.select_one(".gd_nameE")
                feat_tag = container.select_one(".feature")
                if sub_tag:
                    subtitle = sub_tag.get_text(strip=True)
                if feat_tag:
                    edition_info = feat_tag.get_text(strip=True)

            if title_text and href:
                candidates.append({
                    "title":        title_text,
                    "href":         href,
                    "subtitle":     subtitle,
                    "edition_info": edition_info,
                })

        return candidates
    except Exception as exc:
        logger.warning("YES24 후보 수집 실패 (%s): %s", title, exc)
        return []


# ── YES24 2차 상세 수집 ───────────────────────────────────────

def _clean_yes24_text(tag, max_chars: int | None = None) -> str:
    """YES24 콘텐츠 영역에서 순수 텍스트를 추출합니다.

    - 버튼/링크 등 UI 요소 제거
    - br 태그 → 개행 변환
    - 이중 HTML 인코딩(텍스트 안에 <b>, <br/> 등) 처리
    - '미리보기', '펼쳐보기', '접기' UI 잔여 문자 제거
    """
    import re as _re

    # UI 요소 제거 (버튼, 더보기 링크 등)
    for el in tag.select("button, .moreBtn, .btnWrap, .more_btn, a.btn_more, .infoSetBtn"):
        el.decompose()

    # <br> → 개행
    for br in tag.find_all("br"):
        br.replace_with("\n")

    raw = tag.get_text(separator="\n", strip=True)

    # YES24는 콘텐츠를 HTML 엔티티로 이중 인코딩하는 경우가 있어
    # get_text() 후에도 <b>, <br/> 등의 태그 문자가 남을 수 있음 → 재파싱
    if "<" in raw and ">" in raw:
        inner = BeautifulSoup(raw, "html.parser")
        for br in inner.find_all("br"):
            br.replace_with("\n")
        raw = inner.get_text(separator="\n", strip=True)

    # UI 잔여 텍스트 제거 (줄 단위 및 인라인)
    raw = _re.sub(r'(?m)^\s*(미리보기|펼쳐보기|접기|접어보기)\s*$', '', raw)
    raw = _re.sub(r'\s*(미리보기|펼쳐보기|접기|접어보기)\s*', ' ', raw)
    raw = _re.sub(r'(?m)^\s*책의 일부 내용을 미리 읽어보실 수 있습니다\.?\s*$', '', raw)

    # 연속 공백·개행 정리
    raw = _re.sub(r'[ \t]+', ' ', raw)
    raw = _re.sub(r'\n{3,}', '\n\n', raw)
    raw = raw.strip()

    if max_chars:
        raw = raw[:max_chars]
    return raw


def fetch_yes24_book_detail(href: str) -> dict | None:
    """YES24 도서 상세 페이지에서 전체 정보 수집 (2차).

    수집 항목: 썸네일, 도서명, 부제, 저자, 출판사, 개정 정보, 설명, 목차
    '펼쳐보기' 상태의 전체 내용(infoSetContAll)을 우선 수집합니다.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(href, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        result: dict = {}

        # 썸네일
        thumb_tag = soup.select_one(".gd_img img")
        if thumb_tag and thumb_tag.get("src"):
            result["thumbnail_url"] = thumb_tag["src"]

        # 도서명
        title_tag = soup.select_one("h2.gd_name") or soup.select_one(".gd_name")
        if title_tag:
            result["title"] = title_tag.get_text(strip=True)

        # 부제
        subtitle_tag = soup.select_one(".gd_nameE")
        if subtitle_tag:
            subtitle_text = subtitle_tag.get_text(strip=True)
            if subtitle_text:
                result["subtitle"] = subtitle_text

        # 저자
        auth_tag = soup.select_one(".gd_auth")
        if auth_tag:
            result["author"] = auth_tag.get_text(separator=" ", strip=True)

        # 출판사
        pub_tag = soup.select_one(".gd_pub")
        if pub_tag:
            result["publisher"] = pub_tag.get_text(strip=True)

        # 개정 정보
        feat_tag = soup.select_one(".feature")
        if feat_tag:
            result["edition_info"] = feat_tag.get_text(strip=True)

        # 도서 소개 — 펼쳐보기 전체 내용(infoSetContAll) 우선, 없으면 기본 영역
        intro_wrap = soup.select_one("#infoset_introduce .infoSetCont_wrap")
        if not intro_wrap:
            intro_wrap = soup.select_one(".Wrrapper.infoSetCont_wrap")
        if intro_wrap:
            full_el = intro_wrap.select_one(".infoSetContAll") or intro_wrap.select_one(".infoSetCont")
            target = full_el if full_el else intro_wrap
            text = _clean_yes24_text(target, max_chars=2000)
            if text:
                result["description"] = text

        # 목차 — 펼쳐보기 전체 내용 우선
        toc_wrap = soup.select_one("#infoset_toc .infoSetCont_wrap")
        if not toc_wrap:
            toc_wrap = soup.select_one("#infoset_toc")
        if toc_wrap:
            full_el = toc_wrap.select_one(".infoSetContAll") or toc_wrap.select_one(".infoSetCont")
            target = full_el if full_el else toc_wrap
            text = _clean_yes24_text(target, max_chars=3000)
            if text:
                result["toc"] = text

        return result if result else None
    except Exception as exc:
        logger.warning("YES24 상세 수집 실패 (%s): %s", href, exc)
        return None


# ── URL 기반 수집 (수정 폼 수집 버튼용) ──────────────────────────

def clean_toc_artifacts(text: str) -> str:
    """목차 텍스트에서 YES24 UI 잔여 문자(접어보기 등)를 제거하고 정리합니다."""
    import re as _re
    text = _re.sub(r'(?m)^\s*(미리보기|펼쳐보기|접기|접어보기)\s*$', '', text)
    text = _re.sub(r'\s*(접어보기)\s*', ' ', text)
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def scrape_from_url(url: str) -> dict:
    """링크를 분석해 소스를 판별하고 도서 정보를 수집합니다.

    반환 형태:
        {"status": "ok",              "data": {...}}   — 수집 성공
        {"status": "not_implemented", "source": str}  — 구현 전 소스 (알라딘)
        {"status": "unsupported"}                      — 미지원 소스
        {"status": "error",           "message": str} — 수집 실패
    """
    from urllib.parse import urlparse
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().lstrip("www.")

    if "yes24.com" in host:
        data = fetch_yes24_book_detail(url)
        if data:
            return {"status": "ok", "data": data}
        return {"status": "error", "message": "YES24 페이지에서 데이터를 가져오지 못했습니다."}

    if "kyobobook.co.kr" in host:
        data = fetch_kyobo_book_detail(url)
        if data:
            return {"status": "ok", "data": data}
        return {"status": "error", "message": "교보문고 페이지에서 데이터를 가져오지 못했습니다."}

    if "aladin.co.kr" in host or "aladdin.co.kr" in host:
        return {"status": "not_implemented", "source": "aladin"}

    return {"status": "unsupported"}


# ── 멀티소스 통합 수집 ────────────────────────────────────────

def fetch_book_info_with_fallback(
    title: str, author: str = "", return_candidates: bool = False
) -> dict | tuple[dict, list[dict]]:
    """도서 정보 수집 — 우선순위: 알라딘 API → YES24 → 교보문고.

    각 소스에서 비어있는 필드를 채워가며 수집합니다.
    description에 목차 섹션이 포함된 경우 자동으로 분리합니다.

    return_candidates=True이면 (collected, aladin_candidates) 튜플 반환.
    """
    collected: dict = {"thumbnail_url": "", "description": "", "toc": ""}

    # 1차: 알라딘 API (후보 목록 포함)
    aladin_info, candidates = fetch_aladin_book_info(title)
    if aladin_info:
        for key in ("thumbnail_url", "toc"):
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
       (description·thumbnail·toc 중 비어있는 필드만 채움)
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
        if info.get("toc") and not book_list.toc:
            book_list.toc = info["toc"]
            update_fields.append("toc")

    # 썸네일 이미지 저장 — URL은 있지만 저장된 파일이 없는 경우
    if book_list.thumbnail_url and not book_list.thumbnail:
        _log("썸네일 이미지 저장 중...")
        if _save_thumbnail(book_list, book_list.thumbnail_url):
            update_fields.append("thumbnail")

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
    if info.get("toc") and (force or not book_list.toc):
        book_list.toc = info["toc"]
        update_fields.append("toc")

    # 썸네일 이미지 저장 — URL은 있지만 저장된 파일이 없는 경우 (force 시 재다운로드)
    if book_list.thumbnail_url and (force or not book_list.thumbnail):
        if _save_thumbnail(book_list, book_list.thumbnail_url):
            update_fields.append("thumbnail")

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
