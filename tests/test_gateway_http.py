"""Exercise the real gateway/pool/adapter against a local HTTP provider fixture."""

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from token_abuse_engine.config import (
    EngineConfig,
    GatewayConfig,
    HealthCheckConfig,
    ModelConfig,
    ProviderConfig,
    PublicApiConfig,
    StorageConfig,
)
from token_abuse_engine.gateway import create_app


@pytest.mark.parametrize("stream", [False, True])
def test_completion_over_real_upstream_http(stream):
    admin_key = secrets.token_urlsafe(32)
    upstream_key = secrets.token_urlsafe(32)
    requests = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log request credentials.

        def do_POST(self):
            authenticated = secrets.compare_digest(
                self.headers.get("Authorization", ""), f"Bearer {upstream_key}"
            )
            if not authenticated or self.path != "/v1/chat/completions":
                self.send_error(401)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
            if body.get("stream"):
                chunks = [
                    {"choices": [{"delta": {"content": "pong"}}]},
                    {"choices": [], "usage": usage},
                ]
                content = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
                content += "data: [DONE]\n\n"
                content_type = "text/event-stream"
            else:
                content = json.dumps({
                    "id": "chatcmpl-test", "object": "chat.completion",
                    "model": body["model"],
                    "choices": [{"message": {"role": "assistant", "content": "pong"}}],
                    "usage": usage,
                })
                content_type = "application/json"
            encoded = content.encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = EngineConfig(
        environment="development",
        gateway=GatewayConfig(
            admin_keys=[admin_key], public_api=PublicApiConfig(enabled=True),
        ),
        storage=StorageConfig(type="memory"),
        providers=[ProviderConfig(
            name="openai", base_url=f"http://127.0.0.1:{server.server_port}",
            health_check=HealthCheckConfig(enabled=False), model_discovery=False,
            models=[ModelConfig(id="test-model", provider_model="upstream-model")],
        )],
    )
    admin_auth = {"Authorization": f"Bearer {admin_key}"}
    try:
        with TestClient(create_app(config)) as client:
            issued = client.post("/admin/api-keys", headers=admin_auth,
                                 json={"name": "http-smoke"})
            assert issued.status_code == 201
            client_auth = {"Authorization": f"Bearer {issued.json()['api_key']}"}
            payload = {"model": "test-model", "stream": stream,
                       "messages": [{"role": "user", "content": "ping"}]}
            # A configured model is not evidence that a provider credential exists.
            if not stream:
                assert client.post("/v1/chat/completions", headers=client_auth,
                                   json=payload).status_code == 503
            imported = client.post("/admin/tokens", headers=admin_auth,
                                   json={"provider": "openai", "value": upstream_key})
            assert imported.status_code == 201
            assert upstream_key not in imported.text
            result = client.post("/v1/chat/completions", headers=client_auth, json=payload)
            assert result.status_code == 200
            if stream:
                assert result.headers["content-type"].startswith("text/event-stream")
                assert '"content": "pong"' in result.text
                assert result.text.count("data: [DONE]") == 1
                assert requests[0]["stream_options"]["include_usage"] is True
            else:
                assert result.json()["choices"][0]["message"]["content"] == "pong"
            assert requests[0]["model"] == "upstream-model"
            assert requests[0]["messages"] == payload["messages"]
            usage = client.get("/v1/usage", headers=client_auth)
            assert usage.json()["usage"]["tokens_total"] == 5
            assert client.delete(f"/admin/api-keys/{issued.json()['id']}",
                                 headers=admin_auth).status_code == 204
            assert client.get("/v1/models", headers=client_auth).status_code == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
