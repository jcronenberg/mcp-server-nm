import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, vprint, main

async def test(session):
    # Create a fresh connection to modify
    new = await call(
        session,
        "add_connection",
        name="nm-mcp-test-conn-temp",
        conn_type="dummy",
        interface_name="nmt-dummy2",
        ipv4={"method": "manual", "addresses": ["10.99.2.1/24"], "gateway": None, "dns": []},
        ipv6={"method": "auto", "addresses": [], "gateway": None, "dns": []},
    )
    uuid = new["uuid"]
    vprint(f"created: name={new['name']!r}, ipv4={new['ipv4']}, ipv6={new['ipv6']}")

    result = await call(
        session,
        "modify_connection",
        uuid=uuid,
        name="nm-mcp-test-conn-modified",
        ipv4={"method": "manual", "addresses": ["10.99.2.2/24"], "gateway": "10.99.2.1", "dns": ["1.1.1.1"]},
        ipv6={"method": "manual", "addresses": ["fd00::2/64"], "gateway": None, "dns": []},
    )
    vprint(f"modify: {result['status']} — {result['message']}")
    assert result["status"] == "success", f"modify failed: {result}"

    conns = await call(session, "get_connections")
    modified = next(c for c in conns if c["uuid"] == uuid)
    vprint(f"after: name={modified['name']!r}, ipv4={modified['ipv4']}, ipv6={modified['ipv6']}")
    assert modified["name"] == "nm-mcp-test-conn-modified", f"name not updated: {modified['name']!r}"
    assert "10.99.2.2/24" in modified["ipv4"]["addresses"], f"ipv4 address not updated: {modified['ipv4']['addresses']}"
    assert modified["ipv4"]["gateway"] == "10.99.2.1", f"gateway not updated: {modified['ipv4']['gateway']}"
    assert "1.1.1.1" in modified["ipv4"]["dns"], f"dns not updated: {modified['ipv4']['dns']}"
    assert "fd00::2/64" in modified["ipv6"]["addresses"], f"ipv6 address not updated: {modified['ipv6']['addresses']}"

if __name__ == "__main__":
    main(test, "test_modify_connection")
