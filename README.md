# Multimodal Agent RAG

An open-source multimodal retrieval-augmented generation system for building private knowledge assistants.

## What It Does

Multimodal Agent RAG combines document parsing, image understanding, hybrid retrieval and an agent workflow in one local-first application.

- Parse PDF, Word, Markdown, text, spreadsheet and image files.
- Preserve document structure with heading-aware and semantic chunking.
- Index text and images for mixed text-image questions.
- Search with vector retrieval, BM25 full-text retrieval or weighted hybrid retrieval.
- Apply optional reranking, result deduplication and neighboring-context expansion.
- Run an Agent workflow that can call retrieval tools, inspect results and retry weak queries.
- Review recall scores, latency and retrieval history from the web UI.
- Use local Ollama models or any compatible remote model endpoint.

## Architecture

```text
Frontend (React + TypeScript + Vite)
        |
        v
FastAPI API -> Parser -> SQLite metadata
        |                 |
        |                 +-> Chroma vector store
        |                 +-> BM25 index
        |
        +-> Retrieval service -> optional reranker -> Agent workflow -> LLM/VLM
```

The backend keeps runtime data under `local.db` and `storage/`. These paths are ignored by Git and are intentionally absent from the repository.

## Quick Start

### Requirements

- Python 3.10+
- Node.js 18+
- Redis (optional, for background task workers)
- Ollama or another OpenAI-compatible model service

### Backend

```bash
python -m venv .venv

# Linux/macOS
source .venv/bin/activate

# Windows PowerShell
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
copy backend\.env.example backend\.env  # Windows
# cp backend/.env.example backend/.env    # Linux/macOS
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Edit `backend/.env` with local model endpoints and credentials. Never commit that file.

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000` after both services start.

## Retrieval Modes

The normal chat and recall test pages expose three modes:

- `vector`: semantic similarity through the configured embedding model.
- `fulltext`: keyword matching through the BM25 index.
- `hybrid`: weighted fusion of vector and BM25 scores.

Reranking can be enabled for a second-stage relevance pass. The retrieval service also records latency, result counts and fallback usage so that recall quality can be reviewed over time.

## Agent Workflow

The Agent workflow uses LangGraph to:

1. Classify chat versus knowledge-base questions.
2. Call the retrieval tool with the selected knowledge bases and search settings.
3. Parse observations and citations from tool output.
4. Score document relevance and retry query formulation when the score is low.
5. Produce a grounded answer with source references.

## Repository Layout

```text
backend/app/       FastAPI application, parsing, retrieval and Agent services
frontend/src/      React application and retrieval controls
requirements.txt   Python dependencies
backend/.env.example
                     Safe configuration template with no credentials
```

Runtime documents, vector indexes, databases, logs, caches, credentials and local model files are not part of the public source tree.

## Contributing

Issues and pull requests are welcome. Please keep credentials, private documents and generated runtime data outside commits, and run the relevant backend/frontend checks before opening a pull request.

## License

MIT License. See [LICENSE](LICENSE).
