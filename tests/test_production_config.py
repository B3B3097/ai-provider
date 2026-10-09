import secrets

import pytest
import yaml

from token_abuse_engine.config import load_config


@pytest.mark.parametrize("case", ["missing_admin", "missing_client", "shared", "unresolved"])
def test_production_rejects_unsafe_auth_config(tmp_path, case):
    admin = secrets.token_urlsafe(32)
    client = secrets.token_urlsafe(32)
    config = {"environment": "production", "gateway": {
        "admin_keys": [admin], "api_keys": [client], "public_api": {"enabled": False},
    }}
    if case == "missing_admin":
        config["gateway"]["admin_keys"] = [" "]
    elif case == "missing_client":
        config["gateway"]["api_keys"] = []
    elif case == "shared":
        config["gateway"]["api_keys"] = [admin]
    else:
        config["gateway"]["admin_keys"] = ["${UNRESOLVED_GATEWAY_TEST_KEY}"]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError) as exc:
        load_config(path)
    assert admin not in str(exc.value)
    assert client not in str(exc.value)


def test_production_allows_operator_issued_client_keys(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"environment": "production", "gateway": {
        "admin_keys": [secrets.token_urlsafe(32)],
        "api_keys": [], "public_api": {"enabled": True},
    }}))
    assert load_config(path).gateway.public_api.enabled
