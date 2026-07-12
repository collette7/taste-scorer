#!/usr/bin/env python3
"""Naver Local Search fallback for Korean venues Google Places can't find.

Small Seoul/Jeju cafes, bars, and shops often exist only on Naver Maps.
Used automatically by enrich.py when Google fails and the query looks
Korean; also callable directly: `python3 enrich_naver.py "내음성"`.

Keys (https://developers.naver.com — Application > Search API):
  env NAVER_CLIENT_ID / NAVER_CLIENT_SECRET
  or taste.config.json: {"naver": {"client_id": "...", "client_secret": "..."}}
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SEARCH_URL = "https://openapi.naver.com/v1/search/local.json"
HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
TAG_RE = re.compile(r"</?b>")

SEOUL_DISTRICTS = {
    "강남구": "Gangnam District", "강동구": "Gangdong District", "강북구": "Gangbuk District",
    "강서구": "Gangseo District", "관악구": "Gwanak District", "광진구": "Gwangjin District",
    "구로구": "Guro District", "금천구": "Geumcheon District", "노원구": "Nowon District",
    "도봉구": "Dobong District", "동대문구": "Dongdaemun District", "동작구": "Dongjak District",
    "마포구": "Mapo-gu", "서대문구": "Seodaemun District", "서초구": "Seocho District",
    "성동구": "Seongdong District", "성북구": "Seongbuk District", "송파구": "Songpa District",
    "양천구": "Yangcheon District", "영등포구": "Yeongdeungpo District", "용산구": "Yongsan District",
    "은평구": "Eunpyeong District", "종로구": "Jongno District", "중구": "Jung District",
    "중랑구": "Jungnang District",
}

CATEGORY_MAP = {
    "카페": "cafe", "커피": "cafe", "찻집": "cafe", "베이커리": "bakery", "디저트": "cafe",
    "술집": "bar", "바": "bar", "칵테일": "bar", "와인": "bar", "요리주점": "bar",
    "음식점": "restaurant", "한식": "restaurant", "일식": "restaurant", "중식": "restaurant",
    "양식": "restaurant", "레스토랑": "restaurant", "분식": "restaurant",
    "소품": "store", "서점": "book_store", "쇼핑": "store", "잡화": "store", "의류": "clothing_store",
    "숙박": "lodging", "호텔": "lodging", "펜션": "lodging", "게스트하우스": "lodging",
    "박물관": "museum", "미술관": "art_gallery", "갤러리": "art_gallery",
}


def get_naver_keys() -> tuple[str, str] | None:
    cid = os.environ.get("NAVER_CLIENT_ID")
    secret = os.environ.get("NAVER_CLIENT_SECRET")
    if cid and secret:
        return cid, secret
    from taste.paths import PROJECT_ROOT
    config_path = PROJECT_ROOT / "taste.config.json"
    if config_path.exists():
        try:
            naver = json.loads(config_path.read_text()).get("naver", {})
            if naver.get("client_id") and naver.get("client_secret"):
                return naver["client_id"], naver["client_secret"]
        except (json.JSONDecodeError, OSError):
            pass
    return None


def looks_korean(query: str) -> bool:
    if HANGUL_RE.search(query):
        return True
    lowered = query.lower()
    return any(k in lowered for k in ("seoul", "korea", "jeju", "busan", "-gu", "-dong", "gangnam", "hongdae", "seongsu", "itaewon"))


def map_category(category: str) -> list[str]:
    types = []
    for kr, google_type in CATEGORY_MAP.items():
        if kr in category and google_type not in types:
            types.append(google_type)
    return types


def enrich_naver(query: str) -> dict:
    keys = get_naver_keys()
    if not keys:
        return {"name": query, "resolved": False, "reason": "no Naver API keys (set NAVER_CLIENT_ID/SECRET or taste.config.json naver block)"}
    cid, secret = keys

    params = urllib.parse.urlencode({"query": query, "display": 5})
    req = urllib.request.Request(
        f"{SEARCH_URL}?{params}",
        headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except (OSError, json.JSONDecodeError) as e:
        return {"name": query, "resolved": False, "reason": f"Naver lookup failed: {e}"}

    items = data.get("items", [])
    if not items:
        return {"name": query, "resolved": False, "reason": "no Naver match"}

    item = items[0]
    name = TAG_RE.sub("", item.get("title", query))
    category = item.get("category", "")
    address = item.get("roadAddress") or item.get("address", "")
    # mapx/mapy are WGS84 * 1e7 integer strings
    try:
        lng = int(item["mapx"]) / 1e7
        lat = int(item["mapy"]) / 1e7
    except (KeyError, ValueError):
        lat = lng = None

    localities = []
    for part in address.split():
        if part in SEOUL_DISTRICTS:
            localities.append(SEOUL_DISTRICTS[part])
        elif part.endswith(("특별시", "광역시", "시")) and len(part) > 1:
            city = part.removesuffix("특별시").removesuffix("광역시")
            localities.append({"서울": "Seoul", "부산": "Busan", "제주": "Jeju", "인천": "Incheon", "대구": "Daegu", "대전": "Daejeon", "광주": "Gwangju", "울산": "Ulsan"}.get(city, city))
        elif part.endswith(("동", "읍", "면")) and len(part) > 1:
            localities.append(part)
    localities.append("South Korea")
    localities = list(dict.fromkeys(localities))

    types = map_category(category)
    bits = [
        "/".join(types) if types else category,
        address,
        f"Naver category: {category}" if category else "",
    ]
    return {
        "name": name,
        "resolved": True,
        "source": "naver",
        "types": types,
        "formatted_address": address,
        "google_rating": None,
        "url": item.get("link") or f"https://map.naver.com/p/search/{urllib.parse.quote(name)}",
        "lat": lat,
        "lng": lng,
        "photo_url": "",
        "localities": localities,
        "context": " | ".join(b for b in bits if b),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: enrich_naver.py <korean venue name>", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(enrich_naver(sys.argv[1]), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
