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
    sections    = sorted(set(p.get("section","") for p in kb.get("pages",{}).values()))
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
| 🤖 Model | `groq/compound` |

## 📚 Sections Learned ({len(sections)}/37)
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

    # Sections coverage
    sections_done = sorted(set(p.get("section","") for p in kb.get("pages",{}).values()))
    all_sections = 37
    coverage_pct = round(len(sections_done) / all_sections * 100)

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

    # Uncrawled sections
    from pathlib import Path as P
    config = load_json(P("config/mql5_sections.json"), {"sections":[]})
    all_slugs = {s["slug"] for s in config.get("sections",[])}
    uncrawled = all_slugs - set(sections_done)

    prompt = f"""You are mql5-sage performing a daily self-assessment.

CURRENT STATE:
- MQL5 doc pages crawled: {page_count} (coverage: {coverage_pct}% of 37 sections)
- Knowledge chunks: {chunk_count}
- Total queries answered: {queries}
- Sections learned: {', '.join(sections_done) if sections_done else 'none yet'}
- Sections not yet crawled: {', '.join(list(uncrawled)[:10]) if uncrawled else 'all covered!'}
- Most queried topics: {', '.join(f'{s}({c})' for s,c in top_sections) if top_sections else 'none yet'}
- Recent questions asked: {chr(10).join(f'- {q}' for q in recent_queries) if recent_queries else '- none yet'}

RECENT_FAILURES:
{json.dumps(failures, indent=2) if failures else "No recent ingestion failures."}

Respond in EXACTLY this format:

ASSESSMENT: <honest 2-3 sentence assessment of current knowledge coverage>

COVERAGE_GAPS: <which MQL5 sections are missing and why they matter for trading>

QUERY_PATTERNS: <what patterns do you see in the questions being asked?>

INSIGHTS:
1. <specific MQL5 technical insight from the crawled docs>
2. <cross-section connection e.g. how trading functions relate to event handlers>
3. <practical insight for someone building an Expert Advisor>
4. <something about the MQL5 language structure or patterns>

NEXT_CRAWL: <which section slug should be crawled next and why>

SELF_REPAIR: <If there are FAILURES above, analyze them. Is it a Regex issue? A missing DIV? Propose a specific fix for the 'extract_clean_content' function in crawl.py. If no failures, say 'System operating within normal parameters.'>

REFLECTION: <Ask yourself: "What logic in my crawl script is currently slowing me down, and how should I change it?" Analyze your own efficiency and propose one technical optimization.>

JOURNAL_ENTRY: <first-person 4-6 sentence journal entry about today's evolution. Be specific about MQL5 concepts. Mention actual functions, sections, or patterns you've observed.>"""

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
            next_section_tags = ["COVERAGE_GAPS:", "QUERY_PATTERNS:", "INSIGHTS:", "NEXT_CRAWL:", "SELF_REPAIR:", "REFLECTION:", journal_entry_start_tag]
            next_section_index = len(body_content) # Default to end of string

            for tag in next_section_tags:
                tag_index = body_content.find(tag, assessment_start_index)
                if tag_index != -1 and tag_index < next_section_index:
                    next_section_index = tag_index
            
            assessment = body_content[assessment_start_index:next_section_index].strip()
            assessment = assessment.split('\n')[0] # Take only the first line or two for brevity if it spans multiple lines
            assessment = assessment[:200] + "..." if len(assessment) > 200 else assessment


        evolution_msg = (
            f"🧬 *MQL5 Evolution Session*\n"
            f"🧠 *Assessment*: {assessment}\n"
            f"🔗 [View Full Journal](https://{os.getenv('GITHUB_REPOSITORY_OWNER', 'user')}.github.io/{os.getenv('GITHUB_REPOSITORY_NAME', 'repo')}/)"
        )
        send_telegram_message(evolution_msg)

    print(f"\n✓ Evolution complete · Journal updated · README updated")

if __name__ == "__main__":
    main()
