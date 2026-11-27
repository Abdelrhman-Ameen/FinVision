# Knowledge base for the startup assistant

This is the evidence library for the Egyptian Startup Intelligence System. The snapshot from 20 November 2026 covers 24 sectors and 33 founder decisions, including branch expansion, pricing, liquidity, debt, hiring, stock, supply delays, climate and exchange-rate shocks. Start with the [coverage report](coverage/coverage_report.md) to see what each sector has and where the evidence is thin.

The reviewed Arabic cards include English summaries and search terms in both languages. Each card identifies the geography, study population or reference period, and a suggested application. The World Bank Open Knowledge Repository catalog adds licensed publisher abstracts and full-text passages. The book library indexes reusable prose by PDF page. Annual indicators are stored with their dates and units for exact lookup.

| Collection | Coverage |
| --- | --- |
| [Egypt](cards/egypt.json) | Central Bank and World Bank data references, the IMF’s macroeconomic analysis, national sector and SME reports, and Egyptian randomized studies on exporting and microenterprise finance |
| [Recent Egypt sources](cards/egypt_recent_2026.json) | October 2026 urban inflation and poultry price contribution, November 2026 policy rates, FY 2025/26 real GDP and restaurant activity, and 2026 USDA food retail and feed reports |
| [Food-business operations](cards/food_operations.json) | Recipe yield, menu contribution, operating break-even, and local demand tests for expansion |
| [Regional and international research](cards/research.json) | Microcredit, entrepreneurship training, bookkeeping, working practices, grants, inventory, A/B testing and how to test business ideas |
| [Practical finance methods](cards/practical.json) | Cash conversion cycle, cash-flow monitoring and financial ratios |
| [Sector sources](cards/sectors.json) and [decision studies](cards/decision_studies.json) | National and sector sources, Egypt Enterprise Survey 2025, and studies on pricing, retention, faster payment and digital adoption |
| [Sector map](coverage/sectors.json) and [scenario map](coverage/scenarios.json) | Priority scenarios by business type, required company data, expected output, and linked evidence |
| [Publication catalog](data/publications.jsonl) | 10,604 distinct publication records, including 10,112 with licensed searchable content and 204 with Egypt as a recorded geography. [Snapshot and hashes](data/publication_snapshot.json) |
| [Licensed publications](data/publication_passages.jsonl.gz) | 10,112 publisher abstracts and 2,278 body passages from 72 publications in the initial acquisition |
| [Book library](coverage/book_catalog.md) | Distinct titles with indexed prose, original PDF links and page counts. Includes the [textbook catalog](data/books.jsonl), [institutional catalog](data/institutional_books.jsonl), and their compressed [textbook](data/book_passages.jsonl.gz) and [institutional](data/institutional_book_passages.jsonl.gz) passage files. |
| [Annual observations](data/annual_observations.jsonl.gz) | 18,360 observations from 61 WDI series, across Egypt and 22 comparator economies. [Definitions and snapshot](data/indicator_snapshot.json) |
| [Evaluation questions](evaluation_questions.json) and [expanded questions](coverage/evaluation_v2.json) | 59 local retrieval checks in Arabic and English, covering all 24 sectors and 33 scenarios |

## Index the corpus

From the repository root, build the SQLite index using Python with FTS5. The examples are suitable for PowerShell.

```powershell
python -m rag.data_tools.rag_library build
python -m rag.data_tools.rag_library search 'أفتح فرع محل تجزئة في حي شعبي ولا لأ؟' --sector retail --scenario branch
python -m rag.data_tools.rag_library indicator FP.CPI.TOTL.ZG --country EGY --year 2024
python -m rag.data_tools.evaluate_rag_library --report data/knowledge_base/coverage/coverage_report.md
```

The index is built from the snapshots on disk. `search` accepts the sector and scenario IDs if the router or user has selected them. It applies bilingual lexical normalization and a local relevance preference, then returns at most one passage per publication. `indicator` queries annual observations by code, country and year and returns the historical reference with the number. If the year is omitted, it returns the last available year in the snapshot and includes that year in the response.

Source catalogs use UTF-8 JSONL. Each line describes one edition with a stable ID, publisher, date, geography, topics and reuse license. Each line of the compressed passage files holds extracted text with its source ID and a page locator when available. Arabic cards can also be exported to UTF-8 JSONL with `python -m rag.data_tools.build_rag_corpus --output tmp/esis-rag-cards.jsonl`.

SQLite links sources to sectors, geographies and founder scenarios. Some topical links come from the query that located the source, so the passage still needs to support the claim. The local chat combines full-text search of all passages with a multilingual embedding per source. It passes selected passages to Gemma or Gemini. An explicit book request retrieves body text from the book collection, with an Egypt filter when the question specifies Egypt.

Prioritize evidence matching the question and company context. For Egyptian queries, start with Egyptian results and then comparable evidence from MENA and emerging economies. Where local findings are absent, use the closest international study and name its country, sector, company size or intervention in the answer. Local statistics make a stronger case for local context. A comparable trial from another country may better answer a question about what happened after an intervention. Pair follow-ups with their original trial, such as Tunisia and Togo, so a single experiment is counted once.

### Give the router a sector and scenario

For a question about opening a new branch in a popular neighborhood, pair `retail` with `branch`. The sector profile tells the assistant to ask about visits, conversion, basket margin and stockouts. The scenario supplies the other inputs, such as rent and opening costs, and gives a useful result to ask the simulation engine for, such as break-even sales. Population and establishment counts describe the catchment once the location and reference dates have been checked. Actual footfall, rent and competitor activity are separate observations the business can collect.

## Turn evidence into a useful answer

For «لو جربت التصدير، هل ده ممكن يحسّن ربحي؟», the assistant could reply:

> في تجربة مصرية على منتجي سجاد، عرض فرصة التصدير من خلال وسيط ارتبط بارتفاع أرباح المجموعة اللي اتعرضت عليها الفرصة وتحسن الجودة. لو عايز تجرب في شركتك، ممكن تبدأ بطلبية صغيرة وتقيس هامش الربح بعد الشحن والمرتجعات، مع متابعة الجودة وزمن الإنتاج. الأثر المنشور يخص منتجي السجاد والتدخل اللي اتجرب عليهم. [Atkin et al., 2017](https://doi.org/10.1093/QJE/QJX002)

The answer should clearly identify what the research measured, give the founder a decision they can test, and state what data the system needs to estimate the outcome for their company. Cite the source and date next to the claim. Show an estimate for the company as a separate output with its inputs and assumptions.

### Use the evidence type when phrasing claims

- **Randomized trial (`rct`)**. Explain what was assigned and what outcome the researchers measured in that sample. An experimental estimate belongs to the evaluated intervention.
- **Quasi-experimental or observational study**. Describe the research design or association and its limits. A study of tool adoption can support testing a price change, while the company's response to that change still needs to be measured.
- **Survey or market report**. State the population, date and definition. For example, the share of sampled small firms reporting a bank loan is different from the approval rate for loan applications.
- **Official methodology or series**. For figures described as current, fetch and cite a dated observation. Record the metric and unit, the reference period and the publication or observation date. A dated government release can describe national inflation. A company-cost scenario needs the company's own cost assumptions.
- **Practical method**. Explain the calculation, inputs and possible failure modes. A cash-flow schedule supports a scenario about late invoices. A report on US cash buffers describes its sample and supplies no Egypt-wide cash target.

### Send only confirmed assumptions into simulation

If the founder asks about a 15% exchange-rate change, the RAG can explain which kinds of costs can be affected and ask what share of the company's costs is exposed. The simulation uses the share, the time period and the pass-through assumption the founder confirms. Published study effects, country averages, central-bank targets and forecasts remain reference evidence. They enter a scenario only when presented as an explicit assumption and confirmed for that scenario.

Keep the RAG index limited to public evidence. Uploaded financial statements and customer data are handled by the financial import and simulation paths. For example, fetching research on supplier terms only needs a general query about cash conversion, with no invoice values or names.

### Look up changing macroeconomic values

The corpus indexes where to look up a current value and how to interpret it. The WDI snapshot also provides historical observations. References and publication years help the assistant qualify coverage when a series ends several years before the question.

| Request | Lookup |
| --- | --- |
| EGP per USD for a given day | [CBE exchange rate history](https://www.cbe.org.eg/en/economic-research/statistics/exchange-rates/historical-data). Identify the rate type as well as the date. |
| Recent monthly inflation | [CBE inflation history](https://www.cbe.org.eg/en/economic-research/statistics/inflation-rates/historical-data). Identify headline or core and the comparison basis. |
| Latest policy decision | [CBE monetary policy](https://www.cbe.org.eg/en/monetary-policy/monetary-policy-framework). Follow the dated Monetary Policy Committee release. |
| Annual GDP growth, inflation, exchange rate or lending rates | [World Bank Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/898599-indicator-api-queries). Use indicator codes `NY.GDP.MKTP.KD.ZG`, `FP.CPI.TOTL.ZG`, `PA.NUS.FCRF` or `FR.INR.LEND` and keep the year. |

## Evaluate what the assistant retrieves

For each item in [the evaluation set](evaluation_questions.json), check whether the retrieved cards include the expected evidence. In the generated answer, check the findings and their limits, date or population, citation, and any assumptions. The arithmetic case deliberately separates results computed by the scenario engine from research evidence.

The build checks corpus hashes, document attribution, evidence licenses, unique IDs, evidence links and numeric records. The local evaluation measures whether an accepted source appeared in the top results. A production evaluation also needs grounded answer tests with Gemini, independent founder queries, citation checking, and checks for stale figures and unsupported causal claims.

## Refresh the source snapshots

Run a refresh into a staging directory, review the manifests and publication coverage, and then promote the validated input files. Keep the previous snapshot available while reviewing changes.

```powershell
python -m rag.data_tools.expand_rag_publications --output-dir tmp/esis-refresh --minimum 10100
python -m rag.data_tools.expand_rag_books --output-dir tmp/esis-books --selection data/knowledge_base/data/institutional_books.jsonl
python -m rag.data_tools.index_rag_books --pdf-dir tmp/source-book-pdfs --output-dir tmp/esis-textbooks
python -m rag.data_tools.refresh_rag_indicators --output-dir tmp/esis-refresh
```

The publication refresh searches by sector and decision, keeps source dates and deduplicates by handle, title and available DOI. It uses the repository's per-publication reuse metadata to admit CC BY excerpts from World Bank publications. The catalog keeps titles, bibliographic metadata and links for further reading. Preserve the attribution, rights holder, license, source link and excerpt note when serving licensed text. Check the source's stated third-party rights before reusing figures, tables or imagery. [World Bank repository reuse terms](https://www.worldbank.org/ext/en/legal/terms-conditions/open-knowledge-repository)

The run records its date, failures and content counts. Check for licensing changes, missing sectors, unexpected count changes and obsolete source dates before replacing the staged files. WDI may revise historical observations. Preserve the original indicator unit, recorded year and source organization when exposing a retrieved value, and consult series-specific source terms. [World Bank dataset terms](https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets)

Book extraction uses Poppler `pdftotext`. The institutional collector checks the catalog license, the publisher PDF, and the hash of an edition carried forward from a prior snapshot. The extraction retains searchable prose and skips front matter, reference pages, and many caption-only fragments. Each excerpt links to its PDF page. The numbers of available pages and indexed pages can differ with layouts and text extraction quality.

At runtime, treat text retrieved from publications as evidence to read. The router, rate limits, privacy decisions and simulation arithmetic are controlled by application code. RAG queries should use a general description of the company case when calling external services.
