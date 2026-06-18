#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, vprint, main

async def test(session):
    devices = await call(session, "get_devices")
    ifaces = {d["interface"] for d in devices}
    vprint(f"interfaces = {sorted(ifaces)}")
    assert "nmt-dummy0" in ifaces, f"nmt-dummy0 not in {ifaces}"
    assert "nmt-dummy1" in ifaces, f"nmt-dummy1 not in {ifaces}"

if __name__ == "__main__":
    main(test, "test_get_devices")
