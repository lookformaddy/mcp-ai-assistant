# HTTP transport examples

Start the server:

```bash
mcp-ai-assistant serve --transport http --port 8000
# Optional auth: MCP_AI_API_TOKEN=secret mcp-ai-assistant serve --transport http
```

Health and discovery:

```bash
curl -s localhost:8000/health
curl -s localhost:8000/tools | head -c 400
```

A `tools/call` over JSON-RPC:

```bash
curl -s localhost:8000/rpc -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": {"name": "metric_summary", "arguments": {"metric": "revenue", "period": "2026-Q3"}}
}'
```

Read a resource and a prompt template:

```bash
curl -s localhost:8000/rpc -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 2, "method": "resources/read",
  "params": {"uri": "enterprise://inventory/low-stock"}
}'

curl -s localhost:8000/rpc -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 3, "method": "prompts/get",
  "params": {"name": "weekly_ops_review", "arguments": {"region": "NA"}}
}'
```

Inspect the audit trail:

```bash
curl -s 'localhost:8000/audit?status=denied'
```
