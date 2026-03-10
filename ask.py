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
    score = 0.0
    if query_lower in text_lower:
        score += 3.0
    for word in query_words:
        if len(word) < 3: continue
        if word in text_lower: score += 0.6
        if word in chunk.get("page_title", "").lower(): score += 1.0
        for kw in chunk.get("keywords", []):
            if word == kw: score += 0.7
            elif word in kw or kw in word: score += 0.3
    # Boost code examples for "how to" questions
    if "[code_example]" in text_lower and any(w in query_lower for w in ["how", "example", "use", "syntax", "code"]):
        score += 1.5
    return score

def search(kb: dict, question: str, top_k=MAX_CONTEXT) -> list[dict]:
    query_lower = question.lower()
    query_words = re.findall(r'\b\w{3,}\b', query_lower)
    scored = [(score_chunk(c, query_words, query_lower), c) for c in kb.get("chunks", [])]
    scored = [(s, c) for s, c in scored if s > 0]
    scored.sort(key=lambda x: -x[0])
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
    print(f"\n✓ Q&A journaled")

if __name__ == "__main__":
    main()
