# 🧠 mql5-sage

> A self-evolving MQL5 documentation agent — learns from the live mql5.com docs,
> answers your MQL5 questions, and evolves daily. Runs entirely on GitHub Actions.

## 📊 Knowledge Base

| Metric | Value |
|--------|-------|
| 📄 Pages Crawled | 400 |
| 🧩 Knowledge Chunks | 2390 |
| 🔍 Queries Answered | 0 |
| 📓 Journal Entries | 15 |
| 🕷️ Last Crawled | 2026-03-12T15:12:19.149197+00:00 |
| 🤖 Model | `groq/compound` |

## 📚 Sections Ingested (21/37)

`account`, `array`, `basis`, `chart_operations`, `check`, `common`, `constants`, `convert`, `customind`, `dateandtime`, `event_handlers`, `files`, `indicators`, `marketinformation`, `math`, `objects`, `predefined`, `runtime`, `series`, `strings`, `trading`

## 🚀 How to Use

### Ask a question
**Actions → 🔍 Ask MQL5 Question → Run workflow → type question → Run**

### Start crawling (first time)
**Actions → 🕷️ Crawl MQL5 Docs → Run workflow → Run**
*(Crawls ~40 pages per run. Re-run to continue. Full coverage in ~15 runs.)*

### Crawl one specific section
**Actions → 🕷️ Crawl MQL5 Docs → Run workflow → enter section slug (e.g. `trading`) → Run**

### Watch it evolve
Runs automatically every day at 9am UTC. Nothing needed from you.

## ⚙️ One-Time Setup

1. Fork this repo
2. **Settings → Secrets → Actions → New secret:**
   - `GROQ_API_KEY` → from [console.groq.com](https://console.groq.com) (free)
3. Enable workflow write permissions: **Settings → Actions → General → Read and write permissions**
4. Run the **🕷️ Crawl** workflow to start building knowledge

## 📓 Journal
[JOURNAL.md](./JOURNAL.md) — every crawl, query, and evolution session recorded.

---
*Powered by Groq compound · Source: mql5.com/en/docs*
