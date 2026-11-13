"""Static guard on docker-compose.yml: the resolver's and submit's isolation
(AC7). The live checks are in the plan's verification section."""

import yaml

from gttp.config import ROOT

COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]


def _env(svc):
    env = svc.get("environment") or []
    return env if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]


def _networks(svc):
    n = svc.get("networks") or {}
    return set(n) if isinstance(n, (dict, list)) else set()


def test_resolver_is_locked_down():
    r = SERVICES["resolver"]
    for key in ("ports", "volumes", "env_file", "expose", "tmpfs"):
        assert key not in r, key
    assert r["read_only"] is True
    assert r["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in r["security_opt"]
    assert r["user"] == "1000:1000"
    assert r["entrypoint"] == ["python", "-m", "gttp.resolver"]
    assert _networks(r) == {"resolver-internal", "resolver-egress"}
    for e in _env(r):
        name = e.split("=")[0].upper()
        assert not any(w in name for w in ("ANTHROPIC", "KEY", "TOKEN", "SECRET")), e


def test_submit_is_unchanged_apart_from_networks():
    s = SERVICES["submit"]
    assert s["volumes"] == ["./.cache/queue:/app/.cache/queue"]
    assert all(str(p).startswith("127.0.0.1:") for p in s["ports"])
    assert s["read_only"] is True and s["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in s["security_opt"]
    assert "env_file" not in s
    assert not any("ANTHROPIC" in e for e in _env(s))
    assert _networks(s) == {"default", "resolver-internal"}


def test_networks():
    assert COMPOSE["networks"]["resolver-internal"]["internal"] is True
    assert "networks" not in SERVICES["app"]
