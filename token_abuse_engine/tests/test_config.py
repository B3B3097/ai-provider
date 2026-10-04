# language: Python, file: token_abuse_engine/tests/test_config.py, runtime: Python 3.10+

from token_abuse_engine.config import AuthType, load_config


def test_load_config_substitutes_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_URL", "https://provider.test")
    path = tmp_path / "config.yaml"
    path.write_text(
        """
storage:
  type: memory
gateway:
  api_keys: [client]
  admin_keys: [admin]
providers:
  - name: local
    base_url: ${TEST_PROVIDER_URL}
    auth:
      type: bearer
    models:
      - id: local-model
        provider_model: local-model
""".strip()
        + "\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.providers[0].base_url == "https://provider.test"
    assert config.providers[0].auth.type == AuthType.BEARER
    assert config.storage.type == "memory"