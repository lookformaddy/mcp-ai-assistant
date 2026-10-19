import io
import json

from mcp_ai_assistant.server import run_stdio


def _run(server, lines: list[str]) -> list[dict]:
    stdin = io.StringIO("\n".join(lines) + "\n")
    stdout = io.StringIO()
    run_stdio(server, stdin=stdin, stdout=stdout)
    return [json.loads(line) for line in stdout.getvalue().strip().splitlines() if line]


def test_stdio_round_trip(server):
    responses = _run(
        server,
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "inventory_check", "arguments": {"sku": "SKU-2002"}},
                }
            ),
        ],
    )
    # The notification produces no response line.
    assert len(responses) == 2
    assert responses[0]["id"] == 1
    assert responses[1]["result"]["structuredContent"]["inventory"][0]["product"] == (
        "Industrial Label Printer"
    )


def test_stdio_parse_error(server):
    responses = _run(server, ["{broken json"])
    assert len(responses) == 1
    assert responses[0]["error"]["code"] == -32700
