"""Keep both panel deployments static and independent of the API service."""

import json
from pathlib import Path

import yaml

from token_abuse_engine.config import load_config


def test_vercel_deploys_only_static_panel():
    config = json.loads(Path("vercel.json").read_text())
    assert config["framework"] is None
    assert config["buildCommand"] == ""
    assert config["installCommand"] == ""
    assert config["outputDirectory"] == "pages"
    assert "rewrites" not in config  # Do not pretend Pages/Vercel hosts the API.
    output = Path(config["outputDirectory"])
    for asset in ("index.html", "app.mjs", "crypto.mjs", "style.css"):
        assert (output / asset).is_file()
    assert not list(output.rglob("*.py"))


def test_pages_publishes_same_directory_from_main():
    workflow = yaml.safe_load(Path(".github/workflows/pages.yml").read_text())
    deploy = workflow["jobs"]["deploy"]
    assert deploy["if"] == "github.ref == 'refs/heads/main'"
    upload = next(step for step in deploy["steps"]
                  if step.get("uses", "").startswith("actions/upload-pages-artifact@"))
    assert upload["with"]["path"] == "pages"


def test_production_allows_exact_vercel_origin(monkeypatch):
    # Domain is illustrative, not an assertion that a Vercel deployment exists.
    import secrets

    origin = "https://panel.example.vercel.app"
    monkeypatch.setenv("PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("ADMIN_API_KEY", secrets.token_urlsafe(32))
    monkeypatch.setenv("GATEWAY_API_KEY", secrets.token_urlsafe(32))
    config = load_config("config.production.yaml")
    assert origin in config.gateway.cors_origins
    assert "https://b3b3097.github.io" in config.gateway.cors_origins
    assert "*" not in config.gateway.cors_origins
