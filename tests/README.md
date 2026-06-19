# Testing

## Requirements

- Root privileges
- NetworkManager running (`nmcli general status` must succeed)
- The `dummy` kernel module available (`modprobe dummy`)
- Python 3.10+ with the `mcp` package importable (system package or virtualenv)

## Running

```
make test
```

To use a custom server binary, set `NM_MCP_TEST_SERVER` (default: `uv run main.py`):

```
NM_MCP_TEST_SERVER="python3 main.py" make test
```

Individual test files can also be run directly:

```
python3 tests/test_get_connectivity.py [-v]
```
