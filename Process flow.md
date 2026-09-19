How Policy Lookup Works

1. The employee's question is sent to the Google Antigravity Agent.
2. The agent calls the MCP `search_policies` tool for policy questions.
3. Documents uploaded through the portal are split into Markdown section chunks.
4. Each chunk is embedded with Gemini and stored in the persistent ChromaDB collection.
5. The question is embedded with the same model and ChromaDB returns the five closest chunks.
6. The MCP tool returns matching content with document and section citations.
7. The agent uses those retrieved excerpts as RAG context and writes the final answer.

The complete indexed document can also be retrieved with the MCP
`get_policy_document` tool. Email requests use `send_policy_email`, which
archives every message in `sent_emails/` and sends through SMTP when configured.

The bundled policy files are source documents for import; they are not read
directly during search. Use the Import Documents panel to index new or updated
policies.
