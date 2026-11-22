import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_JSON = BASE_DIR / "data" / "events.json"

TARGET_URLS = [
    {
        "url": "https://www.takaratomy.co.jp/products/horobeat-cardgame/news/hb-trialevent-otherstores-261015/",
        "category": "体験会",
        "base_title": "ホロビート お披露目体験会",
        "start_year": 2026,
    },
    {
        "url": "https://www.takaratomy.co.jp/products/horobeat-cardgame/event/hb-meijinsen-2611-2701/",
        "category": "名人戦",
        "base_title": "ホロビート名人戦",
        "start_year": 2026,
    },
]

AREA_COORDS = {
    "秋葉原": ("秋葉原", 35.6984, 139.7731),
    "新宿": ("新宿", 35.6896, 139.7006),
    "渋谷": ("渋谷", 35.6580, 139.7016),
    "池袋": ("池袋", 35.7295, 139.7109),
    "立川": ("立川", 35.6982, 139.4136),
    "八王子": ("八王子", 35.6558, 139.3389),
    "町田": ("町田", 35.5432, 139.4463),
    "横浜": ("横浜", 35.4658, 139.6223),
    "西尾": ("西尾", 34.8672, 137.0544),
    "名古屋": ("名古屋", 35.1709, 136.8816),
    "大阪": ("大阪", 34.7025, 135.4959),
    "日本橋": ("日本橋", 34.6615, 135.5056),
    "京都": ("京都", 34.9858, 135.7588),
    "神戸": ("神戸", 34.6901, 135.1955),
    "福岡": ("福岡", 33.5902, 130.4017),
    "仙台": ("仙台", 38.2601, 140.8824),
    "広島": ("広島", 34.3853, 132.4553),
    "熊本": ("熊本", 32.7898, 130.7417),
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
    "新潟県": (37.9026, 139.0236),
    "石川県": (36.5947, 136.6256),
    "長野県": (36.6513, 138.1810),
    "静岡県": (34.9769, 138.3831),
    "愛知県": (35.1802, 136.9066),
    "岐阜県": (35.3912, 136.7223),
    "三重県": (34.7303, 136.5086),
    "滋賀県": (35.0045, 135.8686),
    "京都府": (35.0210, 135.7556),
    "大阪府": (34.6863, 135.5200),
    "兵庫県": (34.6913, 135.1830),
    "奈良県": (34.6851, 135.8050),
    "岡山県": (34.6618, 133.9344),
    "広島県": (34.3966, 132.4596),
    "香川県": (34.3401, 134.0434),
    "愛媛県": (33.8392, 132.7657),
    "福岡県": (33.6064, 130.4183),
    "熊本県": (32.7898, 130.7417),
    "大分県": (33.2382, 131.6126),
    "宮崎県": (31.9111, 131.4239),
    "鹿児島県": (31.5602, 130.5581),
}


def fetch_html(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        },
    )
    with urlopen(req, timeout=30) as resp:
        raw = resp.read()
        encoding = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(encoding, errors="replace")


def fetch_html_playwright_fallback(url: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1200)
        html = page.content()
        browser.close()
    return html


def parse_date_iso(text: str, start_year: int = 2026) -> str:
    m_ymd = re.search(r"(\d{4})\s*[/-年]\s*(\d{1,2})\s*[/-月]\s*(\d{1,2})\s*日?", text)
    if m_ymd:
        return f"{int(m_ymd.group(1)):04d}-{int(m_ymd.group(2)):02d}-{int(m_ymd.group(3)):02d}"

    m_md = re.search(r"(?<!\d)(\d{1,2})\s*(?:/|月)\s*(\d{1,2})\s*日?(?!\d)", text)
    if m_md:
        month = int(m_md.group(1))
        day = int(m_md.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            year = start_year + 1 if (start_year == 2026 and month == 1) else start_year
            return f"{year:04d}-{month:02d}-{day:02d}"

    return ""


def parse_time_hhmm(text: str) -> str:
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if not m:
        return ""
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"


def resolve_location(text: str) -> tuple[str, str, float, float]:
    area = ""
    lat, lng = 35.6982, 139.4136
    pref = ""

    for p_name, (p_lat, p_lng) in PREF_COORDS.items():
        if p_name in text:
            pref = p_name
            area = p_name.replace("都", "").replace("府", "").replace("県", "")
            lat, lng = p_lat, p_lng
            break

    for key, (a_label, a_lat, a_lng) in AREA_COORDS.items():
        if key in text:
            area = a_label
            lat, lng = a_lat, a_lng
            break

    return area or "未指定", pref, lat, lng


def extract_events_from_html(html: str, cfg: dict) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    seen_ids: set[str] = set()

    # 1. テーブル行（tr）から抽出
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"]) if c.get_text(" ", strip=True)]
        if len(cells) < 2:
            continue

        joined = " ".join(cells)
        date_iso = parse_date_iso(joined, cfg["start_year"])
        if not date_iso:
            continue

        time_hhmm = ""
        capacity = ""
        venue_candidates: list[str] = []

        for cell in cells:
            if not time_hhmm:
                t = parse_time_hhmm(cell)
                if t and len(cell) <= 12:
                    time_hhmm = t
                    continue
            if not capacity and re.fullmatch(r"\d{1,3}\s*名?", cell):
                capacity = cell
                continue
            if not parse_date_iso(cell, cfg["start_year"]):
                venue_candidates.append(cell)

        venue = venue_candidates[0] if venue_candidates else "開催店舗"
        extra = " / ".join(venue_candidates[1:]) if len(venue_candidates) > 1 else ""
        area, pref, lat, lng = resolve_location(f"{venue} {extra}")

        raw_key = f"{cfg['url']}|{date_iso}|{time_hhmm}|{venue}|{extra}"
        ev_id = "hb-" + hashlib.md5(raw_key.encode("utf-8")).hexdigest()[:12]
        if ev_id in seen_ids:
            continue
        seen_ids.add(ev_id)

        notes_parts = []
        if pref:
            notes_parts.append(pref)
        if capacity:
            notes_parts.append(f"定員: {capacity}")
        if extra:
            notes_parts.append(extra)

        q = quote_plus(f"ホロビート {venue} {date_iso}".strip())
        results.append({
            "id": ev_id,
            "title": f"{cfg['base_title']}（{venue}）",
            "category": cfg["category"],
            "date": date_iso,
            "time": time_hhmm,
            "area": area,
            "venue": venue,
            "lat": lat,
            "lng": lng,
            "status": "予定",
            "sourceUrl": cfg["url"],
            "xSearchUrl": f"https://x.com/search?q={q}&src=typed_query&f=live",
            "notes": " / ".join(notes_parts),
        })

    # 2. テーブル外に日付＋店舗の行がある場合も補完抽出
    if not results:
        for el in soup.find_all(["li", "p", "div"]):
            if el.name == "div" and el.find(["div", "table", "ul"]):
                continue
            line = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
            if len(line) < 8 or len(line) > 220:
                continue
            date_iso = parse_date_iso(line, cfg["start_year"])
            if not date_iso:
                continue
            time_hhmm = parse_time_hhmm(line)
            area, pref, lat, lng = resolve_location(line)
            raw_key = f"{cfg['url']}|{date_iso}|{time_hhmm}|{line}"
            ev_id = "hb-" + hashlib.md5(raw_key.encode("utf-8")).hexdigest()[:12]
            if ev_id in seen_ids:
                continue
            seen_ids.add(ev_id)
            q = quote_plus(f"ホロビート {area} {date_iso}".strip())
            results.append({
                "id": ev_id,
                "title": f"{cfg['base_title']}（{area}）",
                "category": cfg["category"],
                "date": date_iso,
                "time": time_hhmm,
                "area": area,
                "venue": line,
                "lat": lat,
                "lng": lng,
                "status": "予定",
                "sourceUrl": cfg["url"],
                "xSearchUrl": f"https://x.com/search?q={q}&src=typed_query&f=live",
                "notes": pref,
            })

    return results


def main() -> None:
    all_events: list[dict] = []

    for cfg in TARGET_URLS:
        print(f"取得中: {cfg['url']}")
        try:
            html = fetch_html(cfg["url"])
            found = extract_events_from_html(html, cfg)
            if not found:
                try:
                    html = fetch_html_playwright_fallback(cfg["url"])
                    found = extract_events_from_html(html, cfg)
                except Exception as pw_err:
                    print(f"  Playwrightフォールバック失敗: {pw_err}")
            print(f"  -> {len(found)} 件の日程データを抽出")
            all_events.extend(found)
        except Exception as exc:
            print(f"  -> 取得エラー: {exc}")

    # IDで重複除外し、日付・時間順に並べる
    unique_by_id = {ev["id"]: ev for ev in all_events}
    sorted_events = sorted(
        unique_by_id.values(),
        key=lambda e: (e.get("date") or "9999-99-99", e.get("time") or "99:99", e.get("title") or ""),
    )

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps(sorted_events, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"保存完了: {OUTPUT_JSON}（合計 {len(sorted_events)} 件）")


if __name__ == "__main__":
    main()
