# Walkthrough: Acme Corp HR Support Agent (FastMCP + Antigravity SDK + Web UI)

We have built a full-stack, enterprise-grade Agentic HR Support system combining **FastMCP** (Model Context Protocol), the **Google Antigravity SDK**, **real SMTP email dispatch**, and a **modern Web Chat UI**.

---

## 1. Architecture Overview

```mermaid
flowchart LR
    subgraph Frontend [Employee Web UI]
      UI["Chat Portal\n(http://localhost:8000)"]
    end

    subgraph Backend [FastAPI Server (app.py)]
      API["FastAPI App\n(/api/chat, /api/status)"]
      Agent["Google Antigravity Agent\n(gemini-3.5-flash-lite)"]
    end

    subgraph MCP [FastMCP Server (hr_mcp_server.py)]
      Server["HR FastMCP Server\n(stdio transport)"]
      SearchTool["search_policies()"]
      DocTool["get_policy_document()"]
      EmailTool["send_policy_email()"]
    end

    subgraph Data [Storage & Services]
      Docs["policies/*.md\n(Leave, Remote, Health)"]
      SMTP["Real SMTP Provider\n(Gmail / Outlook / SendGrid)"]
      Archive["sent_emails/*.txt\n(Audit Trail)"]
    end

    UI <--> API
    API <--> Agent
    Agent <-- "stdio (JSON-RPC)" --> Server
    Server --> SearchTool & DocTool & EmailTool
    SearchTool & DocTool --> Docs
    EmailTool --> SMTP
    EmailTool --> Archive
```

---

## 2. Key Components Created

### 📄 1. HR Policy Knowledge Base (`policies/`)
- [`policies/leave_policy.md`](policies/leave_policy.md): PTO accrual, carry-over rules (max 5 days), 10 sick days, 16 weeks parental leave for primary caregivers, bereavement leave.
- [`policies/remote_work_policy.md`](policies/remote_work_policy.md): Hybrid 2-day model, 10 AM – 4 PM core hours, $500 one-time home office equipment stipend, $50/month internet reimbursement.
- [`policies/health_benefits.md`](policies/health_benefits.md): PPO vs. HDHP + HSA, dental cleanings, $250 vision stipend, 10 free therapy sessions through Modern Health.

### ⚙️ 2. FastMCP Server (`hr_mcp_server.py`)
Exposes tools over standard I/O (`stdio`):
- `search_policies(query: str)`: Performs ChromaDB semantic search over embedded document sections and returns cited excerpts.
- `get_policy_document(policy_name: str)`: Returns full markdown text for a specific policy.
- `list_available_policies()`: Lists all registered policies.
- `send_policy_email(recipient_email, subject, body)`:
  - **Live SMTP Mode**: Connects via TLS/SSL using credentials from `.env` to deliver actual emails.
  - **Audit Archive**: Saves copies in `sent_emails/` for inspection and UI preview.
  - **Dual-Mode Fallback**: Falls back to local archiving if SMTP is not configured, ensuring zero crashes.

### 🌐 3. FastAPI Backend (`app.py`)
- Bridges web client requests to the Google Antigravity Agent.
- Manages the lifecycle of the FastMCP server process.
- Endpoints:
  - `POST /api/chat`: Multi-turn conversational endpoint.
  - `GET /api/status`: System health, MCP connection, and SMTP status.
  - `GET /api/policies`: Policy catalog.
  - `GET /api/emails`: Dispatched email history.
  - `POST /api/reset`: Reset conversation history.

### 💻 4. Interactive Web Chat Portal (`static/index.html`)
- **Brand Header**: Live status badges for **MCP Server (🟢 Connected)** and **Email Dispatch (Live SMTP or Archive)**.
- **Chat Feed**: Markdown rendering via `marked.js`, avatars, and pulsing typing indicators.
- **Suggested Question Chips**: One-click questions for common queries.
- **Dispatched Emails Drawer**: Real-time list of sent emails with click-to-preview modals.

### 🖥️ 5. Terminal CLI (`hr_agent.py`)
- An alternative lightweight interface for interacting with the agent directly from PowerShell.
- **Not required** when using the Web UI (`app.py`). Both connect to the same `hr_mcp_server.py`.

---

## 2a. Post-Implementation Fixes

- **Model changed**: Switched from default `gemini-3.7-flash` to **`gemini-3.5-flash-lite`** (free-tier lower cost model).
- **Tool argument validation fix**: Smaller flash-lite models occasionally omit required tool arguments. System instructions were updated in both `app.py` and `hr_agent.py` to explicitly enforce that all three arguments (`recipient_email`, `subject`, `body`) are always supplied when calling `send_policy_email`.
- **`mcp` library pinned to `<2.0.0`**: `mcp 2.x` renamed `FastMCP` to `MCPServer`. Requirements are pinned to `mcp>=1.0.0,<2.0.0` to preserve the `from mcp.server.fastmcp import FastMCP` API.

---

## 3. Automated Verification Results

1. **FastMCP Server Verification** (`test_mcp_server.py`):
   - `list_available_policies`: ✅ Passed (found `health_benefits`, `leave_policy`, `remote_work_policy`)
   - `search_policies`: ✅ Passed (matched 16-week parental leave clause)
   - `get_policy_document`: ✅ Passed (loaded 2039 chars)
   - `send_policy_email`: ✅ Passed (composed, archived, and returned confirmation)

2. **FastAPI & Static UI Routes** (`test_app.py`):
   - `GET /api/status`: ✅ HTTP 200
   - `GET /api/policies`: ✅ HTTP 200 (3 policies registered)
   - `GET /api/emails`: ✅ HTTP 200 (dispatched emails retrieved)
   - `GET /`: ✅ HTTP 200 (served `static/index.html` - 20KB)

---

## 4. Running the Application

### Step 1: Set your Keys in [`.env`](.env)

Open [`.env`](.env) and verify:
```env
# Required for Gemini Agent
GEMINI_API_KEY=your_actual_gemini_api_key

# Optional: To send real emails to your personal inbox
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_16_char_gmail_app_password
SMTP_FROM_EMAIL=Acme HR Support <your_email@gmail.com>
```

*(If SMTP is left blank, the app runs in archive mode, saving emails to `sent_emails/` and displaying them in the UI).*

### Step 2: Start the Web UI Server

```powershell
.\.venv\Scripts\python.exe app.py
```

Open your browser and navigate to:
👉 **[http://localhost:8000](http://localhost:8000)**

### Step 3: Or Run via Terminal CLI (Alternative)

```powershell
.\.venv\Scripts\python.exe hr_agent.py
```
