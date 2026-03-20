#!/usr/bin/env python3
"""
scripts/ask.py — Answer MQL5 questions from the knowledge base
"""

import os
import sys
import json
import re
import hashlib
import time
from pathlib import Path
from datetime import datetime, timezone
from groq import Groq

GROQ_MODEL = "groq/compound"
KNOWLEDGE_FILE = Path("knowledge/knowledge_base.json")
JOURNAL_FILE = Path("journal/JOURNAL.json")
JOURNAL_MD = Path("JOURNAL.md")
MAX_CONTEXT = 6


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def score_chunk(chunk, query_words, query_lower):
    text_lower = chunk.get("text", "").lower()
    section = chunk.get("section", "").lower()
    score = 0.0

    # Exact phrase match - highest priority
    if query_lower in text_lower:
        score += 10.0

    # Boost trading-related sections for trading questions
    trading_sections = ["trading", "series", "account", "indicators", "common", "marketinformation", "predefined"]
    if any(ts in section for ts in trading_sections):
        if any(w in query_lower for w in ["trade", "order", "position", "pip", "lot", "sl", "tp", "price", "symbol", "market", "buy", "sell", "calculate", "eurusd", "point"]):
            score += 8.0

    # Check query words
    for word in query_words:
        if len(word) < 3:
            continue

        # Word in text
        if word in text_lower:
            score += 1.0

        # Word in title - high priority
        title = chunk.get("page_title", "").lower()
        if word in title:
            score += 4.0

        # Word in section title
        section_title = chunk.get("section_title", "").lower()
        if word in section_title:
            score += 3.0

        # Exact keyword match
        for kw in chunk.get("keywords", []):
            if word == kw:
                score += 2.0
            elif word in kw or kw in word:
                score += 0.8

    # Boost code examples for "how to" questions
    if "code_example" in text_lower or "```mql5" in text_lower:
        if any(w in query_lower for w in ["how", "example", "use", "syntax", "code", "write", "create", "implement", "function"]):
            score += 3.0

    # Boost chunks with function names
    function_indicators = ["ordersend", "symbolinfodouble", "positionget", "copyrates", "copyclose", "ichandle", "indicator", "point()", "symbolinfo"]:
        for fi in function_indicators:
            if fi in text_lower:
                score += 2.0
                break

    return score


def search(kb, question, top_k=MAX_CONTEXT):
    query_lower = question.lower()
    query_words = re.findall(r'\b\w{3,}\b', query_lower)

    # Score all chunks
    chunks = kb.get("chunks", [])
    scored = []
    for chunk in chunks:
        s = score_chunk(chunk, query_words, query_lower)
        if s > 0:
            scored.append((s, chunk))

    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_k]]


def write_journal_md(journal):
    emoji_map = {"LEARNING": "📚", "QUERY": "🔍", "EVOLUTION": "🧬", "CRAWL": "🕷️", "SESSION": "💬", "MILESTONE": "🏆"}
    lines = ["# 📓 mql5-sage Journal\n\n", "> Append-only record of every session.\n\n---\n\n"]
    for e in reversed(journal):
        ts = e.get("timestamp", "")[:16].replace("T", " ")
        emoji = emoji_map.get(e.get("entry_type", ""), "•")
        lines += [f"## {emoji} {e.get('entry_type','')} — {ts} UTC\n\n",
                  f"**{e.get('title','')}**\n\n", f"{e.get('body','')}\n\n"]
        if e.get("tags"):
            lines.append(f"*Tags: {' '.join(f'`{t}`' for t in e['tags'])}*\n\n")
        lines.append("---\n\n")
    JOURNAL_MD.write_text("".join(lines), encoding="utf-8")


def update_status_json(kb, journal):
    """Generate status.json for GitHub Pages dashboard."""
    Path("docs").mkdir(exist_ok=True)

    page_count = len(kb.get("pages", {}))
    chunk_count = len(kb.get("chunks", []))
    queries = kb.get("total_queries", 0)

    # Calculate actual coverage
    pages_by_section = {}
    for p in kb.get("pages", {}).values():
        sec = p.get("section", "")
        pages_by_section[sec] = pages_by_section.get(sec, 0) + 1

    config = load_json(Path("config/mql5_sections.json"), {"sections": []})
    all_slugs = {s["slug"] for s in config.get("sections", [])}
    max_pages_per_section = 60

    section_progress = []
    for slug in all_slugs:
        pages = pages_by_section.get(slug, 0)
        progress = min(pages / max_pages_per_section * 100, 100)
        section_progress.append(progress)

    total_pages = sum(pages_by_section.values())
    if total_pages > 700:
        coverage_pct = 100
    else:
        coverage_pct = round(sum(section_progress) / len(section_progress)) if section_progress else 0

    status = {
        "page_count": page_count,
        "chunk_count": chunk_count,
        "query_count": queries,
        "journal_count": len(journal),
        "coverage_pct": coverage_pct,
        "total_sections": len(all_slugs),
        "sections_with_pages": len([s for s, c in pages_by_section.items() if c > 0]),
        "sections": pages_by_section,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "journal": journal[-20:],
    }

    Path("docs/status.json").write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    api_key = os.environ.get("GROQ_API_KEY", "")
    question = os.environ.get("QUESTION", "").strip()
    show_src = os.environ.get("SHOW_SOURCES", "true").lower() == "true"

    if not api_key:
        print("GROQ_API_KEY not set.")
        sys.exit(1)
    if not question:
        print("No question.")
        sys.exit(1)

    kb = load_json(KNOWLEDGE_FILE, {"pages": {}, "chunks": [], "total_queries": 0})
    journal = load_json(JOURNAL_FILE, [])

    print(f"\n{'='*60}")
    print(f"Question: {question}")
    print(f"{'='*60}\n")

    if not kb.get("chunks"):
        print("Knowledge base is empty. Run the Crawl workflow first.")
        sys.exit(0)

    print(f"Searching {len(kb['chunks'])} knowledge chunks...")
    results = search(kb, question)

    if not results:
        print("No relevant chunks found.")
        sys.exit(0)

    print(f"Found {len(results)} relevant chunks\n")

    # Build context with source labels
    context_parts = []
    sources = []
    seen_pages = set()
    for i, chunk in enumerate(results):
        page_title = chunk.get("page_title", "MQL5 Docs")
        url = chunk.get("source_url", "")
        section = chunk.get("section_title", "")

        if url not in seen_pages:
            seen_pages.add(url)
            sources.append({"title": page_title, "url": url})

        context_parts.append(f"[{i}] {section}: {page_title}\n{chunk.get('text', '')}")

    context = "\n\n---\n\n".join(context_parts)

    # Build prompt
    prompt = f"""You are an expert MQL5 programming assistant. Answer the user's question based ONLY on the provided knowledge base excerpts below.

Include practical, working code examples when relevant. Cite the source pages at the end.

QUESTION: {question}

KNOWLEDGE BASE EXCERPTS:
{context}

Provide a clear, accurate answer with code examples. If the knowledge base doesn't contain enough information, say so honestly." ""

    # Call Groq
    client = Groq(api_key=api_key)
    print("Asking Groq...")

    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": "You are an expert MQL5 programming assistant."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1500,
            temperature=0.3,
        )
        answer = resp.choices[0].message.content or ""
    except Exception as e:
        print(f"Groq error: {e}")
        sys.exit(1)

    print("ANSWER:")
    print(answer)

    if show_src and sources:
        print("\nSource pages:")
        for s in sources:
            print(f"  - {s['title']} — {s['url']}")

    # Update knowledge base
    kb["total_queries"] = kb.get("total_queries", 0) + 1
    save_json(KNOWLEDGE_FILE, kb)

    # Add to journal
    journal.append({
        "id": sha(datetime.now().isoformat()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "entry_type": "QUERY",
        "title": f"Q: {question[:100]}",
        "body": f"Question: {question}\n\nAnswer:\n{answer}\n\nSources: {', '.join(s['title'] for s in sources)}",
        "tags": [],
    })

    save_json(JOURNAL_FILE, journal)
    write_journal_md(journal)
    update_status_json(kb, journal)
    print("\nQ&A journaled")
    print("GitHub Pages status.json updated")


if __name__ == "__main__":
    main()
