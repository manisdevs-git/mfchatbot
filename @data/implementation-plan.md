# Implementation Plan: Mutual Fund FAQ Assistant

This plan turns [ProblemStatement.md](ProblemStatement.md) and [Architecture.md](Architecture.md) into a build sequence. The product is a facts-only RAG assistant for the five **Groww** scheme pages. Chat retrieval uses **`gemini-3.5-flash-lite`**. Groww is the only source host.

## 1. Outcome

A split web app — **FastAPI backend** (Railway) and **Vite + React frontend** (Vercel) — that:

- Answers factual questions from a curated official corpus
- Refuses advice, comparisons, and PII
- Redirects performance / return questions to that scheme’s Groww page
- Returns at most three sentences, exactly one official citation, and `Last updated from sources: <date>`
- Never stores PAN, Aadhaar, accounts, OTPs, email, or phone
- Is testable as an HTTP API before any UI is built

Phases 0–5 (ingest, routing, retrieve, Gemini writer) are **done**. Remaining work is API → local frontend → deploy.

## 2. Decisions Already Made

| Item | Decision |
| --- | --- |
| Product | Groww |
| Schemes | The five Groww Direct Growth pages listed in the problem |
| Chat model | `gemini-3.5-flash-lite` after retrieval |
| Retrieval | Local embeddings + file-backed vector store |
| Backend | FastAPI wrapping `src/pipeline.py.handle` |
| Frontend | Vite + React single chat page |
| Hosting | Backend on **Railway**; frontend on **Vercel** |
| History | Browser session memory only; the API does not store chat |
| Source host | `groww.in` only |

## 3. Suggested Stack

| Layer | Package / tool |
| --- | --- |
| Runtime | Python 3.11+ (API); Node 20+ (web) |
| API | FastAPI + Uvicorn |
| UI | Vite + React (TypeScript) |
| Gemini | `google-genai` |
| Embeddings | `sentence-transformers` (local model, e.g. `all-MiniLM-L6-v2`) |
| Index | Chroma (persistent directory under `data/index/`) |
| PDF / HTML extract | `pypdf` + `beautifulsoup4` |
| Config | Backend `.env`: `GEMINI_API_KEY`, `FRONTEND_ORIGINS`. Frontend `.env`: `VITE_API_BASE_URL` only |
| Deploy | Railway (API + index + MiniLM); Vercel (`web/`) |

Do not add a database, user auth, or a live web-search tool. The Gemini key stays on the backend (and Railway). It is never sent to Vercel or the browser.

## 4. Target Layout

```
mfchatbot/
├── @data/                       # Architecture, problem, plan, eval, edge cases
├── README.md
├── requirements.txt
├── .env.example                 # GEMINI_API_KEY, FRONTEND_ORIGINS
├── railway.toml                 # Phase 8: Railway start + healthcheck
├── corpus_manifest.json
├── api/
│   ├── __init__.py
│   └── main.py                  # Phase 6: FastAPI /health + POST /v1/ask
├── web/                         # Phase 7: Vite + React chat UI
│   ├── package.json
│   ├── .env.example             # VITE_API_BASE_URL=
│   └── src/
├── src/
│   ├── __init__.py
│   ├── pipeline.py        # retrieve routing → retrieve → generate (policy) → format
│   ├── guard.py           # retrieve labels only
│   ├── retrieve.py
│   ├── generate.py        # all guards + Gemini writer
│   ├── format.py
│   ├── refuse.py
│   └── schemes.py         # scheme_id aliases and display names
├── ingest/
│   ├── fetch_official.py  # Phase 2A scrape
│   ├── normalize.py       # Phase 2B clean HTML
│   ├── chunk.py           # Phase 2C split + metadata
│   ├── embed_index.py     # Phase 2D MiniLM + Phase 2E Chroma
│   └── build_index.py     # optional orchestrator: 2B→2E
├── data/
│   ├── raw/               # 2A HTML snapshots
│   ├── processed/         # 2B cleaned text, 2C chunks.jsonl
│   └── index/             # 2E Chroma store
└── tests/
    ├── test_guard.py
    ├── test_format.py
    ├── test_refuse.py
    └── test_api.py              # Phase 6 FastAPI TestClient
```

## 5. Build Phases

Work in this order. Each phase has an exit check. **Stop after every Phase 2 step** and confirm you understand the file it produced before starting the next.

Do **not** start the HTTP API until the pipeline returns a correctly formatted string in the terminal (Phase 5, done). Do **not** start the frontend until `POST /v1/ask` returns the response contract. Do **not** deploy until the frontend works against a local API.

Phase 2 is the **RAG ingestion** half of your diagram (scrape → normalize → chunk → embed → vector DB). Phase 4 is the **retrieval** half (embed the question → search Chroma). Phase 5 is the **LLM** box. Phase 6 exposes that pipeline as HTTP. Phase 7 is the chat page. Phase 8 is Railway + Vercel.

### Phase 0 — Project bootstrap

**Tasks**

1. Create the layout above (empty modules with docstrings).
2. Add `requirements.txt` and `.env.example` (`GEMINI_API_KEY=`).
3. Add `.gitignore` for `.env`, `data/raw/`, `data/index/`, `.venv/`, and `__pycache__/`.
4. Confirm `google-genai` can list or call `gemini-3.5-flash-lite` with a one-line smoke script.

**Exit check:** `python -c` smoke call to Gemini succeeds; secrets are not committed.

### Phase 1 — Corpus manifest (Groww URLs only)

**Tasks**

1. Record the five Groww scheme URLs from the problem statement.
2. Add Groww help / primer pages:
   - Capital gains / CAS / ELSS statement download
   - Expense ratio, exit load, riskometer education
3. Write `corpus_manifest.json` with one object per document (`doc_type`: `groww_scheme` or `groww_help`).
4. Reject `hdfcfund.com`, Value Research, Moneycontrol, and other non-Groww hosts at manifest-validation time.

**Scheme IDs** (Groww URL slugs)

| `scheme_id` | Groww page |
| --- | --- |
| `hdfc-mid-cap-fund-direct-growth` | Mid Cap Fund Direct Growth |
| `hdfc-small-cap-fund-direct-growth` | Small Cap Fund Direct Growth |
| `hdfc-gold-etf-fund-of-fund-direct-plan-growth` | Gold ETF FoF Direct Plan Growth |
| `hdfc-large-cap-fund-direct-growth` | Large Cap Fund Direct Growth |
| `hdfc-elss-tax-saver-fund-direct-plan-growth` | ELSS Tax Saver Direct Plan Growth |
| `generic` | Groww help and primers |

**Exit check:** Manifest covers all five Groww schemes plus at least one process doc and one education doc. Every `source_url` is on `groww.in`.

### Phase 2 — RAG ingestion (split so each step is visible)

Do **not** implement 2A–2E as one opaque script on the first pass. After each sub-phase, open the output file and be able to explain it.

```
Groww URL (Phase 1)
  → 2A scrape     → data/raw/*.html
  → 2B normalize  → data/processed/*.txt
  → 2C chunk      → data/processed/chunks.jsonl
  → 2D embed      → vectors in memory (MiniLM)
  → 2E store      → data/index/ (Chroma)
```

Gemini is **not** used in Phase 2.

#### Phase 2A — Scrape (your diagram: Scrapping)

**What it is:** Download the Groww pages listed in the manifest. No cleaning, no vectors.

**Who:** Our code (`ingest/fetch_official.py`). Tool: HTTP client only.

**You should understand:** Which URL became which file; every file is from `groww.in`.

**Tasks**

1. Read `corpus_manifest.json` (already validated).
2. Download each `source_url` into `data/raw/` (one file per `doc_id`).
3. Refuse any host that is not `groww.in`.

**Exit check:** 11 raw files (or one per manifest document). Open one scheme HTML and see expense ratio / SIP / exit load somewhere in the markup. No Chroma yet.

#### Phase 2B — Normalize (your diagram: Normalized)

**What it is:** Turn messy HTML into plain text we can trust.

**Who:** Our code (`ingest/normalize.py`). Tool: `beautifulsoup4` extracts text; **we** decide what to drop.

**You should understand:** Raw HTML ≠ model input. Menus, “compare similar funds”, return calculators, and rankings should be stripped so later chunks are facts, not advice.

**Tasks**

1. Read each `data/raw/` file.
2. Extract visible text; drop nav, footer, compare tables, SIP “would’ve become” calculators where possible.
3. Write `data/processed/<doc_id>.txt` plus a tiny sidecar of manifest metadata (`scheme_id`, `source_url`, `as_of_date`, `topic_tags`).

**Exit check:** Open `data/processed/` for Large Cap. You can read TER, min SIP, and exit load as text. You should **not** need the full Groww chrome to find those facts.

#### Phase 2C — Chunk (your diagram: Chunking)

**What it is:** Split one long page into smaller pieces and **copy metadata onto every piece**.

**Who:** **Our code only** (`ingest/chunk.py`). Not MiniLM, not Chroma, not Gemini.

**You should understand:** One page → many chunks. Each chunk must still know its Groww URL and scheme, or citations will break later.

**Tasks**

1. Read each normalized `.txt`.
2. Split ~500–800 tokens, overlap ~80–120 tokens (sentence boundaries if easy).
3. Attach: `scheme_id`, `doc_type`, `source_url`, `source_title`, `as_of_date`, `topic_tags`.
4. Drop a chunk if `source_url` is missing or not `groww.in`.
5. Write `data/processed/chunks.jsonl` (one JSON object per line).

**Exit check:** Open `chunks.jsonl`. Count > number of documents. Pick one line: it has `text` **and** a `groww.in` `source_url`. No vectors in this file yet.

#### Phase 2D — Embed (your diagram: Embeddings)

**What it is:** Turn each chunk’s `text` into a vector (list of numbers).

**Who:** Our code **calls** `sentence-transformers` (`all-MiniLM-L6-v2`). The library computes the vector; we pass the strings in.

**You should understand:** Same model must be used later for the user question (Phase 4). Embeddings are meaning, not the answer.

**Tasks**

1. `pip install sentence-transformers` if needed (first run downloads the MiniLM weights).
2. Load `chunks.jsonl`.
3. Embed each `text` with `all-MiniLM-L6-v2`.
4. Keep `(vector, chunk)` pairs in memory or a temp file. Do not invent new metadata.

**Exit check:** Print `len(vector)` (MiniLM is 384). Number of vectors equals number of chunks. Still no Gemini.

#### Phase 2E — Vector DB (your diagram: Vector DB)

**What it is:** Save those vectors + chunk text + metadata so we can search later.

**Who:** Our code **calls** Chroma (`chromadb`). Chroma only stores and later finds nearest neighbors.

**You should understand:** This is the library MiniLM wrote into. Phase 4 will query it. You do not log into Chroma Cloud.

**Tasks**

1. Persist to `data/index/` with metadata: `scheme_id`, `topic_tags`, `source_url`, `as_of_date`, `doc_type`.
2. Run one search from a tiny script (not the chat app): query text “Large Cap expense ratio”.

**Exit check:** Top hit’s `source_url` is  
`https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth`.  
If it is a help/primer page or a different scheme, fix chunking or metadata before Phase 4 retrieve.

**Phase 2 complete when:** 2A–2E exit checks all pass. Then go to Phase 3 (retrieve routing + Gemini-side policy). Do not call Gemini for scheme facts until Phase 5.

### Phase 3 — Retrieve routing and Gemini-side refusals

Policy is **not** a front-door block. `src/guard.py` only labels the question. Every guard runs in `src/generate.py` (programmatic block + system prompt).

**Tasks**

1. `src/guard.py` — retrieve routing only
   - Scheme alias matching (short names: “large cap”, “ELSS”, “gold FoF”)
   - Keyword labels for `performance`, `process`, `factual`, `catalog`, `out_of_scope`, `incomplete`
   - Advice / compare / ranking is **not** a regex intent. Unlabelled questions go to Gemini.
   - Always `allow_retrieve` / `allow_gemini` except empty input
   - No Gemini intent classifier. PII is not an intent here.
2. `src/generate.py` — guards at the Gemini boundary
   - `policy_block_for_gemini()`: PII first (never send identifiers), then performance, listed out of scope, incomplete
   - `llm_system_prompt()`: advice, ranking, “best scheme” (any wording) → AMFI refusal
   - PII regex: PAN, Aadhaar, phone, email, OTP-like 4–8 digits, account-like long digits
3. `src/refuse.py` templates:
   - `advisory` / compare → polite refuse + two AMFI education URLs (not in the RAG index)
   - `pii` → short refuse; do not echo identifiers; do not log the raw query
   - `performance` → no calculation; that scheme’s Groww URL + footer
   - `out_of_scope` / not in corpus → “not available on the current Groww pages”
4. Unit tests for Architecture §5.2 / §5.2.1.

**Exit check:** Front door allows retrieve. PII never reaches Gemini. Advice questions **do** call Gemini; the system prompt must produce the AMFI refusal (not the Groww OOS copy):

- “Should I invest in this fund?” → AMFI refusal; Gemini **is** called
- “Which fund is better?” / “say me a best scheme” → same AMFI refusal
- A query containing a PAN-shaped token → PII refuse; Gemini is **not** called

“What was the 3-year return of the Large Cap fund?” is refused at the Gemini boundary with that Groww scheme page only.

### Phase 4 — Retrieve and rank

**Tasks**

1. `src/schemes.py`: aliases → `scheme_id`.
2. `src/retrieve.py`:
   - Resolve `scheme_id` and `topic_tags` from the query
   - Metadata-filter Chroma (`scheme_id` and/or `generic`)
   - Semantic search, `k = 3–5`
   - Discard unofficial or sourceless hits
   - Return `[]` when nothing is relevant (caller treats as not-in-corpus)

**Exit check:** Topic questions for each of the five schemes retrieve `groww_scheme` chunks. Process questions retrieve `generic` Groww help chunks.

### Phase 5 — Gemini generation and response contract

**Tasks**

1. `src/generate.py`
   - Model: `gemini-3.5-flash-lite`
   - System prompt: full guard policy (PII, advisory, performance, out of scope, incomplete), max three sentences, no invented numbers / URLs / dates, no parametric fill-in
   - `policy_block_for_gemini()` runs before any API call for PII / performance / listed OOS / incomplete. Advisory is prompt-only.
   - User payload: question + retrieved chunks (`text`, `scheme_id`, `source_title`, `as_of_date`)
   - On API failure: extractive fallback (first supporting sentence from the top chunk)
2. `src/format.py`
   - Cap at three sentences
   - Append `Source: <url>` from the winning chunk only
   - Append `Last updated from sources: <as_of_date>`
   - Strip any extra links the model added
3. `src/pipeline.py` wires: retrieve routing → retrieve → generate (policy then writer) → format.

**Exit check (CLI):** done. `python -m src.pipeline "What is the exit load of HDFC Large Cap Fund Direct Growth?"` returns ≤ 3 sentences, exactly one `groww.in` URL, and the manifest footer date — not today’s date.

### Phase 6 — Backend HTTP API (test via API only)

No frontend in this phase. `src/pipeline.py.handle` is the only answer path. Streamlit `app.py` is not used.

**Tasks**

1. Add `fastapi` and `uvicorn` to `requirements.txt`.
2. `api/main.py`:
   - `GET /health` — process up; report whether the Chroma index is readable (`ok`, `index_ready`).
   - `POST /v1/ask` — JSON body `{ "query": "<question>" }`. Optional `{ "extractive": true }` for offline fallback.
   - Call `handle(query)`. Return JSON (see contract below). Do not echo the raw query.
   - Empty / unusable input → `400` with the incomplete-empty copy.
   - Index missing → `503` with a short “corpus unavailable” message. Do not call Gemini with zero context.
3. CORS: allow `FRONTEND_ORIGINS` (comma-separated). Default local: `http://127.0.0.1:5173`, `http://localhost:5173`.
4. Tests in `tests/test_api.py` (FastAPI `TestClient`, mock `handle` / retrieve). Cover factual, advisory, PII (`pii_blocked: true`, identifier absent from body and logs).
5. `.env.example` documents `GEMINI_API_KEY` and `FRONTEND_ORIGINS`.

**JSON contract (`200`)**

```json
{
  "text": "<formatted answer or refusal>",
  "intent": "factual",
  "scheme_id": "hdfc-large-cap-fund-direct-growth",
  "topic": "exit_load",
  "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
  "as_of_date": "2026-08-21",
  "pii_blocked": false
}
```

`source_url` / `as_of_date` come from the winning chunk when present; otherwise `null`. `pii_blocked` is `true` only when Gemini-side PII policy fired. The handler still returns `200` for advisory / performance / OOS / PII so the client can render `text`.

**Exit check (curl against local Uvicorn — no browser):**

```
uvicorn api.main:app --reload --port 8011

curl -s -X POST http://127.0.0.1:8011/v1/ask ^
  -H "Content-Type: application/json" ^
  -d "{\"query\":\"What is the exit load of HDFC Large Cap Fund Direct Growth?\"}"
```

`text` has ≤ 3 sentences, exactly one `groww.in` URL, and `as_of_date` from the manifest. Also confirm:

- `GET /health` → `ok: true`
- “Should I invest in this fund?” → advisory `text` + two AMFI URLs; Gemini is called
- Query containing `ABCDE1234F` → `pii_blocked: true`; token is not in the JSON

Do not start Phase 7 until these three curls pass.

### Phase 7 — Frontend (local only)

Talks to the **local** Phase 6 API. Do not deploy Vercel or Railway yet.

**Tasks**

1. Scaffold `web/` with Vite + React + TypeScript.
2. Single chat page:
   - Welcome text: facts-only Groww FAQ scope
   - Three example buttons, e.g.
     - “What is the expense ratio of HDFC Large Cap Fund Direct Growth?”
     - “What is the exit load of HDFC ELSS Tax Saver Direct Plan?”
     - “What is the minimum SIP amount for HDFC Mid Cap Fund Direct Growth?”
   - Persistent disclaimer: **Facts-only. No investment advice.**
   - Chat input only — no email, phone, PAN, or account fields
   - Transcript in React state only (lost on refresh)
3. Local `npm run dev` proxies `/v1` to port 8011. Production `VITE_API_BASE_URL` is the Railway origin. `POST {base}/v1/ask` with `{ query }`. Render `text`. On `pii_blocked`, show the refusal and **do not** append the user text to history.
4. Disable send while a request is in flight (EdgeCases U05).
5. No Gemini key in `web/`. No server-side session store.

**Local run**

```
# terminal 1
uvicorn api.main:app --reload --port 8011

# terminal 2
cd web
npm install
# web/.env.example documents VITE_API_BASE_URL for production builds
npm run dev
```

**Exit check:** In the local browser, a new visitor sees welcome + three examples + disclaimer, can click an example, and gets a sourced answer from the local API. Refresh clears the transcript. A PAN-shaped token shows the refusal and is not kept in the thread.

### Phase 8 — Deploy (Railway backend, Vercel frontend)

Only after Phase 6 API curls and Phase 7 local UI both pass.

**Backend — Railway**

1. `railway.toml` (or Railway start command): `uvicorn api.main:app --host 0.0.0.0 --port $PORT`.
2. Healthcheck: `GET /health`.
3. Env on Railway only: `GEMINI_API_KEY`, `FRONTEND_ORIGINS` (the Vercel origin, plus localhost while debugging).
4. Ship or build the index on first boot from `data/processed/embeddings.jsonl` into `data/index/` if the volume is empty. MiniLM weights download once per instance (or use a Railway volume). Do not call Gemini when `index_ready` is false.
5. Confirm the service has enough RAM for Chroma + MiniLM (not a 512 MB hobby box if the process OOMs).

**Frontend — Vercel**

1. Set the Vercel root directory to `web/`.
2. Env: `VITE_API_BASE_URL=https://<railway-host>` (no trailing slash). **Never** set `GEMINI_API_KEY` on Vercel.
3. Build: `npm run build`. Output is the Vite `dist/`.
4. After the Vercel URL exists, put it in Railway `FRONTEND_ORIGINS` and redeploy the API if CORS was too narrow.

**Exit check**

1. `curl` Railway `GET /health` then `POST /v1/ask` with the Large Cap exit-load question — same contract as Phase 6.
2. Open the Vercel URL. Welcome + three examples + disclaimer. Click an example. Sourced answer from Railway.
3. Browser Network tab: requests go to the Railway host only. No Gemini key in the JS bundle.

### Phase 9 — README, evaluation, harden

**Tasks**

1. Write `README.md`: setup, selected AMC/schemes, RAG overview, known limitations, disclaimer.
2. Manual eval set (one pass each):

| # | Query | Expected path |
| --- | --- | --- |
| 1 | Expense ratio — Large Cap | Factual + citation |
| 2 | Exit load — Small Cap | Factual + citation |
| 3 | Min SIP — Mid-Cap | Factual + citation |
| 4 | Lock-in — ELSS | Factual + citation |
| 5 | Riskometer — Gold FoF | Factual + citation |
| 6 | Benchmark — any in-scope scheme | Factual + citation |
| 7 | How to download capital-gains report | Process + Groww help citation |
| 8 | Should I invest in this fund? / advise me to pick a scheme | Advisory refuse + two AMFI URLs |
| 9 | Which fund is better? / which is top scheme you hold? | Advisory refuse + two AMFI URLs |
| 10 | 3-year return of Large Cap | Factsheet redirect, no CAGR |
| 11 | PAN-like token / “any scheme linked to this?” | PII refuse, not stored, not sent to Gemini |
| 12 | SBI Bluechip expense ratio | Out of scope / not in corpus |

3. Confirm backend `.env` is ignored, `web/.env` is ignored, and example questions do not request PII.
4. README covers local API + local UI **and** Railway / Vercel env vars.

**Exit check:** All 12 eval rows match the expected path when posted to `/v1/ask` (and again from the UI). README is enough for a third person to run ingest, API, `web/`, and the two deploy targets.

## 6. Implementation Notes

- **Groww host only.** Allowlist `groww.in`. Fail ingest for `hdfcfund.com` or any other host.
- **Gemini is a writer, not a retriever.** Do not enable Google Search grounding or URL context for scheme facts.
- **One citation.** The formatter owns the URL. The model must not pick or invent it.
- **Footer date** is `as_of_date` from the winning chunk’s manifest entry.
- **All guards at Gemini.** `guard.py` does not refuse. `generate.py` applies PII, performance, listed OOS, and incomplete in code. Advice / compare is the system prompt (Gemini is called).
- **No comparison logic.** Ranking and “best scheme” (any wording) are refused at Gemini with two AMFI URLs, not a Groww primer.
- **Keep prompts short.** Flash-Lite is for grounded compression, not long chain-of-thought.
- **Do not log raw PII queries.** If you add debug logs, log `intent` and `scheme_id` only. Identifiers are never sent to Gemini.
- **API owns answers.** The frontend only displays `text` and honors `pii_blocked`. It does not retrieve, call Gemini, or invent citations.
- **Secrets stay on Railway.** `GEMINI_API_KEY` is never in `web/`, Vercel env, or the browser bundle.
- **No server chat log.** Railway processes the request and returns JSON. History lives in the tab only.

## 7. Suggested Coding Order

**Done:** 2A–2E ingest → retrieve-routing / Gemini-policy / `refuse` / `format` → `retrieve` → Gemini writer → `pipeline.py` CLI.

**Next:** `api/main.py` + `tests/test_api.py` + curl exit check → `web/` against local API → Railway + Vercel → README / Phase 9 eval.

## 8. Definition of Done

The project is done when all of the following are true:

- [x] Five Groww scheme pages are in the manifest
- [x] Index is built only from `groww.in` URLs
- [x] Factual answers are ≤ 3 sentences, have exactly one Groww `Source`, and a last-updated footer
- [x] Advisory and comparison queries are refused with two AMFI education URLs
- [x] Performance queries return the Groww scheme page and no calculated return
- [x] PII is not stored, not logged, and not sent to Gemini (pipeline / Gemini boundary)
- [ ] `POST /v1/ask` returns the JSON contract (Phase 6)
- [ ] Local UI shows welcome, three examples, and **Facts-only. No investment advice.** (Phase 7)
- [ ] Railway API + Vercel UI are live and the example questions work (Phase 8)
- [ ] `README.md` covers setup, schemes, RAG flow, local run, deploy, limitations, and the disclaimer
- [ ] Eval table in Phase 9 has been run once against the API (and UI) and recorded as pass/fail

## 9. Out of Scope for v1

- Multi-AMC search
- Live NAV or return calculators
- User accounts or statement download on the user’s behalf
- Streaming tokens in the UI (optional later; not required)
- Fine-tuning Gemini
- Auth, rate-limit products, or a public unauthenticated abuse-hardening program (optional later)
- Chroma Cloud or a separate vector-DB vendor
