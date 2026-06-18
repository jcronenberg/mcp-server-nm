#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, vprint, main

async def test(session):
    # Use the static profile — DHCP on a dummy interface never gets a lease
    # so its activation immediately fails and NM drops it from ActiveConnections.
    conns = await call(session, "get_connections")
    static = next(c for c in conns if c["name"] == "nm-mcp-test-conn-static")
    uuid = static["uuid"]

    result = await call(session, "set_connection_state", connection_uuid=uuid, active=False)
    vprint(f"deactivate: {result['status']} — {result['message']}")
    assert result["status"] == "success", f"deactivate failed: {result}"

    conns = await call(session, "get_connections")
    static = next(c for c in conns if c["name"] == "nm-mcp-test-conn-static")
    vprint(f"active after deactivate: {static['active']}")
    assert static["active"] is False, "profile still active after deactivate"

    result = await call(session, "set_connection_state", connection_uuid=uuid, active=True)
    vprint(f"activate: {result['status']} — {result['message']}")
    assert result["status"] == "success", f"activate failed: {result}"

    conns = await call(session, "get_connections")
    static = next(c for c in conns if c["name"] == "nm-mcp-test-conn-static")
    vprint(f"active after activate: {static['active']}")
    assert static["active"] is True, "profile not active after activate"

if __name__ == "__main__":
    main(test, "test_set_connection_state")
