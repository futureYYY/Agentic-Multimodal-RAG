# Multimodal Agent RAG (Legacy Public Snapshot)

This branch preserves the first public application snapshot without local runtime data, credentials or deployment-specific files. It provides the original multimodal document parsing, knowledge-base management, vector retrieval, reranking and LangGraph chat workflow.

## Run Locally

Requirements: Python 3.10+, Node.js 18+, and an Ollama or OpenAI-compatible model endpoint.

```bash
python -m venv .venv
pip install -r requirements.txt
copy backend\.env.example backend\.env  # Windows
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Configure model endpoints and credentials only in the ignored `backend/.env` file. This branch intentionally contains no database, uploaded document, vector index, or model key.

## License

MIT License. See [LICENSE](LICENSE).
