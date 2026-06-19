import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, vprint, main

async def test(session):
    original = await call(session, "get_hostname")
    vprint(f"original hostname: {original!r}")

    try:
        result = await call(session, "set_hostname", hostname="nm-mcp-test-host")
        vprint(f"set: {result['status']} — {result['message']}")
        assert result["status"] == "success", f"set_hostname failed: {result}"

        current = await call(session, "get_hostname")
        vprint(f"after set: {current!r}")
        assert current == "nm-mcp-test-host", f"hostname not updated: {current!r}"
    finally:
        restore = await call(session, "set_hostname", hostname=original)
        vprint(f"restore: {restore['status']} — {restore['message']}")
        assert restore["status"] == "success", f"restore hostname failed: {restore}"

        current = await call(session, "get_hostname")
        vprint(f"after restore: {current!r}")
        assert current == original, f"hostname not restored: {current!r}"

if __name__ == "__main__":
    main(test, "test_set_hostname")
