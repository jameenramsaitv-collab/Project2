"""
HR Support FastMCP Server

Exposes HR policy search and real/simulated email dispatch tools via the
Model Context Protocol (MCP) using standard I/O (stdio).

Policy retrieval is now backed by ChromaDB semantic vector search
(via vector_store.py) instead of direct filesystem keyword scanning.
"""

import os
import re
import sys
import smtplib
from email.message import EmailMessage
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from vector_store import vector_store

# Load .env variables
load_dotenv()

# Initialize FastMCP Server
mcp = FastMCP("HRSupportServer")

BASE_DIR = Path(__file__).parent.resolve()
SENT_EMAILS_DIR = BASE_DIR / "sent_emails"


# ---------------------------------------------------------------------------
# Policy tools — backed by ChromaDB vector store
# ---------------------------------------------------------------------------

@mcp.tool()
def list_available_policies() -> list[str]:
    """
    List all HR policy topics currently indexed in the vector database.
    Returns a list of policy document name slugs (e.g. 'leave_policy').
    """
    docs = vector_store.list_ingested_documents()
    return [d["doc_name"] for d in docs]


@mcp.tool()
def search_policies(query: str) -> str:
    """
    Perform a semantic search across all HR policy documents in the vector
    database to find sections most relevant to a specific employee query.
    Returns the top matching policy chunks with document and section citations.

    Args:
        query: The question or topic to search for
               (e.g. 'maternity leave', 'gym reimbursement', 'remote work equipment')
    """
    if not query or not query.strip():
        return "Search query was empty. Please provide a question or topic."

    hits = vector_store.semantic_search(query.strip(), n_results=5)

    if not hits:
        return (
            f"No policy content found for '{query}'. "
            "The vector database may be empty — please import policy documents "
            "using the Import Documents panel in the HR portal."
        )

    results = []
    for hit in hits:
        results.append(
            f"--- Citation: [{hit['filename']}] Section: {hit['header']} ---\n"
            f"{hit['content']}\n"
        )

    return "\n".join(results)


@mcp.tool()
def get_policy_document(policy_name: str) -> str:
    """Return the complete indexed policy document in its original section order."""
    name = policy_name.strip().lower()
    if not name:
        return "Policy name was empty. Please provide a policy name."

    if name.endswith(".md") or name.endswith(".txt"):
        name = Path(name).stem
    name = re.sub(r"[ -]+", "_", name)

    chunks = vector_store.get_document(name)
    if not chunks:
        return f"No indexed policy document found for '{policy_name}'."

    return "\n\n".join(chunk["content"] for chunk in chunks)


# ---------------------------------------------------------------------------
# Email tool — unchanged
# ---------------------------------------------------------------------------

@mcp.tool()
def send_policy_email(recipient_email: str, subject: str, body: str) -> str:
    """
    Send an email containing policy information or summaries to an employee.
    If SMTP credentials (SMTP_HOST, SMTP_USER, SMTP_PASSWORD) are set in .env,
    an actual email is delivered to the recipient.
    In all cases, a copy is archived to sent_emails/ for audit and web preview.

    Args:
        recipient_email: The destination email address of the employee (e.g. 'john.doe@company.com')
        subject: The subject line for the email
        body: The formatted content/body of the email (supports markdown/plain text)
    """
    recipient_clean = recipient_email.strip()
    email_regex = r"^[\w\.-]+@[\w\.-]+\.\w+$"
    if not re.match(email_regex, recipient_clean):
        return f"Error: '{recipient_email}' is not a valid email address."

    # 1. Archive email locally
    SENT_EMAILS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_recipient = re.sub(r"[^\w\.-]", "_", recipient_clean)
    filename = f"email_{timestamp_str}_{safe_recipient}.txt"
    filepath = SENT_EMAILS_DIR / filename

    from_address = os.getenv("SMTP_FROM_EMAIL") or os.getenv("SMTP_USER") or "hr-support@acmecorp.internal"

    archive_content = (
        f"====================================================\n"
        f"TIMESTAMP: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"TO:        {recipient_clean}\n"
        f"FROM:      {from_address}\n"
        f"SUBJECT:   {subject}\n"
        f"====================================================\n\n"
        f"{body}\n\n"
        f"--\n"
        f"Acme Corp HR Support Agent\n"
        f"Internal Portal: https://hr.acmecorp.internal\n"
    )
    filepath.write_text(archive_content, encoding="utf-8")

    # 2. Check for real SMTP configuration
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port_str = os.getenv("SMTP_PORT", "587").strip()
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()

    if smtp_host and smtp_user and smtp_password:
        try:
            port = int(smtp_port_str)
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = from_address
            msg["To"] = recipient_clean
            msg.set_content(
                f"{body}\n\n--\nAcme Corp HR Support Agent\nInternal Portal: https://hr.acmecorp.internal"
            )

            # Connect via SSL or STARTTLS
            if port == 465:
                with smtplib.SMTP_SSL(smtp_host, port, timeout=15) as server:
                    server.login(smtp_user, smtp_password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(smtp_host, port, timeout=15) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_password)
                    server.send_message(msg)

            sys.stderr.write(f"\n[MCP Server] Real email dispatched via SMTP to {recipient_clean}.\n")
            sys.stderr.flush()

            return (
                f"Real email successfully dispatched to '{recipient_clean}' via SMTP ({smtp_host})! "
                f"Subject: '{subject}'. "
                f"(Archived in sent_emails/{filename})"
            )

        except Exception as e:
            sys.stderr.write(f"\n[MCP Server SMTP Error]: {e}\n")
            sys.stderr.flush()
            return (
                f"Email saved locally to sent_emails/{filename} for '{recipient_clean}', "
                f"but live SMTP delivery encountered an error: {e}. "
                f"Please verify your SMTP credentials in .env."
            )

    # If SMTP is not configured, inform the user cleanly
    sys.stderr.write(f"\n[MCP Server] Email archived locally to {filename} (SMTP not configured in .env)\n")
    sys.stderr.flush()
    return (
        f"Email successfully composed and archived to sent_emails/{filename} for '{recipient_clean}'. "
        f"Subject: '{subject}'. "
        f"(Note: To deliver real emails to inboxes, specify SMTP_HOST, SMTP_USER, and SMTP_PASSWORD in .env)"
    )


# ---------------------------------------------------------------------------
# MCP Resource
# ---------------------------------------------------------------------------

@mcp.resource("policy://{policy_name}")
def get_policy_resource(policy_name: str) -> str:
    """Read a policy document via MCP resource — returns top search hits."""
    hits = vector_store.semantic_search(policy_name, n_results=10)
    if not hits:
        return f"No content found for policy '{policy_name}' in the vector database."
    return "\n\n".join(
        f"[{h['filename']}] {h['header']}\n{h['content']}" for h in hits
    )


if __name__ == "__main__":
    mcp.run()
