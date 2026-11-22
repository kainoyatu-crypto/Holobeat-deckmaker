import json
import re
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INBOX_JSON = BASE_DIR / "data" / "tournament_inbox.json"
OUTPUT_JSON = BASE_DIR / "data" / "tournament_results.json"


def load_items(path: Path) -> list[dict]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        return raw["items"]
    return []


def guess_rank(text: str) -> str:
    for rank in ("優勝", "準優勝", "3位", "ベスト4", "ベスト8", "入賞"):
        if rank in text:
            return rank
    return ""


def guess_date(posted_at: str, text: str) -> str:
    if posted_at and len(posted_at) >= 10:
        return posted_at[:10]
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m_jp = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m_jp:
        year = datetime.now(timezone.utc).year
        return f"{year:04d}-{int(m_jp.group(1)):02d}-{int(m_jp.group(2)):02d}"
    return ""


def normalize_records(items: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}

    for item in items:
        raw_text = str(item.get("rawText") or item.get("text") or "").strip()
        if not raw_text:
            continue

        url = str(item.get("url") or item.get("sourceUrl") or "").strip()
        key = url or re.sub(r"\s+", " ", raw_text)[:120]

        if key in merged:
            continue

        merged[key] = {
            "id": str(item.get("id") or key),
            "date": guess_date(str(item.get("postedAt") or ""), raw_text),
            "tournament": "ホロビート名人戦" if "名人戦" in raw_text else "ホロビート大会",
            "rank": guess_rank(raw_text),
            "sourceUrl": url,
            "rawText": raw_text,
            "verified": bool(item.get("verified", False)),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }

    return list(merged.values())


def main() -> None:
    inbox_items = load_items(INBOX_JSON)
    existing_results = load_items(OUTPUT_JSON)

    combined = existing_results + inbox_items
    normalized = normalize_records(combined)

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Updated {OUTPUT_JSON}: total={len(normalized)}")


if __name__ == "__main__":
    main()
