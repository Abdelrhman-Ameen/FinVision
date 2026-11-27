# دليل المؤسس

The localhost chatbot answers questions from the project's public knowledge base. Its FastAPI server retrieves evidence locally and can generate cited responses with Gemma via Ollama or with Gemini 3.5 Flash-Lite. Questions about supported annual indicators use exact dated observations from the local data snapshot.

## Run from the repository root

Use Python 3.12 on a machine with a recent x64 CPU. Run these PowerShell commands from the FinVision repository root.

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r rag/requirements.txt
New-Item -ItemType Directory -Path .local -Force | Out-Null
if (!(Test-Path '.local/rag.env')) { Copy-Item rag/rag.env.example .local/rag.env }
```

Select the generator in `.local/rag.env`. For Gemma, start Ollama with `OLLAMA_NO_CLOUD=1` and an installed `gemma3:4b` model. Set `RAG_PROVIDER=ollama`, `OLLAMA_MODEL=gemma3:4b`, and `OLLAMA_URL` to the Ollama loopback origin. For Gemini, set `RAG_PROVIDER=gemini` and fill in `GEMINI_API_KEY`. Environment variables take precedence over the file. Keep keys out of the shell command line and repository history.

```powershell
& '.venv/Scripts/python.exe' -m rag prepare
& '.venv/Scripts/python.exe' -m rag serve
```

Open [the local chat](http://127.0.0.1:8000/). Add `--port 8001` to the serve command if the default port is occupied. The first preparation downloads the pinned multilingual E5 tokenizer and ONNX model, about 129 MiB combined, and verifies both SHA-256 hashes. It builds SQLite and the NumPy vector index. Run `prepare` again after changing the knowledge base or its snapshots. Generated files and local configuration live under Git-ignored `.local/`.

## What happens to a question

1. The server handles standalone greetings, thanks, and other recognized conversational turns directly in Arabic or English. These replies skip retrieval and generation. A greeting with a business question continues to evidence lookup. Follow-ups can reuse the last substantive question.
2. It retrieves with SQLite FTS5 across the passage text and cosine similarity over the 384-dimensional source embeddings, using `intfloat/multilingual-e5-small` on the CPU. It combines the rankings and keeps one passage per publication, with a preference for matching country evidence. Book queries can search the page-level text directly.
3. The configured model receives a bounded set of evidence excerpts, the question and short question history, and returns structured text blocks with numbered sources. The server validates the structure and checks every citation against the evidence included in that request. It supplies the actual source links, dates and attribution.
4. For supported questions about a specific historical year, it looks up the annual observation and cites the original WDI series. For selected Egyptian figures, it can cite the dated Central Bank or Ministry of Planning release. It gives a coverage message and official reference for values outside the snapshots.

The local page keeps the current conversation in browser memory. In Gemma mode, generation requests go to the loopback Ollama server. In Gemini mode, questions and evidence excerpts go to Gemini, so use public or non-sensitive questions. The local index contains published evidence and annual data. [Embedding model card](https://huggingface.co/intfloat/multilingual-e5-small) · [Gemini structured output](https://aistudio.google.com/docs/structured-output)

## Tests

```powershell
uv pip install --python .venv/Scripts/python.exe -r rag/requirements-dev.txt
& '.venv/Scripts/python.exe' -m pytest rag/tests -q
& '.venv/Scripts/python.exe' -m rag.evaluate
```

The API tests use a mocked Gemini response and verify source links, citation validation, missing evidence, historical and unavailable current figures, follow-up context, and quota and input errors. The evaluation builds on the 59 author-written corpus questions and exercises the actual local ONNX and SQLite retrieval path. These checks measure retrieval and citation wiring. Evaluate factual grounding on fresh questions when preparing a public release.

For a live smoke test, ask whether an Egyptian study compared loans and grants for small businesses. Check that the answer opens the study in the sources panel. Then ask for Egyptian inflation in 2024 and for today's dollar exchange rate. The first returns a dated observation. The second directs you to the current official source.

## Local API

| Route | Purpose |
| --- | --- |
| `GET /` | Chat interface |
| `GET /api/status` | Index size, configured provider and model, and generator readiness |
| `POST /api/chat` | JSON with `question`, optional `previous_questions` and optional ISO-3 `country` |

The server binds to loopback. A public deployment needs its own authentication, quota controls, live-source policies and review of how user data is handled.
