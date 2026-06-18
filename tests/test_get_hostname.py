#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, vprint, run, main

async def test(session):
    mcp_host = await call(session, "get_hostname")
    nm_host = run("nmcli general hostname").stdout.strip()
    vprint(f"MCP={mcp_host!r}, nmcli={nm_host!r}")
    assert mcp_host == nm_host, f"hostname mismatch: MCP={mcp_host!r}, nmcli={nm_host!r}"

if __name__ == "__main__":
    main(test, "test_get_hostname")
