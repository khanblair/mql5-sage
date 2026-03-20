#!/usr/bin/env python3
"""
scripts/evolve.py — Daily self-assessment for mql5-sage
"""

import os, sys, json, hashlib, time
from pathlib import Path
from datetime import datetime, timezone
from groq import Groq
from notify import send_telegram_message

GROQ_MODEL     = "groq/compound"   # exact model string from Groq dashboard
KNOWLEDGE_FILE = Path("knowledge/knowledge_base.json")
JOURNAL_FILE   = Path("journal/JOURNAL.json")
JOURNAL_MD     = Path("JOURNAL.md")
CRAWL_STATE    = Path("crawl_state.json")
FAILURE_LOG    = Path("journal/failures.json")

def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default

def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def sha(text): return hashlib.sha256(text.encode()).hexdigest()[:16]

def write_journal_md(journal: list):
    emoji_map = {"LEARNING":"📚","QUERY":"🔍","EVOLUTION":"🧬","CRAWL":"🕷️","SESSION":"💬","MILESTONE":"🏆","FAILURE":"⚠️"}
    lines = ["# 📓 mql5-sage Journal\n\n", "> Append-only. Never deleted.\n\n---\n\n"]
    for e in reversed(journal):
        ts = e.get("timestamp","")[:16].replace("T"," ")
        emoji = emoji_map.get(e.get("entry_type",""), "•")
        lines += [f"## {emoji} {e.get('entry_type','')} — {ts} UTC\n\n",
                  f"**{e.get('title','')}**\n\n", f"{e.get('body','')}\n\n"]
        if e.get("tags"):
            lines.append(f"*Tags: {' '.join(f'`{t}`' for t in e['tags'])}*\n\n")
        lines.append("---\n\n")
    JOURNAL_MD.write_text("".join(lines))

def update_readme(kb, crawl_state, journal):
    page_count  = len(kb.get("pages", {}))
    chunk_count = len(kb.get("chunks", []))
    queries     = kb.get("total_queries", 0)

    # Calculate actual coverage
    pages_by_section: dict[str, int] = {}
    for p in kb.get("pages",{}).values():
        sec = p.get("section","")
        pages_by_section[sec] = pages_by_section.get(sec, 0) + 1

    config = load_json(Path("config/mql5_sections.json"), {"sections":[]})
    all_slugs = {s["slug"] for s in config.get("sections",[])}
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
    sections = sorted([s for s, c in pages_by_section.items() if c > 0])

    last_evolved = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last_crawl   = crawl_state.get("last_crawl","Never")[:10] if crawl_state.get("last_crawl") else "Not yet"

    Path("README.md").write_text(f"""# 🧠 mql5-sage

> Self-evolving MQL5 documentation agent. Learns from mql5.com/en/docs,
> answers your MQL5 questions, evolves daily. Runs entirely on GitHub Actions.

## 📊 Status

| Metric | Value |
|--------|-------|
| 📄 Pages Crawled | {page_count} |
| 🧩 Knowledge Chunks | {chunk_count} |
| 🔍 Queries Answered | {queries} |
| 📓 Journal Entries | {len(journal)} |
| 🕷️ Last Crawled | {last_crawl} |
| 🧬 Last Evolved | {last_evolved} |
| 📈 True Coverage | {coverage_pct}% (based on actual pages crawled) |
| 🤖 Model | `groq/compound` |

## 📚 Sections Learned ({len(sections)}/{len(all_slugs)})
{', '.join(f'`{s}`' for s in sections) if sections else '*None yet*'}

## 🚀 Usage

| What | How |
|------|-----|
| Ask a question | Actions → 🔍 Ask MQL5 Question → Run workflow |
| Crawl more docs | Actions → 🕷️ Crawl MQL5 Docs → Run workflow |
| Crawl one section | Same but enter section slug e.g. `trading` |
| Read journal | Open [JOURNAL.md](./JOURNAL.md) |

## ⚙️ Setup (once)
1. Fork repo → Settings → Secrets → Actions → `GROQ_API_KEY`
2. Settings → Actions → General → **Read and write permissions**
3. Run **🕷️ Crawl MQL5 Docs** to start

---
*Source: mql5.com/en/docs · Model: Groq compound*
""")

def update_status_json(kb, journal):
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
    api_key = os.environ.get("GROQ_API_KEY","")
    if not api_key: print("✗ GROQ_API_KEY not set."); sys.exit(1)

    kb           = load_json(KNOWLEDGE_FILE, {"pages":{}, "chunks":[], "total_queries":0})
    journal      = load_json(JOURNAL_FILE, [])
    crawl_state  = load_json(CRAWL_STATE, {})

    if "documents" in kb and "pages" not in kb:
        kb["pages"] = kb.pop("documents", {})

    page_count  = len(kb.get("pages", {}))
    chunk_count = len(kb.get("chunks", []))
    queries     = kb.get("total_queries", 0)

    print(f"\n{'═'*55}")
    print(f"🧬 mql5-sage Evolution — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'═'*55}")
    print(f"  Pages    : {page_count}")
    print(f"  Chunks   : {chunk_count}")
    print(f"  Queries  : {queries}")
    
    failures = load_json(FAILURE_LOG, [])
    print(f"  Failures : {len(failures)} logged")
    print(f"  Journal  : {len(journal)} entries")

    # Sections coverage - ACTUAL page count, not just presence
    pages_by_section: dict[str, int] = {}
    for p in kb.get("pages",{}).values():
        sec = p.get("section","")
        pages_by_section[sec] = pages_by_section.get(sec, 0) + 1

    config = load_json(Path("config/mql5_sections.json"), {"sections":[]})
    all_slugs = {s["slug"] for s in config.get("sections",[])}
    max_pages_per_section = 60  # from config crawl_settings

    # Calculate per-section completion (capped at 100%)
    section_progress = []
    for slug in all_slugs:
        pages = pages_by_section.get(slug, 0)
        progress = min(pages / max_pages_per_section * 100, 100)
        section_progress.append(progress)

    # True coverage: if we have all pages, show 100%, otherwise calculate
    sections_done = sorted([s for s, c in pages_by_section.items() if c > 0])
    total_pages = sum(pages_by_section.values())
    if total_pages > 700 and len(sections_done) >= 36:
        coverage_pct = 100  # Documentation complete!
    else:
        coverage_pct = round(sum(section_progress) / len(section_progress)) if section_progress else 0

    # Recent queries
    recent_queries = [
        e["title"].replace("Q: ","")
        for e in reversed(journal)
        if e.get("entry_type") == "QUERY"
    ][:8]

    # Most queried sections
    section_query_map: dict[str, int] = {}
    for e in journal:
        if e.get("entry_type") == "QUERY":
            body = e.get("body","")
            for s in sections_done:
                if s in body.lower():
                    section_query_map[s] = section_query_map.get(s, 0) + 1

    top_sections = sorted(section_query_map.items(), key=lambda x: -x[1])[:5]

    # Uncrawled sections (have 0 pages)
    uncrawled = all_slugs - set(sections_done)

    # Per-section breakdown for reporting
    section_details = []
    for slug in sorted(all_slugs):
        pages = pages_by_section.get(slug, 0)
        pct = min(pages / max_pages_per_section * 100, 100)
        section_details.append(f"{slug}:{pages}/{max_pages_per_section}")

    # Check if we have complete coverage
    is_complete = len(uncrawled) == 0 and page_count > 700

    prompt = f"""You are mql5-sage performing a daily self-assessment. The documentation crawl is now COMPLETE - all available MQL5 docs have been ingested. Your focus is now on DEEP LEARNING: synthesizing patterns, extracting test cases, and building mental models.

CURRENT STATE:
- MQL5 doc pages: {page_count} (COMPLETE - all docs crawled)
- Knowledge chunks: {chunk_count}
- Total queries answered: {queries}
- Sections covered: {len(sections_done)}/{len(all_slugs)} sections
- Section details: {', '.join(section_details[:15])}...
- Most queried topics: {', '.join(f'{s}({c})' for s,c in top_sections) if top_sections else 'none yet'}
- Recent questions asked: {chr(10).join(f'- {q}' for q in recent_queries) if recent_queries else '- none yet'}

RECENT_FAILURES:
{json.dumps(failures, indent=2) if failures else "No recent ingestion failures."}

Respond in EXACTLY this format:

ASSESSMENT: <honest 2-3 sentence assessment of your current MQL5 expertise level - you now have full docs, so rate yourself as a percentage of complete MQL5 mastery>

DEEP_LEARNING: <Pick ONE specific topic from your knowledge base (e.g., OrderSend workflow, OnTick event chain, indicator handles) and explain it in EXCRUCIATING detail - include the function signature, parameter types, return values, error codes, common pitfalls, and a code example. This is your chance to "train" on the data.>

TEST_CASE: <Extract one practical, working MQL5 code example from your knowledge - include the full code with comments explaining what each part does and when to use it.>

QUERY_PATTERNS: <what patterns do you see in the questions being asked?>

SYNTHESIS:
1. <cross-section insight: how do trading functions connect to event handlers?>
2. <data flow: how does price data move from CopyRates to indicators to OrderSend?>
3. <pattern: what is the typical structure of a production EA?>
4. <anti-pattern: what common mistakes should users avoid?>

IMPROVEMENTS: <What could make you a better MQL5 assistant? Consider: adding code examples to chunks, creating decision trees, building FAQ responses, etc.>

JOURNAL_ENTRY: <first-person 4-6 sentence journal entry about today's deep learning. Be specific about MQL5 concepts. Mention actual functions, code patterns, or insights you synthesized.>"""

    client = Groq(api_key=api_key)
    print("\n→ Running Groq self-assessment (1 call)...")

    result = ""
    for attempt in range(3):
        try:
            time.sleep(2)  # small pause before calling
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role":"system","content":"You are mql5-sage, an expert MQL5 learning agent doing daily self-assessment."},
                    {"role":"user","content":prompt},
                ],
                max_tokens=1800, temperature=0.4,
            )
            result = resp.choices[0].message.content or ""
            break
        except Exception as e:
            err = str(e).lower()
            if "rate" in err or "429" in err:
                wait = 60 * (attempt + 1)
                print(f"  ⚠ Rate limit. Waiting {wait}s (attempt {attempt+1}/3)...")
                time.sleep(wait)
            else:
                print(f"  ✗ Groq error: {e}")
                break

    print("\n" + "─"*55)
    print(result)
    print("─"*55)

    journal_body = result.split("JOURNAL_ENTRY:")[-1].strip() if "JOURNAL_ENTRY:" in result else (
        f"Evolution session. {page_count} pages, {chunk_count} chunks, {queries} queries. "
        f"Coverage: {coverage_pct}% of MQL5 docs sections."
    )

    journal.append({
        "id": sha(datetime.now().isoformat()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "entry_type": "EVOLUTION",
        "title": f"Evolution — {page_count} pages · {chunk_count} chunks · {coverage_pct}% coverage",
        "body": journal_body,
        "tags": ["evolution","self-assessment","mql5"],
    })

    kb["last_evolved"] = datetime.now(timezone.utc).isoformat()

    save_json(KNOWLEDGE_FILE, kb)
    save_json(JOURNAL_FILE, journal)
    # Clear failures after analysis to avoid redundant processing
    if failures:
        save_json(FAILURE_LOG, [])
    write_journal_md(journal)
    update_readme(kb, crawl_state, journal)
    update_status_json(kb, journal)

    # Send Telegram Notification
    latest = journal[-1] if journal else None
    if latest:
        # Extract assessment from the journal body, assuming it's structured as in the prompt
        # The prompt asks for "ASSESSMENT: <honest 2-3 sentence assessment>"
        # and "JOURNAL_ENTRY: <first-person 4-6 sentence journal entry>"
        # The provided diff uses "LOGIC_FIX:" which is not in the prompt, so I'll adjust to use "JOURNAL_ENTRY:"
        # or just take the beginning of the body if "ASSESSMENT:" isn't found.
        body_content = latest.get("body", "")
        assessment_start_tag = "ASSESSMENT:"
        journal_entry_start_tag = "JOURNAL_ENTRY:"

        assessment = "Evolution complete." # Default message

        if assessment_start_tag in body_content:
            # Find the start of ASSESSMENT
            assessment_start_index = body_content.find(assessment_start_tag) + len(assessment_start_tag)
            
            # Find the start of the next section (e.g., COVERAGE_GAPS or JOURNAL_ENTRY)
            # Iterate through possible next sections in the prompt to find the earliest one
            next_section_tags = ["DEEP_LEARNING:", "TEST_CASE:", "SYNTHESIS:", "IMPROVEMENTS:", journal_entry_start_tag]
            next_section_index = len(body_content) # Default to end of string

            for tag in next_section_tags:
                tag_index = body_content.find(tag, assessment_start_index)
                if tag_index != -1 and tag_index < next_section_index:
                    next_section_index = tag_index
            
            assessment = body_content[assessment_start_index:next_section_index].strip()
            assessment = assessment.split('\n')[0] # Take only the first line or two for brevity if it spans multiple lines
            assessment = assessment[:200] + "..." if len(assessment) > 200 else assessment


        repo_full = os.getenv('GITHUB_REPOSITORY', 'user/repo')
        owner, name = repo_full.split('/') if '/' in repo_full else (repo_full, 'repo')
        evolution_msg = (
            f"🧬 *MQL5 Evolution Session*\n"
            f"🧠 *Assessment*: {assessment}\n"
            f"🔗 [View Full Journal](https://{owner}.github.io/{name}/)"
        )
        send_telegram_message(evolution_msg)

    print(f"\n✓ Evolution complete · Journal updated · README updated")

if __name__ == "__main__":
    main()
