import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_JSON = BASE_DIR / "data" / "cards.json"
IMAGES_DIR = BASE_DIR / "data" / "images"

# 公式カード一覧ページのURL（必要に応じて実際のカード一覧URLへ変更）
OFFICIAL_URL = os.environ.get(
    "HOROBEAT_OFFICIAL_URL",
    "https://www.takaratomy.co.jp/products/horobeat/",
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
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("cards"), list):
        return raw["cards"]
    return []


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


def download_image_if_needed(page, img_url: str) -> str:
    """画像を data/images/ に保存し、ローカル相対パスまたは元URLを返す"""
    if not img_url:
        return ""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(img_url)
    ext = Path(parsed.path).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        ext = ".jpg"

    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", parsed.path.strip("/"))[-60:] or "card"
    filename = f"{safe_name}{ext}"
    dest = IMAGES_DIR / filename

    if not dest.exists():
        try:
            resp = page.request.get(img_url, timeout=20000)
            if resp.ok:
                body = resp.body()
                if len(body) > 512:
                    dest.write_bytes(body)
                    return f"data/images/{filename}"
        except Exception:
            pass

    return f"data/images/{filename}" if dest.exists() else img_url


def extract_rows_from_frame(frame, page_url: str) -> list[dict]:
    """フレーム（メインページまたはiframe）から画像候補を抽出"""
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
                    text: text.replace(/\\s+/g, ' ').trim().slice(0, 300),
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
        # 既知サイズがある場合は極小アイコンを除外
        if w > 0 and h > 0 and w < 36 and h < 36:
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
    """遅延読み込みを発火させるために段階スクロールして先頭へ戻す"""
    for step in range(1, 8):
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {step} / 7)")
        page.wait_for_timeout(450)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)


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
            page.goto(OFFICIAL_URL, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(1500)

            # 絞り込みや検索ボタンが初期表示に必要な場合があれば押す（存在しなければスキップ）
            for btn_selector in [
                'button:has-text("検索")',
                'button:has-text("すべて")',
                'button:has-text("一覧")',
                'a:has-text("カード一覧")',
            ]:
                loc = page.locator(btn_selector).first
                if loc.count() > 0:
                    try:
                        loc.click(timeout=3000)
                        page.wait_for_timeout(1500)
                        break
                    except Exception:
                        pass

            body_text = page.inner_text("body") if page.locator("body").count() > 0 else ""
            img_total = page.locator("img").count()
            print("URL:", page.url, "| TITLE:", page.title())
            print("IMG_TOTAL:", img_total, "| BODY_LEN:", len(body_text))

            page_num = 1
            while True:
                scroll_to_load_lazy_images(page)

                frame_rows = extract_rows_from_frame(page, page.url)

                # メインページに画像がない場合は iframe 内も確認
                if not frame_rows and len(page.frames) > 1:
                    for frame in page.frames[1:]:
                        try:
                            sub_rows = extract_rows_from_frame(frame, frame.url or page.url)
                            frame_rows.extend(sub_rows)
                        except Exception:
                            continue

                added_this_page = 0
                for row in frame_rows:
                    if row["src"] not in seen_urls:
                        seen_urls.add(row["src"])
                        items.append(row)
                        added_this_page += 1

                print(f"PAGE {page_num}: found {len(frame_rows)} rows, new {added_this_page}")

                # 次ページボタンがあれば遷移（最大20ページまで）
                if page_num >= 20:
                    break

                next_btn = page.locator(
                    'a:has-text("次へ"), button:has-text("次へ"), '
                    '[class*="next"]:not([class*="disabled"]) a, '
                    '[class*="next"]:not([class*="disabled"]) button'
                ).first

                if next_btn.count() > 0:
                    prev_snap = page.content()[:2500]
                    try:
                        next_btn.click(timeout=5000)
                        page.wait_for_timeout(1500)
                        if page.content()[:2500] == prev_snap:
                            break
                        page_num += 1
                    except Exception:
                        break
                else:
                    break

        finally:
            browser.close()

    print("SCRAPED_COUNT:", len(items))
    for it in items[:5]:
        print("ITEM:", it.get("src"), "|", it.get("alt"), "|", it.get("text", "")[:50])

    return items


def normalize_id(val: str) -> str:
    return re.sub(r"\s+", "", str(val or "")).upper()


def normalize_name(val: str) -> str:
    return re.sub(r"\s+", "", str(val or "")).lower()


def merge_scraped_into_cards(existing_cards: list[dict], scraped_items: list[dict]) -> tuple[list[dict], int, int]:
    by_id: dict[str, int] = {}
    by_name: dict[str, int] = {}

    for idx, c in enumerate(existing_cards):
        cid = normalize_id(c.get("id", ""))
        cname = normalize_name(c.get("name", ""))
        if cid:
            by_id[cid] = idx
        if cname:
            by_name[cname] = idx

    matched_imgs = 0
    added_cards = 0

    for row in scraped_items:
        src = (row.get("src") or "").strip()
        if not src:
            continue

        blob = f"{row.get('alt', '')} {row.get('title', '')} {row.get('text', '')}".strip()
        blob_upper = blob.upper()
        blob_lower = blob.lower()

        matched_idx = None

        # 1. 既存カードIDとの照合
        for cid_norm, idx in by_id.items():
            if len(cid_norm) >= 2 and cid_norm in blob_upper:
                matched_idx = idx
                break

        # 2. 既存カード名との照合
        if matched_idx is None:
            blob_no_space = re.sub(r"\s+", "", blob_lower)
            for cname_norm, idx in by_name.items():
                if len(cname_norm) >= 2 and cname_norm in blob_no_space:
                    matched_idx = idx
                    break

        if matched_idx is not None:
            existing_cards[matched_idx]["imageUrl"] = src
            matched_imgs += 1
        else:
            # 既存にない新規カード候補として追加
            new_id = f"OFFICIAL-{len(existing_cards) + added_cards + 1:03d}"
            fallback_name = row.get("alt") or row.get("title") or f"Card {len(existing_cards) + added_cards + 1}"
            new_card = {
                "id": new_id,
                "name": fallback_name[:80],
                "type": "",
                "cost": 0,
                "imageUrl": src,
                "text": row.get("text", "")[:200],
            }
            existing_cards.append(new_card)
            by_id[normalize_id(new_id)] = len(existing_cards) - 1
            by_name[normalize_name(fallback_name)] = len(existing_cards) - 1
            added_cards += 1

    return existing_cards, matched_imgs, added_cards


def main() -> None:
    existing_cards = load_existing_cards(DATA_JSON)
    print(f"Loaded {len(existing_cards)} existing cards from {DATA_JSON}")

    scraped_items = scrape_official_items()

    updated_cards, matched_imgs, added_cards = merge_scraped_into_cards(
        existing_cards, scraped_items
    )

    save_cards(DATA_JSON, updated_cards)
    print(
        f"Done. total={len(updated_cards)}, matched_imgs={matched_imgs}, added_cards={added_cards}"
    )


if __name__ == "__main__":
    main()
