# Data

| Folder | Used for | Contents |
| --- | --- | --- |
| [knowledge_base](knowledge_base/README.md) | Chatbot retrieval and citations | Evidence cards, publication and book snapshots, annual indicators, and retrieval checks |
| [poland](poland/README.md) | Bankruptcy model experiments | Five CSVs, one for each prediction horizon |
| [world-bank](world-bank/README.md) | Macroeconomic analysis | Annual World Development Indicators for Egypt and a regional and emerging-market comparison set |

Each folder documents its source and reuse terms. The chatbot builds SQLite and embedding indexes from the knowledge-base snapshots when you run `python -m rag prepare`.
