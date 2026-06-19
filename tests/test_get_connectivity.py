import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, vprint, main

KNOWN_STATES = {"Unknown", "None", "Portal", "Limited", "Full"}

async def test(session):
    state = await call(session, "get_connectivity")
    vprint(f"connectivity = {state!r}")
    assert state in KNOWN_STATES, f"unexpected state: {state!r}"

if __name__ == "__main__":
    main(test, "test_get_connectivity")
