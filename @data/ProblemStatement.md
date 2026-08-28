# Problem Statement: Mutual Fund FAQ Assistant (Facts-Only Q&A)

## Overview

The objective of this project is to build a facts-only FAQ assistant for mutual fund schemes, using **Groww** as the product and the corpus. The assistant will answer objective, verifiable queries by retrieving information from the Groww scheme pages listed below and from Groww help / primer pages.

The system must strictly avoid providing investment advice, opinions, or recommendations. Every response must include a single, clear source link and adhere to defined constraints around clarity, accuracy, and compliance.

## Objective

Design and implement a lightweight Retrieval-Augmented Generation (RAG)-based assistant that:

- Answers factual queries about mutual fund schemes
- Uses a curated corpus of official documents
- Provides concise, source-backed responses

## Target Users

- Retail investors comparing mutual fund schemes
- Customer support and content teams handling repetitive mutual fund queries

## Scope of Work

### 1. Corpus Definition

In-scope schemes are the five **Groww** pages given in the problem (category mix: mid-cap, small-cap, gold FoF, large-cap, ELSS):

| Groww page | Category | Source URL |
| --- | --- | --- |
| Mid Cap Fund Direct Growth | Mid-cap | https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth |
| Small Cap Fund Direct Growth | Small-cap | https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth |
| Gold ETF Fund of Fund Direct Plan Growth | Gold / FoF | https://groww.in/mutual-funds/hdfc-gold-etf-fund-of-fund-direct-plan-growth |
| Large Cap Fund Direct Growth | Large-cap | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| ELSS Tax Saver Fund Direct Plan Growth | ELSS | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth |

### 2. FAQ Assistant Requirements

The assistant must answer facts-only queries, such as:

- Expense ratio of a scheme
- Exit load details
- Minimum SIP amount
- ELSS lock-in period
- Riskometer classification
- Benchmark index
- Process to download statements or capital gains reports

Each response must:

- Be limited to a maximum of 3 sentences
- Include exactly one citation link
- Include a footer: `Last updated from sources: <date>`

### 3. Refusal Handling

The assistant must refuse non-factual or advisory queries, such as:

- “Should I invest in this fund?”
- “Which fund is better?”

Refusal responses should:

- Be polite and clearly worded
- Reinforce the facts-only limitation
- Provide a relevant educational link (AMFI investor education and AMFI mutual fund risks — not in the RAG index)

### 4. User Interface (Minimal)

The chat UI is a separate web frontend. Answers come only from the backend HTTP API (`POST /v1/ask`). The page must include:

- A welcome message
- Three example questions
- A visible disclaimer: **“Facts-only. No investment advice.”**

Hosting: backend on Railway, frontend on Vercel. The Gemini key and the Groww index stay on the backend.

## Constraints

### Data and Sources

- Use **Groww** scheme pages and Groww help / primer pages only
- Do not use AMC sites (for example hdfcfund.com), Moneycontrol, Value Research, or other non-Groww hosts

### Privacy and Security

Do not collect, store, or process:

- PAN or Aadhaar numbers
- Account numbers
- OTPs
- Email addresses or phone numbers

### Content Restrictions

- No investment advice or recommendations
- No performance comparisons or return calculations
- For performance-related queries, provide a link to that scheme’s Groww page only (do not calculate returns)

### Transparency

- Responses must be short, factual, and verifiable
- Every answer must include a source link and last updated date

## Expected Deliverables

### README Document

- Setup instructions (local API + local UI, then Railway / Vercel)
- Selected Groww schemes
- Architecture overview (RAG approach, API contract)
- Known limitations

### Disclaimer Snippet

> Facts-only. No investment advice.

## Success Criteria

- Accurate retrieval of factual mutual fund information
- Strict adherence to facts-only responses
- Consistent inclusion of valid source citations
- Proper refusal of advisory queries
- Clean, minimal, and user-friendly interface

## Summary

The goal is to build a trustworthy, transparent, and compliant mutual fund FAQ assistant that prioritizes accuracy over intelligence. The system should ensure that users receive only verified, source-backed financial information, without any advisory bias or speculative content.
