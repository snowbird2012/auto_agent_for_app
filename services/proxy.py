"""Shared proxy resolution for model and internal HTTP requests."""

from __future__ import annotations

from urllib.parse import quote, urlsplit, urlunsplit


def build_proxy_map(settings: dict | None, target_url: str, scope: str) -> dict[str, str] | None:
    """Return a requests-compatible proxy map, or None when proxying is disabled."""
    if not settings or not settings.get("enabled"):
        return None
    scope_key = "use_for_model" if scope == "model" else "use_for_internal"
    if not settings.get(scope_key, False) or _is_bypassed(target_url, settings.get("bypass_hosts", "")):
        return None
    proxy_url = str(settings.get("proxy_url", "")).strip()
    if not proxy_url:
        return None
    if "://" not in proxy_url:
        proxy_url = "http://" + proxy_url
    username = str(settings.get("username", "")).strip()
    password = str(settings.get("password", ""))
    if username:
        parsed = urlsplit(proxy_url)
        host = parsed.hostname or ""
        if parsed.port:
            host += f":{parsed.port}"
        credentials = quote(username, safe="")
        if password:
            credentials += ":" + quote(password, safe="")
        proxy_url = urlunsplit((parsed.scheme, f"{credentials}@{host}", parsed.path, parsed.query, parsed.fragment))
    return {"http": proxy_url, "https": proxy_url}


def request_verify_ssl(settings: dict | None, scope: str) -> bool:
    if not settings or not settings.get("enabled"):
        return True
    scope_key = "use_for_model" if scope == "model" else "use_for_internal"
    return bool(settings.get("verify_ssl", True)) if settings.get(scope_key, False) else True


def _is_bypassed(target_url: str, bypass_hosts: str) -> bool:
    hostname = (urlsplit(target_url).hostname or "").lower()
    if not hostname:
        return False
    for raw_pattern in bypass_hosts.replace(";", ",").split(","):
        pattern = raw_pattern.strip().lower()
        if not pattern:
            continue
        if pattern == "<local>" and "." not in hostname:
            return True
        normalized = pattern.lstrip("*.")
        if hostname == normalized or hostname.endswith("." + normalized):
            return True
    return False
