import json
import os
import re
from urllib.parse import urljoin
import pandas as pd
from playwright.sync_api import sync_playwright

OFFICIAL_URL = "https://www.takaratomy.co.jp/products/horobeat-cardgame/cardlist/"
DATA_JSON = "data/cards.json"
DATA_CSV = "data/cards.csv"


def load_existing_cards(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    return []


def scrape_official_items():
    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(OFFICIAL_URL, wait_until="networkidle", timeout=60000)

        search_btn = page.locator("button, input[type='submit'], a").filter(
            has_text=re.compile(r"^(検索|一覧を表示|すべて表示)$")
        ).first
        if search_btn.count() > 0 and search_btn.is_visible():
            try:
                search_btn.click()
                page.wait_for_timeout(1500)
            except Exception:
                pass

        page_num = 1
        seen_srcs = set()
        while True:
            page.wait_for_timeout(1200)
            extracted = page.evaluate('''() => {
                const imgs = Array.from(document.querySelectorAll('img'));
                const out = [];
                for (const img of imgs) {
                    const src = img.currentSrc || img.getAttribute('src') || '';
                    if (!src) continue;
                    if (/logo|banner|icon|sns|footer|header|top_btn|arrow/i.test(src)) continue;
                    const cardEl = img.closest('li, article, .card, .item, tr, div') || img.parentElement;
                    const rawText = (cardEl ? cardEl.innerText : '') || img.alt || '';
                    out.push({
                        src: src,
                        alt: img.alt || '',
                        text: rawText.trim()
                    });
                }
                return out;
            }''')

            for row in extracted:
                abs_src = urljoin(OFFICIAL_URL, row["src"])
                if abs_src not in seen_srcs:
                    seen_srcs.add(abs_src)
                    items.append({
                        "src": abs_src,
                        "alt": row["alt"],
                        "text": row["text"],
                        "page": page_num,
                    })

            next_btn = page.locator("a, button").filter(
                has_text=re.compile(r"^(次へ|次のページ|>|›)$")
            ).first
            if next_btn.count() > 0 and next_btn.is_visible() and next_btn.is_enabled():
                prev_snap = page.content()[:2500]
                next_btn.click()
                page.wait_for_timeout(1400)
                if page.content()[:2500] == prev_snap:
                    break
                page_num += 1
            else:
                break

        browser.close()
    return items


def merge_scraped_into_cards(existing_cards, scraped_items):
    by_id = {}
    by_name = {}
    for idx, c in enumerate(existing_cards):
        cid = str(c.get("id", "")).strip().upper()
        cname = str(c.get("name", "")).strip()
        if cid:
            by_id[cid] = idx
        if cname:
            by_name[cname] = idx

    matched_imgs = 0
    added_cards = 0

    for row in scraped_items:
        blob = f"{row.get('alt', '')} {row.get('text', '')}"
        src = row.get("src", "").strip()
        if not src:
            continue

        matched_idx = None
        for cid_upper, idx in by_id.items():
            if not cid_upper.startswith("OFFICIAL-") and cid_upper in blob.upper():
                matched_idx = idx
                break

        if matched_idx is None:
            for cname, idx in by_name.items():
                if cname and len(cname) >= 2 and cname in blob:
                    matched_idx = idx
                    break

        if matched_idx is not None:
            if not existing_cards[matched_idx].get("imageUrl"):
                existing_cards[matched_idx]["imageUrl"] = src
                matched_imgs += 1
        else:
            id_match = re.search(r"\b(HB\d{2}[-/][A-Z0-9-]+)\b", blob, re.IGNORECASE)
            lines = [ln.strip() for ln in str(row.get("text", "")).splitlines() if ln.strip()]
            candidate_name = lines[0] if lines else str(row.get("alt", "")).strip()
            if candidate_name and len(candidate_name) <= 40:
                new_id = id_match.group(1).upper() if id_match else f"AUTO-{len(existing_cards) + 1:03d}"
                key_id = new_id.upper()
                if key_id not in by_id and candidate_name not in by_name:
                    new_card = {
                        "id": new_id,
                        "name": candidate_name,
                        "type": "ワザ" if "ワザ" in blob else "ホロビト",
                        "color": "赤" if "赤" in blob else ("青" if "青" in blob else ("緑" if "緑" in blob else "無色")),
                        "cost": None,
                        "power": None,
                        "rarity": "",
                        "set": "HB-BP01",
                        "tribe": "",
                        "illustrator": "",
                        "imageUrl": src,
                        "text": blob[:500],
                    }
                    existing_cards.append(new_card)
                    by_id[key_id] = len(existing_cards) - 1
                    by_name[candidate_name] = len(existing_cards) - 1
                    added_cards += 1

    return existing_cards, matched_imgs, added_cards


def main():
    cards = load_existing_cards(DATA_JSON)
    try:
        scraped = scrape_official_items()
        cards, matched_imgs, added_cards = merge_scraped_into_cards(cards, scraped)
        print(f"Scraped {len(scraped)} items; updated {matched_imgs} images; added {added_cards} new cards.")
    except Exception as e:
        print(f"Warning during scrape: {e}")

    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)

    pd.DataFrame(cards).to_csv(DATA_CSV, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
