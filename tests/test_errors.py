import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, vprint, assert_tool_error, main

FAKE_UUID = "00000000-0000-0000-0000-000000000000"


async def test(session):
    # delete_connection: non-existent UUID
    vprint(f"delete_connection with fake UUID {FAKE_UUID}")
    await assert_tool_error(session, "delete_connection", uuid=FAKE_UUID)
    vprint("correctly returned an error")

    # modify_connection: non-existent UUID
    vprint(f"modify_connection with fake UUID {FAKE_UUID}")
    await assert_tool_error(
        session,
        "modify_connection",
        uuid=FAKE_UUID,
        ipv4={"method": "manual", "addresses": ["10.0.0.1/24"], "gateway": None, "dns": []},
    )
    vprint("correctly returned an error")

    # set_connection_state (activate): non-existent UUID
    # Deactivate with a missing UUID is a silent no-op in NM, so only activate is tested.
    vprint(f"set_connection_state activate with fake UUID {FAKE_UUID}")
    await assert_tool_error(
        session, "set_connection_state", connection_uuid=FAKE_UUID, active=True
    )
    vprint("correctly returned an error")

    # add_connection: interface name exceeding the 15-character kernel limit
    long_iface = "nmt-this-name-is-too-long"
    vprint(f"add_connection with interface_name={long_iface!r} ({len(long_iface)} chars)")
    await assert_tool_error(
        session,
        "add_connection",
        name="nm-mcp-test-conn-bad",
        conn_type="dummy",
        interface_name=long_iface,
        ipv4={"method": "auto", "addresses": [], "gateway": None, "dns": []},
        ipv6={"method": "auto", "addresses": [], "gateway": None, "dns": []},
    )
    vprint("correctly returned an error")


if __name__ == "__main__":
    main(test, "test_errors")
