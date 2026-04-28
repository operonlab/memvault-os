#!/usr/bin/env python3
"""Batch translate KG data to Traditional Chinese via DeepSeek.

Translates:
  1. Triples (subject predicate object) → display_zh
  2. Communities (missing description_zh) → description_zh

Usage:
    cd core && uv run python3 src/modules/memvault/scripts/translate_kg_zh.py
    cd core && uv run python3 src/modules/memvault/scripts/translate_kg_zh.py --triples-only
    cd core && uv run python3 src/modules/memvault/scripts/translate_kg_zh.py --communities-only
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_here = Path(__file__).resolve()
_core_root = _here.parents[4]

for _env_path in [_core_root / ".env", _core_root.parent / ".env"]:
    if _env_path.exists():
        from dotenv import load_dotenv

        load_dotenv(_env_path)
        break

CORE_API = os.environ.get("CORE_API_URL", "http://localhost:10000")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BATCH_SIZE = 20  # triples per LLM call
COMMUNITY_BATCH_SIZE = 10


def llm_call(prompt: str) -> str:
    """Call DeepSeek and return response text."""
    payload = json.dumps(
        {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2000,
            "temperature": 0.1,
        }
    ).encode()
    req = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()


def api_get(path: str) -> list | dict:
    url = f"{CORE_API}/api/memvault{path}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def api_post(path: str, body: dict) -> dict:
    url = f"{CORE_API}/api/memvault{path}"
    payload = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": e.code}


def translate_triples():
    """Translate all triples missing display_zh."""
    from sqlalchemy import create_engine, text

    _raw = os.environ.get("CORE_DB_URL", "postgresql://joneshong:REDACTED@localhost/workshop")
    db_url = (
        _raw.replace("postgresql://", "postgresql+psycopg://", 1)
        if "+" not in _raw.split("://")[0]
        else _raw
    )

    engine = create_engine(db_url)

    # Fetch triples missing display_zh
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, subject, predicate, object"
                " FROM memvault.triples"
                " WHERE display_zh IS NULL AND invalid_at IS NULL"
                " ORDER BY created_at"
            )
        ).fetchall()

    total = len(rows)
    print(f"\n[Triples] {total} triples need translation")
    if not total:
        return

    translated = 0
    for start in range(0, total, BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        # Build prompt
        lines = []
        for i, r in enumerate(batch):
            lines.append(f"{i}|{r.subject}|{r.predicate}|{r.object}")

        prompt = (
            "將以下知識圖譜三元組翻譯成繁體中文白話文句子。\n"
            "輸入格式：序號|主詞|關係|受詞\n"
            "輸出格式：每行一句，序號|繁體中文句子\n"
            "規則：\n"
            "- 用自然的繁體中文表達，不要直譯\n"
            "- 專有名詞（工具名、框架名）保留原文\n"
            "- 每句 15-40 字，簡潔有力\n\n" + "\n".join(lines)
        )

        try:
            result = llm_call(prompt)
            # Parse response
            for line in result.strip().split("\n"):
                line = line.strip()
                if not line or "|" not in line:
                    continue
                parts = line.split("|", 1)
                try:
                    idx = int(parts[0].strip())
                except ValueError:
                    continue
                zh_text = parts[1].strip()
                if idx < len(batch) and zh_text:
                    triple_id = batch[idx].id
                    with engine.connect() as conn:
                        conn.execute(
                            text("UPDATE memvault.triples SET display_zh = :zh WHERE id = :id"),
                            {"zh": zh_text, "id": triple_id},
                        )
                        conn.commit()
                    translated += 1

            end = min(start + BATCH_SIZE, total)
            print(f"  [{end}/{total}] translated: {translated}")
            time.sleep(0.3)  # rate limit
        except Exception as e:
            print(f"  [error] batch {start}: {e}", file=sys.stderr)
            time.sleep(1)

    engine.dispose()
    print(f"[Triples] Done: {translated}/{total} translated")


def translate_communities():
    """Translate communities missing description_zh."""
    from sqlalchemy import create_engine, text

    _raw = os.environ.get("CORE_DB_URL", "postgresql://joneshong:REDACTED@localhost/workshop")
    db_url = (
        _raw.replace("postgresql://", "postgresql+psycopg://", 1)
        if "+" not in _raw.split("://")[0]
        else _raw
    )

    engine = create_engine(db_url)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, name, summary, top_entities::text, top_predicates::text"
                " FROM memvault.communities"
                " WHERE description_zh IS NULL"
                " ORDER BY size DESC"
            )
        ).fetchall()

    total = len(rows)
    print(f"\n[Communities] {total} communities need description_zh")
    if not total:
        return

    translated = 0
    for start in range(0, total, COMMUNITY_BATCH_SIZE):
        batch = rows[start : start + COMMUNITY_BATCH_SIZE]
        lines = []
        for i, r in enumerate(batch):
            entities = r.top_entities or "[]"
            lines.append(f"{i}|{r.name}|{r.summary or ''}|{entities}")

        prompt = (
            "將以下知識社群資料翻譯成繁體中文白話文描述。\n"
            "輸入格式：序號|社群名|摘要|實體列表\n"
            "輸出格式：每行一段，序號|繁體中文描述（50-120字）\n"
            "規則：\n"
            "- 用自然的繁體中文說明這個社群在討論什麼主題\n"
            "- 專有名詞保留原文\n"
            "- 涵蓋主要實體和關係模式\n\n" + "\n".join(lines)
        )

        try:
            result = llm_call(prompt)
            for line in result.strip().split("\n"):
                line = line.strip()
                if not line or "|" not in line:
                    continue
                parts = line.split("|", 1)
                try:
                    idx = int(parts[0].strip())
                except ValueError:
                    continue
                zh_text = parts[1].strip()
                if idx < len(batch) and zh_text:
                    comm_id = batch[idx].id
                    with engine.connect() as conn:
                        q = "UPDATE memvault.communities"
                        q += " SET description_zh = :zh WHERE id = :id"
                        conn.execute(text(q), {"zh": zh_text, "id": comm_id})
                        conn.commit()
                    translated += 1

            end = min(start + COMMUNITY_BATCH_SIZE, total)
            print(f"  [{end}/{total}] translated: {translated}")
            time.sleep(0.3)
        except Exception as e:
            print(f"  [error] batch {start}: {e}", file=sys.stderr)
            time.sleep(1)

    engine.dispose()
    print(f"[Communities] Done: {translated}/{total} translated")


def main():
    parser = argparse.ArgumentParser(description="Batch translate KG to Traditional Chinese")
    parser.add_argument("--triples-only", action="store_true")
    parser.add_argument("--communities-only", action="store_true")
    args = parser.parse_args()

    if not DEEPSEEK_API_KEY:
        print("[error] DEEPSEEK_API_KEY required", file=sys.stderr)
        sys.exit(1)

    print("=== KG → 繁體中文 Translation ===")

    if not args.communities_only:
        translate_triples()

    if not args.triples_only:
        translate_communities()

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
