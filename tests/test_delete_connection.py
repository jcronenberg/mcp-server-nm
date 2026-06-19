import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, vprint, main

async def test(session):
    # Create a connection to delete
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
    vprint(f"created: uuid={uuid}")

    result = await call(session, "delete_connection", uuid=uuid)
    vprint(f"delete: {result['status']} — {result['message']}")
    assert result["status"] == "success", f"delete failed: {result}"

    conns = await call(session, "get_connections")
    assert not any(c["uuid"] == uuid for c in conns), "profile still present after delete"
    vprint("confirmed gone from get_connections")

if __name__ == "__main__":
    main(test, "test_delete_connection")
