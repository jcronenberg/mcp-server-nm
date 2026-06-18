#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, vprint, main

async def test(session):
    conns = await call(session, "get_connections")
    by_name = {c["name"]: c for c in conns}
    assert "nm-mcp-test-conn-static" in by_name, "static profile missing"
    assert "nm-mcp-test-conn-dhcp" in by_name, "dhcp profile missing"
    static = by_name["nm-mcp-test-conn-static"]
    vprint(f"static active={static['active']}, addresses={static['ipv4']['addresses']}")
    vprint(f"dhcp   active={by_name['nm-mcp-test-conn-dhcp']['active']}")
    assert static["active"] is True, "static profile should be active"
    assert "10.99.0.2/24" in static["ipv4"]["addresses"], (
        f"expected 10.99.0.2/24 in {static['ipv4']['addresses']}"
    )

if __name__ == "__main__":
    main(test, "test_get_connections")
