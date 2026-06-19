"""Shared harness for the NetworkManager MCP server test suite."""

import asyncio
import json
import os
import shlex
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters, stdio_client

VERBOSE = False
CURRENT_TEST = ""


def vprint(*args, **kwargs):
    if VERBOSE:
        print(f"  [{CURRENT_TEST}]", *args, **kwargs)


def run(cmd, *, check=True):
    result = subprocess.run(
        cmd if isinstance(cmd, list) else shlex.split(cmd),
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Command {result.args} failed (rc={result.returncode}): {msg}")
    return result


async def call(session, tool, **kwargs):
    result = await session.call_tool(tool, kwargs)
    if result.isError:
        text = result.content[0].text if result.content else "unknown error"
        raise RuntimeError(f"Tool {tool!r} error: {text}")
    if not result.content:
        return None
    if len(result.content) > 1:
        # List return: FastMCP emits one TextContent block per element
        parsed = []
        for block in result.content:
            try:
                parsed.append(json.loads(block.text))
            except (json.JSONDecodeError, AttributeError):
                parsed.append(block.text)
        return parsed
    text = result.content[0].text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, AttributeError):
        return text


def setup():
    teardown()
    run("modprobe dummy")
    run("ip link add nmt-dummy0 type dummy")
    run("ip link add nmt-dummy1 type dummy")
    run(
        "nmcli con add type dummy ifname nmt-dummy0 "
        "con-name nm-mcp-test-conn-static ip4 10.99.0.2/24"
    )
    run("nmcli con up nm-mcp-test-conn-static")
    run(
        "nmcli con add type dummy ifname nmt-dummy1 "
        "con-name nm-mcp-test-conn-dhcp"
    )


def teardown():
    result = run("nmcli -t -f NAME con show", check=False)
    for name in result.stdout.splitlines():
        if name.startswith("nm-mcp-test-"):
            run(f"nmcli con delete '{name}'", check=False)

    result = run("ip -o link show", check=False)
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 2:
            iface = parts[1].strip()
            if iface.startswith("nmt-"):
                run(f"ip link delete {iface}", check=False)


async def _run(test_fn, verbose):
    global VERBOSE
    VERBOSE = verbose

    cmd = shlex.split(os.environ.get("NM_MCP_TEST_SERVER", "uv run main.py"))
    params = StdioServerParameters(command=cmd[0], args=cmd[1:])

    try:
        setup()
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await test_fn(session)
    finally:
        teardown()


def run_test(test_fn, name, verbose=False):
    """Run one test with full setup/teardown. Returns (passed: bool, error: str|None)."""
    global CURRENT_TEST
    CURRENT_TEST = name
    try:
        asyncio.run(_run(test_fn, verbose))
        return True, None
    except Exception as e:
        return False, str(e)


async def assert_tool_error(session, tool, **kwargs):
    """Assert that a tool call returns an MCP error. Raises AssertionError if it succeeds."""
    try:
        result = await call(session, tool, **kwargs)
        raise AssertionError(f"Tool {tool!r} should have returned an error but succeeded: {result}")
    except RuntimeError:
        pass  # expected — call() raises RuntimeError on isError responses


def main(test_fn, name):
    """Standalone entry point for individual test files."""
    verbose = "-v" in sys.argv
    passed, error = run_test(test_fn, name, verbose=verbose)
    if passed:
        print(f"[ OK ] {name}")
    else:
        print(f"[FAIL] {name}")
        print(f"   {error}")
    sys.exit(0 if passed else 1)
