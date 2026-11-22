import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_JSON = BASE_DIR / "data" / "cards.json"

DEFAULT_OFFICIAL_URL = "https://www.takaratomy.co.jp/products/horobeat-cardgame/cardlist/"

FETCH_DETAIL_PAGES = os.environ.get("FETCH_DETAIL_PAGES", "0").strip().lower() in ("1", "true", "yes")

EXCLUDE_URL_KEYWORDS = (
    "logo",
    "banner",
    "icon",
    "sprite",
    "common",
    "header",
    "footer",
    "blank",
    "spacer",
    "noimage",
    "no_image",
    "sns",
    "share",
    "arrow",
    "button",
    "btn",
)

COLOR_CANDIDATES = ("赤", "青", "緑", "黄", "紫", "黒", "白", "無")


def sanitize_official_url(raw_url: str | None) -> str:
    s = str(raw_url or "").strip()
    if not s:
        return DEFAULT_OFFICIAL_URL

    # Markdownリンク [label](url) 形式になっていた場合は括弧内のURLを優先抽出
    md_match = re.search(r"\]\((https?://[^\s\)]+)\)", s)
    if md_match:
        s = md_match.group(1).strip()
    else:
        # 余分な角括弧・丸括弧・クォーテーション・空白を除去
        s = s.strip("<>\"'`[]() \t\r\n")

    # 文字列中に https:// または http:// が含まれる場合、先頭からURL部分を切り出す
    url_match = re.search(r"https?://[^\s\]\)\"'>]+", s)
    if url_match:
        s = url_match.group(0).rstrip(".,;]")

    parsed = urlparse(s)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return DEFAULT_OFFICIAL_URL

    return urlunparse(parsed)


OFFICIAL_URL = sanitize_official_url(os.environ.get("HOROBEAT_OFFICIAL_URL"))


def load_existing_cards(path: Path) -> list[dict]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        cards = raw
    elif isinstance(raw, dict) and isinstance(raw.get("cards"), list):
        cards = raw["cards"]
    else:
        cards = []

    cleaned = [
        c for c in cards
        if not re.match(r"^Card\s+\d+$", str(c.get("name", "")).strip(), re.IGNORECASE)
    ]
    return cleaned


def save_cards(path: Path, cards: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cards, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def resolve_image_url(raw_url: str, base_url: str) -> str:
    if not raw_url:
        return ""
    u = str(raw_url).strip().strip("<>\"'`[]()")
    if not u or u.startswith("data:") or u.startswith("blob:") or u.startswith("javascript:"):
        return ""
    full = urljoin(base_url, u)
    lower = full.lower()
    if any(kw in lower for kw in EXCLUDE_URL_KEYWORDS):
        return ""
    return full


def normalize_token(val: str) -> str:
    s = str(val or "").strip()
    s = s.translate(str.maketrans(
        "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ－／",
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-/",
    ))
    return re.sub(r"\s+", "", s).upper()


def normalize_color(val: str) -> str:
    s = str(val or "").strip()
    if not s:
        return ""
    m = re.search(r"(?:色|いろ|文明)\s*[:：]?\s*([赤青緑黄紫黒白無])", s)
    if m:
        return m.group(1)
    for c in COLOR_CANDIDATES:
        if re.fullmatch(rf"\s*{c}\s*", s):
            return c
    return ""


def parse_labeled_fields(text: str) -> dict:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    info = {
        "color": "",
        "type": "",
        "cost": None,
        "power": "",
        "boost": "",
        "race": "",
        "keyword": "",
        "trigger": "",
        "rarity": "",
    }
    if not cleaned:
        return info

    m_color = re.search(r"(?:色|いろ)\s*[:：]?\s*([赤青緑黄紫黒白無])", cleaned)
    if m_color:
        info["color"] = normalize_color(m_color.group(1))

    m_type = re.search(r"カードタイプ\s*[:：]?\s*([^\s]+(?:\s+[^\s]+)?)", cleaned)
    if m_type:
        cand_type = m_type.group(1).strip()
        stop_labels = ("色", "いろ", "コスト", "パワー", "ブースト", "種族", "しゅぞく", "キーワード", "トリガー", "レアリティ")
        if cand_type not in stop_labels:
            info["type"] = cand_type

    m_cost = re.search(r"コスト\s*[:：]?\s*([0-9０-９]+)", cleaned)
    if m_cost:
        cost_digits = m_cost.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        if cost_digits.isdigit():
            info["cost"] = int(cost_digits)

    m_power = re.search(r"パワー\s*[:：]?\s*([0-9０-９]+|なし|-)", cleaned)
    if m_power:
        info["power"] = m_power.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))

    m_boost = re.search(r"ブースト\s*[:：]?\s*([^\s]+)", cleaned)
    if m_boost and m_boost.group(1) not in ("種族", "しゅぞく", "キーワード", "トリガー", "レアリティ"):
        info["boost"] = m_boost.group(1)

    m_race = re.search(r"(?:種族|しゅぞく)\s*[:：]?\s*(.+?)(?=\s+(?:キーワード|のうりょく|トリガー|レアリティ)\b|$)", cleaned)
    if m_race:
        info["race"] = m_race.group(1).strip()

    m_kw = re.search(r"(?:キーワード\s*能力|キーワード|のうりょく)\s*[:：]?\s*(.+?)(?=\s+(?:トリガー|レアリティ)\b|$)", cleaned)
    if m_kw:
        info["keyword"] = m_kw.group(1).strip()

    m_trg = re.search(r"トリガー\s*[:：]?\s*(.+?)(?=\s+レアリティ\b|$)", cleaned)
    if m_trg:
        info["trigger"] = m_trg.group(1).strip()

    m_rar = re.search(r"レアリティ\s*[:：]?\s*([A-Za-z0-9Ａ-Ｚａ-ｚ０-９]+)", cleaned)
    if m_rar:
        info["rarity"] = normalize_token(m_rar.group(1))

    return info


def extract_rows_from_frame(frame, page_url: str) -> list[dict]:
    raw_rows = frame.evaluate(
        """() => {
            const containerSelector = [
                '[class*="card"]',
                '[id*="card"]',
                '[class*="item"]',
                '[class*="list"] > li',
                'article',
                'li'
            ].join(',');

            const imgs = Array.from(document.querySelectorAll('img'));
            return imgs.map((img) => {
                const rect = img.getBoundingClientRect();
                const container = img.closest(containerSelector) || img.parentElement;
                const text = container ? (container.innerText || '') : '';
                const linkEl = img.closest('a[href]') || (container ? container.querySelector('a[href]') : null);
                const href = linkEl ? (linkEl.getAttribute('href') || '') : '';

                const srcset = img.getAttribute('srcset') || '';
                let srcsetLast = '';
                if (srcset.trim()) {
                    const parts = srcset.split(',').map(s => s.trim().split(/\s+/)[0]).filter(Boolean);
                    if (parts.length > 0) {
                        srcsetLast = parts[parts.length - 1];
                    }
                }

                const candidates = [
                    srcsetLast,
                    img.getAttribute('data-src') || '',
                    img.getAttribute('data-original') || '',
                    img.getAttribute('data-lazy-src') || '',
                    img.currentSrc || '',
                    img.getAttribute('src') || ''
                ].filter(Boolean);

                return {
                    src: candidates[0] || '',
                    alt: (img.getAttribute('alt') || '').trim(),
                    title: (img.getAttribute('title') || '').trim(),
                    text: text.replace(/\s+/g, ' ').trim().slice(0, 800),
                    href: href.trim(),
                    width: Math.max(rect.width || 0, img.naturalWidth || 0),
                    height: Math.max(rect.height || 0, img.naturalHeight || 0)
                };
            });
        }"""
    )

    results = []
    seen_srcs = set()
    for r in raw_rows:
        w = r.get("width", 0)
        h = r.get("height", 0)
        if w > 0 and h > 0 and w < 40 and h < 40:
            continue

        resolved = resolve_image_url(r.get("src", ""), page_url)
        if not resolved or resolved in seen_srcs:
            continue
        seen_srcs.add(resolved)

        detail_url = urljoin(page_url, r["href"]) if r.get("href") else ""
        parsed = parse_labeled_fields(r.get("text", ""))

        results.append(
            {
                "src": resolved,
                "alt": r.get("alt", ""),
                "title": r.get("title", ""),
                "text": r.get("text", "")[:400],
                "detailUrl": detail_url,
                **parsed,
            }
        )
    return results


def scroll_to_load_lazy_images(page) -> None:
    for step in range(1, 10):
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {step} / 9)")
        page.wait_for_timeout(350)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(400)


def build_page_url(base_url: str, page_num: int) -> str:
    clean_base = sanitize_official_url(base_url)
    parsed = urlparse(clean_base)
    qs = parse_qs(parsed.query)
    qs["page"] = [str(page_num)]
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def enrich_items_from_detail_pages(context, items: list[dict]) -> None:
    detail_urls = sorted({it["detailUrl"] for it in items if it.get("detailUrl") and not it.get("color")})
    if not detail_urls:
        return

    detail_page = context.new_page()
    fetched_map: dict[str, dict] = {}
    try:
        for url in detail_urls:
            clean_detail_url = sanitize_official_url(url)
            if not clean_detail_url:
                continue
            try:
                detail_page.goto(clean_detail_url, wait_until="domcontentloaded", timeout=30000)
                detail_page.wait_for_timeout(600)
                body_text = detail_page.evaluate("() => document.body ? (document.body.innerText || '') : ''")
                fetched_map[url] = parse_labeled_fields(body_text)
            except Exception:
                continue
    finally:
        detail_page.close()

    for it in items:
        d_info = fetched_map.get(it.get("detailUrl", ""))
        if not d_info:
            continue
        for k, v in d_info.items():
            if v is not None and v != "" and not it.get(k):
                it[k] = v


def scrape_official_items() -> list[dict]:
    items: list[dict] = []
    seen_urls: set[str] = set()

    print(f"Using OFFICIAL_URL: {OFFICIAL_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            for page_num in range(1, 25):
                target_url = build_page_url(OFFICIAL_URL, page_num)
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                page.wait_for_timeout(1200)

                scroll_to_load_lazy_images(page)
                frame_rows = extract_rows_from_frame(page, page.url)

                if not frame_rows and len(page.frames) > 1:
                    for frame in page.frames[1:]:
                        try:
                            frame_rows.extend(extract_rows_from_frame(frame, frame.url or page.url))
                        except Exception:
                            continue

                new_count = 0
                for row in frame_rows:
                    if row["src"] not in seen_urls:
                        seen_urls.add(row["src"])
                        items.append(row)
                        new_count += 1

                print(f"PAGE {page_num}: rows={len(frame_rows)}, new={new_count}")
                if new_count == 0:
                    break

            if FETCH_DETAIL_PAGES:
                enrich_items_from_detail_pages(context, items)
        finally:
            browser.close()

    colored_count = sum(1 for it in items if it.get("color"))
    print(f"SCRAPED_TOTAL_UNIQUE_IMAGES: {len(items)}, WITH_COLOR: {colored_count}")
    return items


def extract_card_id_from_blob(blob: str) -> str:
    m = re.search(r"(HB\d{2}(?:/PR)?-\d{3})", normalize_token(blob))
    return m.group(1) if m else ""


def extract_rarity_hint(blob: str) -> str:
    norm = normalize_token(blob)
    for r in ("SEC", "UR", "HR", "SR", "AR", "RR", "R", "U", "C", "PR", "PAR", "SP"):
        if re.search(rf"(?<![A-Z0-9]){r}(?![A-Z0-9])", norm):
            return r
    return ""


def make_unique_id(base_id: str, rarity: str, used_ids: set[str]) -> str:
    candidate = f"{base_id}_{rarity}" if rarity else base_id
    if candidate not in used_ids:
        used_ids.add(candidate)
        return candidate

    n = 2
    while True:
        alt_id = f"{candidate}__{n}"
        if alt_id not in used_ids:
            used_ids.add(alt_id)
            return alt_id
        n += 1


def apply_scraped_fields(card: dict, row: dict, blob: str) -> None:
    color = normalize_color(row.get("color", "")) or normalize_color(blob)
    if color and not str(card.get("color") or "").strip():
        card["color"] = color

    if row.get("type") and not str(card.get("type") or "").strip():
        card["type"] = row["type"]

    if row.get("cost") is not None and card.get("cost") in (None, "", 0):
        card["cost"] = row["cost"]

    if row.get("rarity") and not str(card.get("rarity") or "").strip():
        card["rarity"] = row["rarity"]

    for extra_key in ("power", "boost", "race", "keyword", "trigger"):
        if row.get(extra_key) and not str(card.get(extra_key) or "").strip():
            card[extra_key] = row[extra_key]


def merge_scraped_into_cards(existing_cards: list[dict], scraped_items: list[dict]) -> tuple[list[dict], int, int]:
    matched_imgs = 0
    added_cards = 0

    used_ids: set[str] = {str(c.get("id", "")).strip() for c in existing_cards if str(c.get("id", "")).strip()}
    known_srcs: dict[str, int] = {}
    for idx, c in enumerate(existing_cards):
        u = str(c.get("imageUrl", "")).strip()
        if u:
            known_srcs[u] = idx

    indexed_cards = list(enumerate(existing_cards))
    indexed_cards.sort(key=lambda pair: len(normalize_token(pair[1].get("name", ""))), reverse=True)

    for row in scraped_items:
        src = (row.get("src") or "").strip()
        if not src:
            continue

        filename_part = Path(urlparse(src).path).stem
        blob = f"{row.get('alt', '')} {row.get('title', '')} {row.get('text', '')} {filename_part}"
        blob_norm = normalize_token(blob)

        if src in known_srcs:
            existing_idx = known_srcs[src]
            apply_scraped_fields(existing_cards[existing_idx], row, blob)
            continue

        matched_idx = None

        for idx, card in indexed_cards:
            raw_cid = str(card.get("cardNumber") or card.get("id") or "")
            cid_norm = normalize_token(raw_cid)
            if len(cid_norm) >= 4 and not cid_norm.startswith("OFFICIAL-") and cid_norm in blob_norm:
                matched_idx = idx
                break

        if matched_idx is None:
            for idx, card in indexed_cards:
                cname_norm = normalize_token(card.get("name", ""))
                if len(cname_norm) >= 2 and cname_norm in blob_norm:
                    matched_idx = idx
                    break

        if matched_idx is not None:
            base_card = existing_cards[matched_idx]
            existing_src = str(base_card.get("imageUrl") or "").strip()

            if not existing_src:
                base_card["imageUrl"] = src
                apply_scraped_fields(base_card, row, blob)
                known_srcs[src] = matched_idx
                matched_imgs += 1
            else:
                base_no = str(
                    base_card.get("cardNumber")
                    or extract_card_id_from_blob(str(base_card.get("id", "")))
                    or base_card.get("id")
                    or "CARD"
                ).strip()
                rarity = row.get("rarity") or extract_rarity_hint(blob) or str(base_card.get("rarity") or "").strip()
                unique_id = make_unique_id(base_no, rarity, used_ids)

                variant_card = {
                    **base_card,
                    "id": unique_id,
                    "cardNumber": base_no,
                    "rarity": rarity,
                    "imageUrl": src,
                }
                apply_scraped_fields(variant_card, row, blob)
                existing_cards.append(variant_card)
                new_idx = len(existing_cards) - 1
                indexed_cards.append((new_idx, variant_card))
                known_srcs[src] = new_idx
                added_cards += 1
        else:
            raw_name = (row.get("alt") or row.get("title") or "").strip()
            if not raw_name or re.match(r"^Card\s+\d+$", raw_name, re.IGNORECASE):
                continue

            detected_no = extract_card_id_from_blob(blob)
            rarity = row.get("rarity") or extract_rarity_hint(blob)
            base_id = detected_no or f"OFFICIAL-{len(existing_cards) + added_cards + 1:03d}"
            unique_id = make_unique_id(base_id, rarity, used_ids)

            new_card = {
                "id": unique_id,
                "cardNumber": detected_no,
                "rarity": rarity,
                "color": normalize_color(row.get("color", "")) or normalize_color(blob),
                "name": raw_name[:80],
                "type": row.get("type") or "",
                "cost": row.get("cost") if row.get("cost") is not None else 0,
                "imageUrl": src,
                "text": row.get("text", "")[:200],
            }
            existing_cards.append(new_card)
            new_idx = len(existing_cards) - 1
            indexed_cards.append((new_idx, new_card))
            known_srcs[src] = new_idx
            added_cards += 1

    return existing_cards, matched_imgs, added_cards


def main() -> None:
    existing_cards = load_existing_cards(DATA_JSON)
    print(f"Loaded {len(existing_cards)} cards from {DATA_JSON}")

    scraped_items = scrape_official_items()
    updated_cards, matched_imgs, added_cards = merge_scraped_into_cards(existing_cards, scraped_items)

    save_cards(DATA_JSON, updated_cards)
    print(f"Done. total_cards={len(updated_cards)}, matched_imgs={matched_imgs}, added_cards={added_cards}")


if __name__ == "__main__":
    main()
