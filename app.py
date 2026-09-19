"""
Acme Corp HR Portal - FastAPI Backend

Serves the interactive HR Support Web Chat UI and bridges requests
to the Google Antigravity Agent connected to the FastMCP server.

Policy documents are stored and searched via ChromaDB (vector_store.py).
The /api/import-documents endpoint accepts file uploads and ingests them
into the vector database. Direct filesystem policy search has been removed.
"""

import os
import re
import sys
import asyncio
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from google.antigravity import Agent, LocalAgentConfig, types
from vector_store import vector_store

# Load environment variables
load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()
SENT_EMAILS_DIR = BASE_DIR / "sent_emails"
STATIC_DIR = BASE_DIR / "static"
SERVER_SCRIPT = BASE_DIR / "hr_mcp_server.py"

SYSTEM_INSTRUCTIONS = """
You are an empathetic, knowledgeable, and professional HR Support Assistant for Acme Corp.

Your Capabilities:
- You have access to an external Model Context Protocol (MCP) server providing Acme Corp HR policy tools:
  * `search_policies`: Perform semantic search across all indexed HR policy documents to find relevant clauses.
  * `list_available_policies`: Check which policy documents are currently indexed in the vector database.
  * `send_policy_email`: Send an email summary of policy details directly to the employee.

Your Guidelines:
1. Always search first: Use `search_policies` to ground your answers in official Acme Corp policies.
2. Cite your sources: Always clearly mention the document name (e.g., leave_policy.md) and section header.
3. Proactive Email Offer: Whenever you provide policy information, proactively offer to email the summary to the employee's email address.
4. Action Execution: If the employee asks you to email them or provides/confirms an email address, call `send_policy_email` with an organized, professional subject and body. YOU MUST strictly provide all three arguments: `recipient_email`, `subject`, and `body`. Do not omit `recipient_email`.
5. Accuracy & Honesty: If a query is not covered in the policy documents, state clearly that it is not covered and recommend contacting hr@acmecorp.internal.
"""


def create_agent_config() -> LocalAgentConfig:
    """Create LocalAgentConfig with the FastMCP stdio server."""
    mcp_server = types.McpStdioServer(
        name="acme_hr_server",
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
    )

    return LocalAgentConfig(
        model="gemini-3.5-flash-lite",
        system_instructions=SYSTEM_INSTRUCTIONS,
        mcp_servers=[mcp_server],
    )


# ---------------------------------------------------------------------------
# Agent session manager
# ---------------------------------------------------------------------------

class AgentSessionManager:
    def __init__(self):
        self.agent: Agent | None = None
        self._cm = None

    async def start(self):
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key or "paste_your_gemini_api_key_here" in api_key:
            print("[Warning] GEMINI_API_KEY is not configured in .env!")
            return

        config = create_agent_config()
        self._cm = Agent(config=config)
        self.agent = await self._cm.__aenter__()
        print("[AgentSessionManager] Google Antigravity Agent and FastMCP Server initialized.")

    async def stop(self):
        if self._cm and self.agent:
            await self._cm.__aexit__(None, None, None)
            self.agent = None
            self._cm = None
            print("[AgentSessionManager] Agent session shut down cleanly.")

    async def restart(self):
        await self.stop()
        await self.start()


agent_manager = AgentSessionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await agent_manager.start()
    yield
    await agent_manager.stop()


app = FastAPI(title="Acme Corp HR Support Portal", lifespan=lifespan)

# Allow CORS for development ease
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Status & info endpoints
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    """Return system readiness, vector DB status, and SMTP configuration."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    has_api_key = bool(api_key and "paste_your_gemini_api_key_here" not in api_key)
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_configured = bool(smtp_host and smtp_user)

    try:
        stats = vector_store.collection_stats()
        vdb_ready = True
    except Exception:
        stats = {"total_chunks": 0, "document_count": 0, "documents": []}
        vdb_ready = False

    return {
        "api_key_configured": has_api_key,
        "mcp_server_active": agent_manager.agent is not None,
        "smtp_configured": smtp_configured,
        "smtp_host": smtp_host or None,
        "smtp_user": smtp_user or None,
        "vector_db_ready": vdb_ready,
        "ingested_document_count": stats.get("document_count", 0),
        "total_chunks": stats.get("total_chunks", 0),
        "policies_available": stats.get("document_count", 0),
    }


@app.get("/api/vectordb/stats")
async def get_vectordb_stats():
    """Return ChromaDB collection statistics and per-document chunk counts."""
    try:
        stats = vector_store.collection_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vector DB error: {e}")


@app.get("/api/policies")
async def get_policies():
    """List policy documents currently indexed in the vector database."""
    try:
        docs = vector_store.list_ingested_documents()
        return {"policies": docs}
    except Exception as e:
        return {"policies": [], "error": str(e)}


# ---------------------------------------------------------------------------
# Document import / delete endpoints
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".md", ".txt"}
MAX_FILE_SIZE_MB = 10


@app.post("/api/import-documents")
async def import_documents(files: list[UploadFile] = File(...)):
    """
    Accept one or more uploaded documents and ingest them into ChromaDB.

    Supported formats: .md, .txt
    Each file is read as UTF-8 text, chunked by markdown sections,
    embedded via Gemini, and stored in the vector database.
    Re-uploading a file with the same name replaces its previous chunks.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    results = []
    for upload in files:
        filename = upload.filename or "unknown.md"
        ext = Path(filename).suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            results.append({
                "filename": filename,
                "status": "skipped",
                "reason": f"Unsupported file type '{ext}'. Use .md or .txt",
            })
            continue

        try:
            raw_bytes = await upload.read()

            # Size guard
            size_mb = len(raw_bytes) / (1024 * 1024)
            if size_mb > MAX_FILE_SIZE_MB:
                results.append({
                    "filename": filename,
                    "status": "skipped",
                    "reason": f"File too large ({size_mb:.1f} MB). Max is {MAX_FILE_SIZE_MB} MB.",
                })
                continue

            content = raw_bytes.decode("utf-8", errors="replace")

            # Run ingest in a thread so we don't block the event loop
            result = await asyncio.get_event_loop().run_in_executor(
                None, vector_store.ingest_document, filename, content
            )
            results.append({
                "filename": filename,
                "status": result["status"],
                "doc_name": result.get("doc_name"),
                "chunks_added": result.get("chunks_added", 0),
            })

        except Exception as e:
            results.append({
                "filename": filename,
                "status": "error",
                "reason": str(e),
            })

    return {"results": results}


@app.delete("/api/documents/{doc_name}")
async def delete_document(doc_name: str):
    """Remove all vector DB chunks for a document (by slug name)."""
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, vector_store.delete_document, doc_name
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Email endpoints
# ---------------------------------------------------------------------------

@app.get("/api/emails")
async def get_emails():
    """List dispatched/archived emails."""
    emails = []
    if not SENT_EMAILS_DIR.exists():
        return {"emails": []}

    for file in sorted(SENT_EMAILS_DIR.glob("email_*.txt"), reverse=True):
        raw_text = file.read_text(encoding="utf-8")
        headers = {}
        body = raw_text

        match = re.search(
            r"TIMESTAMP:\s*(.*?)\nTO:\s*(.*?)\nFROM:\s*(.*?)\nSUBJECT:\s*(.*?)\n={20,}\n\n(.*)",
            raw_text,
            re.DOTALL,
        )
        if match:
            headers["timestamp"] = match.group(1).strip()
            headers["to"] = match.group(2).strip()
            headers["from"] = match.group(3).strip()
            headers["subject"] = match.group(4).strip()
            body = match.group(5).strip()
        else:
            headers["timestamp"] = datetime.fromtimestamp(file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            headers["to"] = "Unknown"
            headers["subject"] = file.name

        emails.append({
            "filename": file.name,
            "headers": headers,
            "body": body,
        })
    return {"emails": emails}


# ---------------------------------------------------------------------------
# Session & chat endpoints
# ---------------------------------------------------------------------------

@app.post("/api/reset")
async def reset_session():
    """Restart the agent session to clear conversation history."""
    await agent_manager.restart()
    return {"status": "ok", "message": "Conversation session reset."}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Send user query to the Antigravity Agent and return response."""
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or "paste_your_gemini_api_key_here" in api_key:
        return {
            "response": (
                "⚠️ **GEMINI_API_KEY is not configured!**\n\n"
                "Please open `.env` in your project folder and add your key from "
                "[Google AI Studio](https://aistudio.google.com/app/api-keys)."
            ),
            "status": "error",
        }

    if not agent_manager.agent:
        await agent_manager.start()
        if not agent_manager.agent:
            raise HTTPException(status_code=500, detail="Failed to initialize Antigravity Agent.")

    try:
        response = await agent_manager.agent.chat(user_msg)
        answer_text = await response.text()
        return {
            "response": answer_text,
            "status": "success",
        }
    except Exception as e:
        return {
            "response": f"An error occurred while consulting the HR Agent: {str(e)}",
            "status": "error",
        }


# ---------------------------------------------------------------------------
# Static file serving
# ---------------------------------------------------------------------------

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    print("\nStarting Acme Corp HR Support Portal at http://localhost:8000")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
