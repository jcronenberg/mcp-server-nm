#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, vprint, main

async def test(session):
    new = await call(
        session,
        "add_connection",
        name="nm-mcp-test-conn-temp",
        conn_type="dummy",
        interface_name="nmt-dummy2",
        ipv4={"method": "manual", "addresses": ["10.99.2.1/24"], "gateway": None, "dns": []},
        ipv6={"method": "auto", "addresses": [], "gateway": None, "dns": []},
    )
    vprint(f"added: name={new['name']!r}, type={new['type']!r}, addresses={new['ipv4']['addresses']}")
    assert new["name"] == "nm-mcp-test-conn-temp", f"unexpected name: {new['name']!r}"
    assert new["type"] == "dummy", f"unexpected type: {new['type']!r}"
    assert "10.99.2.1/24" in new["ipv4"]["addresses"], f"address missing: {new['ipv4']['addresses']}"

    conns = await call(session, "get_connections")
    assert any(c["uuid"] == new["uuid"] for c in conns), "newly added profile not found in get_connections"
    vprint("confirmed present in get_connections")

if __name__ == "__main__":
    main(test, "test_add_connection")
