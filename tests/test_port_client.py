import httpx
import pytest

from app.port_client import PortClient, PortUnavailable

# Port streams an agent run as one SSE event per token. SSE strips a single
# space after "data:", so the sender pads each token — stripping whitespace on
# our side runs the words together.
SSE_RUN = b"""event: invocationIdentifier
data: inv-123

event: execution
data: Let me pull

event: execution
data:  relevant catalog data

event: execution
data:  in parallel.\\n\\nFinding: chaos injection is active.

event: done
data: {"rateLimitUsage":{}}

"""


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PORT_CLIENT_ID", "id")
    monkeypatch.setenv("PORT_CLIENT_SECRET", "secret")
    c = PortClient()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/access_token"):
            return httpx.Response(200, json={"accessToken": "tok", "expiresIn": 3600})
        return httpx.Response(200, content=SSE_RUN, headers={"content-type": "text/event-stream"})

    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_missing_credentials_raise_a_typed_error(monkeypatch):
    monkeypatch.delenv("PORT_CLIENT_ID", raising=False)
    monkeypatch.delenv("PORT_CLIENT_SECRET", raising=False)
    with pytest.raises(PortUnavailable):
        PortClient()


@pytest.mark.asyncio
async def test_token_spacing_survives_the_sse_stream(client):
    result = await client.invoke_agent("agent-1", "why?")
    assert "Let me pull relevant catalog data in parallel." in result["response"]
    assert "catalogdata" not in result["response"]


@pytest.mark.asyncio
async def test_escaped_newlines_are_restored(client):
    result = await client.invoke_agent("agent-1", "why?")
    assert "\n\nFinding:" in result["response"]
    assert "\\n" not in result["response"]


@pytest.mark.asyncio
async def test_reports_invocation_id_and_completion(client):
    result = await client.invoke_agent("agent-1", "why?")
    assert result["invocation_id"] == "inv-123"
    assert result["finished"] is True
    assert result["agent_id"] == "agent-1"


@pytest.mark.asyncio
async def test_http_failure_raises(monkeypatch):
    monkeypatch.setenv("PORT_CLIENT_ID", "id")
    monkeypatch.setenv("PORT_CLIENT_SECRET", "secret")
    c = PortClient()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/access_token"):
            return httpx.Response(200, json={"accessToken": "tok", "expiresIn": 3600})
        return httpx.Response(404, text="no such agent")

    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="invoke failed"):
        await c.invoke_agent("missing", "hi")
