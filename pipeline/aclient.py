"""Anthropic client + an IPv4-forcing helper.

Some hosts (e.g. Render's free tier) have no IPv6 outbound, but api.anthropic.com
publishes AAAA records — the HTTP client then tries IPv6 and fails with
"Connection error". force_ipv4_dns() makes DNS resolution return IPv4 only, which
fixes outbound to Anthropic (and is harmless for the Supabase pooler, which is IPv4).
Applied process-wide at server start; no third-party deps.
"""
from __future__ import annotations
import os
import socket


def force_ipv4_dns():
    _orig = socket.getaddrinfo
    if getattr(_orig, "_ipv4_forced", False):
        return
    def _ipv4(host, port, family=0, *args, **kwargs):
        return _orig(host, port, socket.AF_INET, *args, **kwargs)
    _ipv4._ipv4_forced = True
    socket.getaddrinfo = _ipv4


def make_anthropic(force_ipv4: bool = False):
    if force_ipv4 or os.environ.get("EPI_FORCE_IPV4", "1") == "1":
        force_ipv4_dns()
    import anthropic
    return anthropic.Anthropic()
