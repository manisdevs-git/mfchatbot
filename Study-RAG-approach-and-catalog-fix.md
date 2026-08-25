# Study: RAG approach, why the prompt worked, and the catalog fix

A personal reference for this project. It records **what was built**, **why the constrained Gemini prompt behaved well**, and **why “exit loads of all schemes in a table” failed then started working** — without re-chunking or re-embedding.

Companion docs: [ProblemStatement.md](ProblemStatement.md), [Architecture.md](Architecture.md), [implementation-plan.md](implementation-plan.md), [EdgeCases.md](EdgeCases.md).

---

## 1. What this product is

A **facts-only FAQ** over **five Groww Direct Growth scheme pages** plus Groww help / primers. It is not a general investment advisor and not a live web search.

**Hard product rules (v1, still true for single-scheme answers):**

- Answer only what official `groww.in` chunks support.
- At most **three sentences** for one scheme + one topic.
- Exactly **one** Groww `Source:` URL (formatter owns it; the model must not invent it).
- Footer: `Last updated from sources: <manifest as_of_date>` — not “today”.
- Refuse advice, comparisons, and return calculations.
- Never send PAN / Aadhaar / phone / email / OTP / folio to Gemini or into chat history.

**Catalog exception (added later):** one factual topic **across all five schemes** may be a **table** with one Groww URL per row. That is a new intent, not a loosening of advisory/compare.

---

## 2. Approach that was followed

Work was done in the plan’s order so each layer could be tested before the next.

```
Phase 1  Manifest (Groww URLs only)
   ↓
Phase 2  Ingest: scrape → normalize → chunk → MiniLM embed → Chroma
   ↓
Phase 3  Retrieve routing (guard.py) + Gemini-side policy (generate.py)
   ↓
Phase 4  Retrieve (same MiniLM + Chroma metadata filters)
   ↓
Phase 5  Gemini writer + formatter (3 sentences, one citation)
   ↓
Phase 6  FastAPI  GET /health  POST /v1/ask
   ↓
Phase 7  Vite + React chat (browser state only)
```

### 2.1 Offline RAG half (ingest)

Gemini is **not** used here. The corpus is curated once:

| Step | Who | Output |
| --- | --- | --- |
| Scrape | HTTP client | `data/raw/*.html` from `groww.in` only |
| Normalize | BeautifulSoup + our drop rules | `data/processed/*.txt` (nav, compare tables, SIP “would’ve become” stripped where possible) |
| Chunk | Our code | `chunks.jsonl` — text **plus** `scheme_id`, `source_url`, `as_of_date`, `topic_tags` |
| Embed | `all-MiniLM-L6-v2` (384-d) | vectors; **same model** must embed the question later |
| Store | Chroma under `data/index/` | vectors + metadata for filtered search |

**Why metadata on every chunk matters:** later citations and filters cannot invent a URL. If a chunk has no Groww `source_url`, retrieve drops it.

### 2.2 Online RAG half (chat)

```
UI  →  POST /v1/ask { query }
        → classify()          # label only; does not refuse (except empty)
        → retrieve()          # MiniLM + Chroma; Groww hits only
        → policy_block()      # PII / advisory / performance / OOS / incomplete
        → catalog? table      # no Gemini
        → else Gemini writer  # or extractive fallback
        → format_response()   # cap + one Source + footer
        → JSON { text, intent, scheme_id, topic, source_url, as_of_date, pii_blocked }
```

Split deploy: **browser never holds the Gemini key or the index.** UI only renders `text` and honors `pii_blocked`.

### 2.3 Two-layer policy (this is the core design)

| Layer | File | Job |
| --- | --- | --- |
| Front door | `src/guard.py` | **Label** `intent`, `scheme_id`, `topic`. Allow retrieve for almost everything. |
| Gemini boundary | `src/generate.py` | **Refuse** in code first (`policy_block_for_gemini`), then the **same rules** in `llm_system_prompt()`. |

PII is not a retrieve intent. A PAN-shaped token can still be labeled `factual`; identifiers are stripped at the Gemini boundary and never logged as the raw query.

Priority (first match wins):

`PII > advisory/compare > performance > out_of_scope > incomplete > catalog > process/factual`

---

## 3. Why the prompt (and the whole writer design) worked well

The system prompt in `llm_system_prompt()` is not “be a helpful finance bot.” It is a **copy of the programmatic guards**, written for `gemini-3.5-flash-lite` as a **short, grounded compressor**.

### 3.1 Gemini is a writer, not a retriever

What we **did not** do:

- Google Search grounding / URL context for scheme facts
- Asking the model “what is the TER of Large Cap?” from parametric memory
- Letting the model pick or invent a citation

What we **did**:

1. Retrieve official chunks first.
2. Block policy-violating questions **before** `generate_content`.
3. Send: system policy + question + chunk **text** (scheme_id, title, as_of_date — **not** `source_url`).
4. Let the formatter attach `Source:` from the winning chunk.

If the model is wrong or the API is down, **extractive fallback** copies the first supporting sentence from the top chunk. Accuracy over fluency.

### 3.2 Why this prompt shape is stable

| Prompt choice | Why it worked |
| --- | --- |
| Same rules in code **and** in the system prompt | If a question slips through, the model still has the refusal copy. Code is the source of truth for PII (never send the token). |
| Explicit priority list | Stops mixed turns (“Should I invest? Also what is TER?”) from answering the factual half. Advisory wins. |
| “At most three sentences” + “no invented numbers / URLs / dates” | Flash-Lite is good at short compression; it is bad at unconstrained finance essays. The cap matches the product. |
| “Do not write Source or last-updated” | Citation cannot drift to Moneycontrol or a hallucinated Groww path. `format.py` owns the URL. |
| “Do not use parametric knowledge to fill gaps” | Knowledge cutoff and training data are not a source. If the chunk lacks the fact → “not available on the current Groww pages.” |
| Allowed schemes listed with official URLs | Reduces “SBI / other HDFC scheme” fill-in. |
| Expense `1.25%` and SIP `500` called out as **not** PII | Avoids false PII blocks on the facts we exist to answer. |
| Temperature `0.1`, `max_output_tokens` 256 | Short, low-variance replies. |
| No tools / no automatic function calling | Model cannot browse. |

### 3.3 Why “prompt only” would have been weaker

If policy lived **only** in the prompt:

- A PAN could still be sent to Google.
- Advisory questions might still get a fluent “it depends on your risk profile.”
- Two URLs in the model text would ship unless the formatter stripped them.

The working pattern is: **programmatic block → constrained writer → mechanical formatter.** The prompt is the second lock, not the only lock.

### 3.4 What a good single-scheme answer looks like

Query: `What is the exit load of HDFC Large Cap Fund Direct Growth?`

1. `classify` → `factual`, `scheme_id=hdfc-large-cap-fund-direct-growth`, `topic=exit_load`
2. Chroma filter on that `scheme_id`, prefer `exit_load` tags
3. Policy: not blocked
4. Gemini (or extractive) writes one or two sentences from the chunk
5. Formatter:

```
<≤3 sentences>

Source: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth

Last updated from sources: 2026-08-21
```

That path is why factual examples felt “correct”: retrieve + format, not model memory.

---

## 4. The failure: “show me exit loads of all schemes in table”

**User-visible reply (before the fix):**

> That is not available on the current Groww pages in this assistant. I cover five HDFC Direct Growth schemes…

That text is `OUT_OF_SCOPE_REFUSAL`. It sounds like “Groww doesn’t have this.” **That was false.** All five scheme chunks already contained exit load (Large/Mid/Small: 1% within 1 year; Gold FoF: 1% within 15 days; ELSS: Nil).

### 4.1 What `classify()` actually saw

The router is **regex + aliases**, not an LLM intent classifier.

| Detector | Pattern then | Query token | Match? |
| --- | --- | --- | --- |
| Topic `exit_load` | `\bexit load\b` | `exit loads` | **No** — `s` is a word character, so the boundary after `load` fails |
| Scheme | aliases like `large cap`, `elss` | `all schemes` | **No** — not an alias |
| Advisory | `compare`, `better`, `vs` | none | No |
| Other-AMC OOS | SBI, Bluechip, … | none | No |

Then the ladder ran out:

```
advisory? no
performance? no
listed OOS? no
two named schemes? no
process? no
topic AND scheme? no
topic AND NOT scheme? no  (topic was empty)
scheme AND NOT topic? no
→ out_of_scope, reason=unknown
```

`unknown` is mapped to **out of scope**. Policy treats `out_of_scope` as a refuse intent. Gemini is not asked to write a table. Retrieve may still run, but the **answer is the refusal**, not the chunks.

### 4.2 A second, independent limit (even with better wording)

`exit load of all schemes` (singular) **would** have set `topic=exit_load` and then `incomplete` / `scheme_required`: “Please name **one** in-scope scheme…”

That is the old **product contract**: one scheme, three sentences, one URL, no tables. A five-row grid was not “missing from Groww”; it was **no handler for that shape**.

### 4.3 The real lesson

```
Wrong label  →  wrong policy  →  wrong user message
```

RAG did not fail. The **taxonomy had no box** for “one topic × all in-scope schemes.” Unknown boxes were labeled “not in corpus,” which is a misleading error.

**Debug habit:** print `classify(query)` (`intent`, `topic`, `scheme_id`, `reason`) before blaming Chroma or Gemini.

---

## 5. The fix (catalog path)

We did **not** re-scrape, re-chunk, or re-embed. We added a handler for that question shape.

### 5.1 Route: new intent `catalog`

In `src/guard.py`:

- Topic regexes accept **plurals**: `exit loads?`, `expense ratios?`, `sips?`, …
- `ALL_SCHEMES_RE` detects `all/every/each (five) schemes/funds`, `across all schemes`
- After advisory / performance / listed OOS:

  - `wants_catalog` **and** topic → `intent=catalog`
  - `wants_catalog` **and** no topic → still `incomplete` (ask for a topic)
- `Compare exit loads of all schemes` stays **advisory** (`compare` wins first). Catalog **lists**; it does not rank.

`catalog` is **not** in `POLICY_INTENTS`. Policy does not refuse it.

### 5.2 Retrieve: one hit per scheme

`retrieve()` on `catalog` loops `CATALOG_SCHEME_IDS` (Large → Mid → Small → Gold FoF → ELSS):

- Same query embedding (one MiniLM call)
- Chroma `where: { scheme_id: <that scheme> }`
- Prefer chunks whose `topic_tags` include the topic
- Keep official `groww.in` hits only

Five filtered searches on the **existing** index.

### 5.3 Format: table, not three sentences

`format_catalog()` in `src/format.py`:

- One markdown row per scheme
- Cell text = first supporting sentence copied from that chunk (extractive; no paraphrase)
- Source column = that chunk’s Groww URL
- Footer date from the chunks’ `as_of_date`

Pipeline short-circuits: **no Gemini** for catalog (lower risk of ranking or invented numbers).

```
if intent == catalog:
    return format_catalog(chunks, topic)
```

API JSON: `source_url` is `null` (five URLs live in the table). `intent` is `catalog`.

### 5.4 UI

`web/src/App.tsx` parses a markdown table and renders HTML. Disclaimer and PII rules unchanged.

### 5.5 After vs before (same query)

| | Before | After |
| --- | --- | --- |
| `classify` | `out_of_scope` / `unknown` | `catalog` / `exit_load` |
| Policy | Refuse OOS copy | Not blocked |
| Retrieve used for the answer? | No | Yes, 5 scheme hits |
| Gemini | Not used to write | Not used (extractive table) |
| User sees | “Not available on current Groww pages” | Table + five Groww links + footer |

### 5.6 Why ingest did not change

`data/processed/chunks.jsonl` already had, for each of the five schemes:

- Visible exit-load line in `text`
- `topic_tags` including `exit_load`
- Official `source_url`

The gap was **routing + formatting**, not missing vectors.

---

## 6. Patterns to reuse on the next RAG project

1. **Label before you retrieve for the answer.** If `reason=unknown`, fix the taxonomy before tuning embeddings.
2. **Unknown ≠ not in corpus.** Use a distinct incomplete message when the shape is “I need one scheme,” not the OOS Groww copy.
3. **Regex topics must include morphology** (`load` / `loads`) or users will silently miss the topic.
4. **Product shapes need intents.** “All rows for one field” is not the same as “one scheme FAQ” and not the same as “which is better.”
5. **Keep Gemini after retrieve.** Prompt quality matters; it does not replace metadata filters or a formatter.
6. **Dual policy:** code block for safety (PII), prompt for residual obedience, formatter for citations.
7. **Extractive fallback / extractive catalog** when the output is a structured grid of numbers already in the chunks.
8. **Do not re-ingest by default.** Confirm chunks already contain the fact (`topic_tags` + text) before spending another MiniLM pass.

---

## 7. Quick checklist if a similar bug appears

```text
1. python -m src.guard "THE QUERY"
   → intent, topic, scheme_id, reason

2. If reason=unknown or topic is None:
   fix regex / aliases / add an intent. Stop.

3. If intent is in POLICY_INTENTS and should not be:
   change classify or POLICY_INTENTS, not the prompt alone.

4. If intent is factual/catalog but the fact is wrong:
   then inspect retrieve hits and chunk text / tags.

5. Only then consider re-chunk / re-embed.
```

Useful files: `src/guard.py`, `src/retrieve.py`, `src/generate.py`, `src/format.py`, `src/pipeline.py`, `tests/test_catalog.py`.

---

## 8. File map

| Concern | Where |
| --- | --- |
| Scheme aliases | `src/schemes.py` |
| Labels | `src/guard.py` |
| Search | `src/retrieve.py` |
| Prompt + policy block | `src/generate.py` |
| One citation vs catalog table | `src/format.py` |
| Wire order | `src/pipeline.py` |
| HTTP contract | `api/main.py` |
| Chat render | `web/src/App.tsx` |
| Catalog tests | `tests/test_catalog.py` |

---

*Written after Phase 6–7 and the catalog path. Single-scheme answers still use the three-sentence / one-Source contract. Catalog is the explicit exception for “all in-scope schemes + one factual topic.”*
