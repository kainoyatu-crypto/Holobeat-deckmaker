import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_JSON = BASE_DIR / "data" / "events.json"

TARGET_SOURCES = [
    {
        "url": "https://www.takaratomy.co.jp/products/horobeat-cardgame/news/hb-trialevent-otherstores-261015/",
        "category": "体験会",
        "default_title": "ホロビート お披露目体験会",
        "default_year": 2026,
    },
    {
        "url": "https://www.takaratomy.co.jp/products/horobeat-cardgame/event/hb-meijinsen-2611-2701/",
        "category": "名人戦",
        "default_title": "ホロビート名人戦",
        "default_year": 2026,
    },
]

AREA_COORDS = {
    "立川": (35.6982, 139.4136),
    "八王子": (35.6558, 139.3389),
    "町田": (35.5432, 139.4463),
    "新宿": (35.6896, 139.7006),
    "秋葉原": (35.6984, 139.7731),
    "渋谷": (35.6580, 139.7016),
    "池袋": (35.7295, 139.7109),
    "横浜": (35.4658, 139.6223),
    "名古屋": (35.1709, 136.8816),
    "大阪": (34.7025, 135.4959),
    "日本橋": (34.6615, 135.5056),
    "京都": (34.9858, 135.7588),
    "神戸": (34.6901, 135.1955),
    "福岡": (33.5902, 130.4017),
    "仙台": (38.2601, 140.8824),
    "広島": (34.3853, 132.4553),
    "熊本": (32.7898, 130.7417),
    "静岡": (34.9756, 138.3828),
}

PREF_COORDS = {
    "北海道": (43.0642, 141.3469),
    "宮城県": (38.2682, 140.8694),
    "茨城県": (36.3418, 140.4468),
    "栃木県": (36.5657, 139.8836),
    "群馬県": (36.3911, 139.0608),
    "埼玉県": (35.8569, 139.6489),
    "千葉県": (35.6051, 140.1233),
    "東京都": (35.6895, 139.6917),
    "神奈川県": (35.4478, 139.6425),
    "長野県": (36.6513, 138.1810),
    "静岡県": (34.9769, 138.3831),
    "愛知県": (35.1802, 136.9066),
    "岐阜県": (35.3912, 136.7223),
    "滋賀県": (35.0045, 135.8686),
    "京都府": (35.0210, 135.7556),
    "大阪府": (34.6863, 135.5200),
    "兵庫県": (34.6913, 135.1830),
    "鳥取県": (35.5011, 134.2351),
    "岡山県": (34.6618, 133.9344),
    "広島県": (34.3966, 132.4596),
    "高知県": (33.5597, 133.5311),
    "福岡県": (33.6064, 130.4183),
    "熊本県": (32.7898, 130.7417),
    "宮崎県": (31.9111, 131.4239),
}


def fetch_html_with_playwright(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)
        html = page.content()
        browser.close()
    return html


def normalize_date_str(raw: str, default_year: int = 2026) -> str:
    s = raw.strip()
    m_full = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if m_full:
        return f"{int(m_full.group(1)):04d}-{int(m_full.group(2)):02d}-{int(m_full.group(3)):02d}"

    m_jp_full = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m_jp_full:
        return f"{int(m_jp_full.group(1)):04d}-{int(m_jp_full.group(2)):02d}-{int(m_jp_full.group(3)):02d}"

    m_short = re.search(r"(\d{1,2})/(\d{1,2})", s)
    if m_short:
        month = int(m_short.group(1))
        day = int(m_short.group(2))
        year = default_year + 1 if month == 1 and default_year == 2026 else default_year
        return f"{year:04d}-{month:02d}-{day:02d}"

    m_jp_short = re.search(r"(\d{1,2})月(\d{1,2})日", s)
    if m_jp_short:
        month = int(m_jp_short.group(1))
        day = int(m_jp_short.group(2))
        year = default_year + 1 if month == 1 and default_year == 2026 else default_year
        return f"{year:04d}-{month:02d}-{day:02d}"

    return ""


def normalize_time_str(raw: str) -> str:
    m = re.search(r"\b(\d{1,2}:\d{2})\b", raw.strip())
    if not m:
        return ""
    hh, mm = m.group(1).split(":")
    return f"{int(hh):02d}:{int(mm):02d}"


def infer_area_and_coords(venue: str, pref: str, address: str) -> tuple[str, float, float]:
    combined = f"{venue} {pref} {address}"
    for label, (lat, lng) in AREA_COORDS.items():
        if label in combined:
            return label, lat, lng

    for p_name, (lat, lng) in PREF_COORDS.items():
        if p_name in combined:
            area_label = p_name.replace("都", "").replace("府", "").replace("県", "")
            return area_label, lat, lng

    return "未指定", 35.6982, 139.4136


def parse_tables_from_html(html: str, source: dict) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    parsed: list[dict] = []
    page_title_el = soup.find("h1")
    page_heading = page_title_el.get_text(" ", strip=True) if page_title_el else source["default_title"]

    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
        for tr in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) < 2 or cells == headers:
                continue

            # 列構成の推定（開催日, 開始時間, 定員, 店舗名, 都道府県, 住所 など）
            date_str = ""
            time_str = ""
            capacity = ""
            venue = ""
            pref = ""
            address = ""

            for cell in cells:
                if not date_str:
                    d = normalize_date_str(cell, source["default_year"])
                    if d:
                        date_str = d
                        continue
                if not time_str:
                    t = normalize_time_str(cell)
                    if t and len(cell) <= 12:
                        time_str = t
                        continue
                if not capacity and re.fullmatch(r"\d{1,3}名?", cell.strip()):
                    capacity = cell.strip()
                    continue
                if not pref and re.search(r"(北海道|.+[都府県])$", cell.strip()):
                    pref = cell.strip()
                    continue

            remaining = [
                c for c in cells
                if c and c != capacity and c != pref
                and normalize_date_str(c, source["default_year"]) != date_str
                and normalize_time_str(c) != time_str
            ]
            if remaining:
                venue = remaining[0]
            if len(remaining) >= 2:
                address = " ".join(remaining[1:])

            if not date_str and not venue:
                continue

            area, lat, lng = infer_area_and_coords(venue, pref, address)
            title = f"{source['default_title']}（{venue or area}）"
            raw_key = f"{source['url']}|{date_str}|{time_str}|{venue}|{address}"
            ev_id = "hb-" + hashlib.md5(raw_key.encode("utf-8")).hexdigest()[:12]

            memo_parts = []
            if capacity:
                memo_parts.append(f"定員: {capacity}")
            if address:
                memo_parts.append(f"住所: {address}")
            if page_heading and page_heading != source["default_title"]:
                memo_parts.append(f"案内: {page_heading}")

            search_query = f"ホロビート {source['default_title']} {venue} {date_str}".strip()
            x_search_url = f"https://x.com/search?q={quote_plus(search_query)}&src=typed_query&f=live"

            parsed.append({
                "id": ev_id,
                "title": title,
                "category": source["category"],
                "date": date_str,
                "time": time_str,
                "area": area,
                "venue": venue or "対象店舗",
                "lat": lat,
                "lng": lng,
                "status": "予定",
                "sourceUrl": source["url"],
                "xSearchUrl": x_search_url,
                "notes": " / ".join(memo_parts),
            })

    return parsed


def load_existing_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def sync_events() -> list[dict]:
    existing = load_existing_events(OUTPUT_JSON)
    merged: dict[str, dict] = {}

    # 手動追加や既存レコードを基準にしつつ、取得結果で更新・追加
    for item in existing:
        if isinstance(item, dict) and item.get("id"):
            merged[str(item["id"])] = item

    for src in TARGET_SOURCES:
        print(f"Fetching: {src['url']}")
        try:
            html = fetch_html_with_playwright(src["url"])
            found = parse_tables_from_html(html, src)
            print(f"  -> Extracted {len(found)} items")
            for ev in found:
                merged[ev["id"]] = ev
        except Exception as exc:
            print(f"  -> Error fetching {src['url']}: {exc}")

    result = sorted(
        merged.values(),
        key=lambda e: (e.get("date") or "9999-99-99", e.get("time") or "99:99", e.get("title") or ""),
    )

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(result)} events to {OUTPUT_JSON}")
    return result


if __name__ == "__main__":
    sync_events()
