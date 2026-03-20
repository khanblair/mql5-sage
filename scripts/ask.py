#!/usr/bin/env python3
"""
scripts/ask.py — Answer MQL5 questions from the knowledge base
"""

import os, sys, json, re, hashlib, time
from pathlib import Path
from datetime import datetime, timezone
from groq import Groq

GROQ_MODEL     = "groq/compound"   # exact model string from Groq dashboard
KNOWLEDGE_FILE = Path("knowledge/knowledge_base.json")
JOURNAL_FILE   = Path("journal/JOURNAL.json")
JOURNAL_MD     = Path("JOURNAL.md")
MAX_CONTEXT    = 6

def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default

def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def sha(text): return hashlib.sha256(text.encode()).hexdigest()[:16]

def score_chunk(chunk: dict, query_words: list, query_lower: str) -> float:
    text_lower = chunk["text"].lower()
    section = chunk.get("section", "").lower()
    score = 0.0

    # Exact phrase match
    if query_lower in text_lower:
        score += 5.0

    # Trading operations sections (core trading functions)
    trading_ops_sections = ["trading", "series", "account", "common", "marketinformation"]
    # Technical sections (indicators, objects, etc.) - lower priority for trading queries
    technical_sections = ["indicators", "objects", "constants", "convert"]

    is_trading_ops = any(ts in section for ts in trading_ops_sections)
    is_technical = any(ts in section for ts in technical_sections)

    # Strong boost for trading operations sections
    if is_trading_ops:
        score += 8.0

    # Slight boost for technical sections (but less than trading ops)
    if is_technical:
        score += 2.0

    if is_trading_ops or (is_technical and any(w in query_lower for w in ["indicator", "handle", "buffer", "oncalculate"])):
        trading_keywords = ["trade", "order", "position", "pip", "pips", "lot", "lotsize", "sl", "tp", "price", "symbol", "market",
                           "buy", "sell", "stop", "profit", "loss", "equity", "margin", "balance", "broker",
                           "tick", "volume", "spread", "bid", "ask", "currency", "forex", "ea", "expert", "advisor",
                           "mql5", "mt5", "metatrader", "expertadvisor", "script", "library"]
        if any(w in query_lower for w in trading_keywords):
            score += 8.0

    # Additional domain-specific boost: trading function names in text
    trading_funcs = ["ordersend", "ordermodify", "orderclose", "orderdelete", "positionget", "positionopen",
                     "symbolinfo", "symbolinfodouble", "symbolinfoint", "symbolinfostring",
                     "copyrates", "copyclose", "copyhigh", "copylow", "copytick", "copyticks",
                     "volume", "normalizedlot", "calculate", "checkmoney"]
    if is_trading_ops and any(tf in text_lower for tf in trading_funcs):
        score += 5.0

    for word in query_words:
        if len(word) < 3: continue
        # Word in text
        if word in text_lower: score += 0.8
        # Word in title (important!)
        if word in chunk.get("page_title", "").lower(): score += 2.0
        # Word in section title
        if word in chunk.get("section_title", "").lower(): score += 1.5
        # Keyword matching
        for kw in chunk.get("keywords", []):
            if word == kw: score += 1.0
            elif word in kw or kw in word: score += 0.5

    # Boost code examples for "how to" questions
    if "[code_example]" in text_lower and any(w in query_lower for w in ["how", "example", "use", "syntax", "code", "write", "create", "implement"]):
        score += 2.0

    # Boost chunks with function names (highly relevant)
    if any(w in text_lower for w in ["ordersend", "symbolinfodouble", "positionget", "copyrates", "copyclose", "ichandle", "indicator"]):
        score += 2.0

    return score

def search(kb: dict, question: str, top_k=MAX_CONTEXT) -> list[dict]:
    query_lower = question.lower()
    query_words = re.findall(r'\b\w{3,}\b', query_lower)

    # First pass: keyword-based scoring
    scored = [(score_chunk(c, query_words, query_lower), c) for c in kb.get("chunks", [])]
    scored = [(s, c) for s, c in scored if s > 0]
    scored.sort(key=lambda x: -x[0])

    # Get top candidates
    candidates = [c for _, c in scored[:50]]  # Top 50 candidates

    if not candidates:
        return []

    # Second pass: Use Groq to pick the best chunks semantically
    # This ensures we get the most relevant chunks even if keywords don't match perfectly
    chunk_list = "\n\n".join([
        f"[{i}] Section: {c.get('section_title','')} | Page: {c.get('page_title','')}\n{c['text'][:500]}"
        for i, c in enumerate(candidates)
    ])

    # Determine likely domain from question keywords
    question_lower = question.lower()
    is_trading_query = any(w in question_lower for w in [
        "trade", "order", "position", "pip", "lot", "sl", "tp", "price", "symbol", "market",
        "buy", "sell", "stop", "profit", "loss", "equity", "margin", "balance", "broker",
        "tick", "volume", "spread", "bid", "ask", "currency", "forex", "ea", "expert", "advisor",
        "mql5", "mt5", "metatrader", "indicator", "expertadvisor", "script", "library"
    ])

    domain_hint = ""
    if is_trading_query:
        domain_hint = "IMPORTANT: This appears to be a TRADING question. Prioritize chunks from trading operations sections (trading, series, account, common, marketinformation). EXCLUDE generic indicator pages."

    selection_prompt = f"""Given the user question: "{question}"
{domain_hint}

Select the {top_k} most relevant chunks from this knowledge base that can answer the question. Consider:
- Section relevance: chunks from trading-related sections are preferred for trading questions
- Function names, parameters, return values
- Code examples
- Related concepts
- Prioritize chunks with relevant code examples for "how to" questions

Return ONLY a JSON array of the chunk indices (e.g., [0, 3, 7]) - no other text:

Available chunks:
{chunk_list}
"""

    try:
        client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
        resp = client.chat.completions.create(
            model="groq/compound",
            messages=[{"role": "user", "content": selection_prompt}],
            max_tokens=200,
            temperature=0.1
        )
        result_text = resp.choices[0].message.content or ""

        # Parse the JSON response
        import json
        try:
            selected_indices = json.loads(result_text.strip())
            if isinstance(selected_indices, list):
                return [candidates[i] for i in selected_indices if i < len(candidates)]
        except:
            pass
    except Exception as e:
        print(f"  ⚠ Semantic selection failed: {e}, using keyword results")

    # If trading query, filter to trading operations sections first (exclude indicators which is too broad)
    if is_trading_query:
        trading_ops_sections = ["trading", "series", "account", "common", "marketinformation"]
        trading_results = [(s, c) for s, c in scored if c.get("section", "").lower() in trading_ops_sections]
        if trading_results:
            return [c for _, c in trading_results[:top_k]]

    # Fallback to keyword-based results
    return [c for _, c in scored[:top_k]]

def write_journal_md(journal: list):
    emoji_map = {"LEARNING":"📚","QUERY":"🔍","EVOLUTION":"🧬","CRAWL":"🕷️","SESSION":"💬","MILESTONE":"🏆","FAILURE":"⚠️"}
    lines = ["# 📓 mql5-sage Journal\n\n", "> Append-only record of every session.\n\n---\n\n"]
    for e in reversed(journal):
        ts = e.get("timestamp","")[:16].replace("T"," ")
        emoji = emoji_map.get(e.get("entry_type",""), "•")
        lines += [f"## {emoji} {e.get('entry_type','')} — {ts} UTC\n\n",
                  f"**{e.get('title','')}**\n\n", f"{e.get('body','')}\n\n"]
        if e.get("tags"):
            lines.append(f"*Tags: {' '.join(f'`{t}`' for t in e['tags'])}*\n\n")
        lines.append("---\n\n")
    JOURNAL_MD.write_text("".join(lines))

def update_status_json(kb: dict, journal: list):
    """Generate status.json for GitHub Pages dashboard."""
    Path("docs").mkdir(exist_ok=True)

    page_count = len(kb.get("pages", {}))
    chunk_count = len(kb.get("chunks", []))
    queries = kb.get("total_queries", 0)

    # Calculate actual coverage
    pages_by_section: dict[str, int] = {}
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
        coverage_pct = 100  # Documentation complete!
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
        "journal": journal[-20:],  # Last 20 entries for dashboard
    }

    # Write to docs/ for GitHub Pages
    Path("docs/status.json").write_text(json.dumps(status, indent=2, ensure_ascii=False))

def main():
    api_key  = os.environ.get("GROQ_API_KEY","")
    question = os.environ.get("QUESTION","").strip()
    show_src = os.environ.get("SHOW_SOURCES","true").lower() == "true"

    if not api_key: print("✗ GROQ_API_KEY not set."); sys.exit(1)
    if not question: print("✗ No question."); sys.exit(1)

    kb      = load_json(KNOWLEDGE_FILE, {"pages":{}, "chunks":[], "total_queries":0})
    journal = load_json(JOURNAL_FILE, [])

    print(f"\n{'═'*60}")
    print(f"🔍 Question: {question}")
    print(f"{'═'*60}\n")

    if not kb.get("chunks"):
        print("⚠ Knowledge base is empty. Run the 🕷️ Crawl workflow first.")
        sys.exit(0)

    print(f"→ Searching {len(kb['chunks'])} knowledge chunks...")
    results = search(kb, question)

    if not results:
        print("⚠ No relevant chunks found.")
        sys.exit(0)

    print(f"✓ {len(results)} relevant chunks found\n")

    # Build context with source labels
    context_parts = []
    sources = []
    seen_pages = set()
    for i, chunk in enumerate(results):
        page_title = chunk.get("page_title", "MQL5 Docs")
        url = chunk.get("source_url", "")
        section = chunk.get("section_title", "")
        label = f"Source {i+1}: {page_title} [{section}]"
        context_parts.append(f"[{label}]\n{chunk['text']}")
        if url not in seen_pages:
            sources.append({"title": page_title, "url": url, "section": section})
            seen_pages.add(url)

    context = "\n\n---\n\n".join(context_parts)

    system = """You are mql5-sage, an expert on MQL5 algorithmic trading language.
You answer questions using ONLY the provided MQL5 documentation excerpts.
Rules:
- Always cite which source your information comes from
- For code questions, show the function signature and parameters
- If a code example is in the excerpts, include it
- Be precise with MQL5 syntax — traders depend on accuracy
- If the excerpts don't fully answer, say what you know and what's unclear
- Never invent function names or parameters"""

    user = f"""Using these MQL5 documentation excerpts, answer the question.

EXCERPTS:
{context}

QUESTION: {question}

Provide a clear, accurate answer with source references. Include code examples if available in the excerpts."""

    client = Groq(api_key=api_key)
    print(f"→ Asking Groq {GROQ_MODEL}...\n")

    answer = ""
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                max_tokens=1800, temperature=0.15,
            )
            answer = resp.choices[0].message.content or "No answer generated."
            break
        except Exception as e:
            err = str(e).lower()
            if "rate" in err or "429" in err:
                wait = 60 * (attempt + 1)
                print(f"  ⚠ Rate limit. Waiting {wait}s (attempt {attempt+1}/3)...")
                time.sleep(wait)
            else:
                print(f"  ✗ Groq error: {e}")
                answer = f"Error contacting Groq: {e}"
                break

    print("─"*60)
    print("✅ ANSWER:\n")
    print(answer)
    print("\n" + "─"*60)

    if show_src:
        print("\n📎 Source pages used:")
        for s in sources:
            print(f"  • {s['title']} — {s['url']}")

    # Update stats and journal
    kb["total_queries"] = kb.get("total_queries", 0) + 1
    journal.append({
        "id": sha(f"{question}{datetime.now().isoformat()}"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "entry_type": "QUERY",
        "title": f"Q: {question[:100]}",
        "body": f"**Question:** {question}\n\n**Answer:**\n{answer}\n\n**Sources:** {', '.join(s['title'] for s in sources)}",
        "tags": [],
    })

    save_json(KNOWLEDGE_FILE, kb)
    save_json(JOURNAL_FILE, journal)
    write_journal_md(journal)
    update_status_json(kb, journal)
    print(f"\n✓ Q&A journaled")
    print(f"✓ GitHub Pages status.json updated")

if __name__ == "__main__":
    main()
