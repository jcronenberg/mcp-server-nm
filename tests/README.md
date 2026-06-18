# Testing

## Requirements

- Root privileges
- NetworkManager running (`nmcli general status` must succeed)
- The `dummy` kernel module available (`modprobe dummy`)
- Python 3.10+ with the `mcp` package importable (system package or virtualenv)

## Running

```
sudo python3 tests/run_tests.py              # all tests
sudo python3 tests/run_tests.py -v           # all tests, verbose
sudo python3 tests/run_tests.py get_devices  # single test (test_ prefix optional)
sudo python3 tests/run_tests.py get_devices get_connections -v  # multiple, verbose
```

Individual test files can also be run directly:

```
sudo python3 tests/test_get_connectivity.py [-v]
```

## Server binary

Set `NM_MCP_TEST_SERVER` to override the default `uv run main.py`:

```
sudo NM_MCP_TEST_SERVER="python3 main.py" python3 tests/run_tests.py
```
