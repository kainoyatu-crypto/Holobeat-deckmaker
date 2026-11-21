import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse, parse_qs, urlunparse
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_JSON = BASE_DIR / "data" / "cards.json"

OFFICIAL_URL = os.environ.get(
    "HOROBEAT_OFFICIAL_URL",
    "https://www.takaratomy.co.jp/products/horobeat-cardgame/cardlist/?same_name=hide",
)

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

    # 過去のテスト同期で混入した仮レコード（OFFICIAL-xxx）を除去
    cleaned = [
        c for c in cards
        if not str(c.get("id", "")).strip().upper().startswith("OFFICIAL-")
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
    u = raw_url.strip()
    if not u or u.startswith("data:") or u.startswith("blob:") or u.startswith("javascript:"):
        return ""
    full = urljoin(base_url, u)
    lower = full.lower()
    if any(kw in lower for kw in EXCLUDE_URL_KEYWORDS):
        return ""
    return full


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

                const srcset = img.getAttribute('srcset') || '';
                let srcsetLast = '';
                if (srcset.trim()) {
                    const parts = srcset.split(',').map(s => s.trim().split(/\\s+/)[0]).filter(Boolean);
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
                    text: text.replace(/\\s+/g, ' ').trim().slice(0, 400),
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

        results.append(
            {
                "src": resolved,
                "alt": r.get("alt", ""),
                "title": r.get("title", ""),
                "text": r.get("text", ""),
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
    parsed = urlparse(base_url)
    qs = parse_qs(parsed.query)
    qs["page"] = [str(page_num)]
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def scrape_official_items() -> list[dict]:
    items: list[dict] = []
    seen_urls: set[str] = set()

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
        finally:
            browser.close()

    print(f"SCRAPED_TOTAL_UNIQUE_IMAGES: {len(items)}")
    return items


def normalize_token(val: str) -> str:
    # 全角英数字・記号のゆれや空白を吸収
    s = str(val or "").strip()
    s = s.translate(str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ－／",
                                  "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-/"))
    return re.sub(r"\s+", "", s).upper()


def merge_scraped_into_cards(existing_cards: list[dict], scraped_items: list[dict]) -> tuple[list[dict], int]:
    matched_imgs = 0

    # 長いカード名・型番から優先して誤爆を防ぐ
    indexed_cards = list(enumerate(existing_cards))
    indexed_cards.sort(key=lambda pair: len(normalize_token(pair[1].get("name", ""))), reverse=True)

    for row in scraped_items:
        src = (row.get("src") or "").strip()
        if not src:
            continue

        # 画像ファイル名も含めて照合対象にする（URLにHB01-010等が含まれる場合に対応）
        filename_part = Path(urlparse(src).path).stem
        blob = f"{row.get('alt', '')} {row.get('title', '')} {row.get('text', '')} {filename_part}"
        blob_norm = normalize_token(blob)

        matched_idx = None

        # 1. 型番（ID）一致（例: HB01-010, HB01/PR-028）
        for idx, card in indexed_cards:
            cid_norm = normalize_token(card.get("id", ""))
            if len(cid_norm) >= 4 and cid_norm in blob_norm:
                matched_idx = idx
                break

        # 2. カード名一致
        if matched_idx is None:
            for idx, card in indexed_cards:
                cname_norm = normalize_token(card.get("name", ""))
                if len(cname_norm) >= 2 and cname_norm in blob_norm:
                    matched_idx = idx
                    break

        if matched_idx is not None:
            existing_cards[matched_idx]["imageUrl"] = src
            matched_imgs += 1

    return existing_cards, matched_imgs


def main() -> None:
    existing_cards = load_existing_cards(DATA_JSON)
    print(f"Loaded {len(existing_cards)} official cards from {DATA_JSON}")

    scraped_items = scrape_official_items()
    updated_cards, matched_imgs = merge_scraped_into_cards(existing_cards, scraped_items)

    save_cards(DATA_JSON, updated_cards)
    print(f"Done. total_cards={len(updated_cards)}, matched_imgs={matched_imgs}")


if __name__ == "__main__":
    main()
