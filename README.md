# NetworkManager MCP Server

A Model Context Protocol (MCP) server for NetworkManager.

## Usage

```bash
uv run main.py
```

## Tools

**Read-only**
- `get_connectivity` - global network connectivity state
- `get_devices` - list network devices (interface, type, state, MAC address)
- `get_connections` - list configured connection profiles with IPv4/IPv6 config
- `get_hostname` - persistent system hostname

**Mutating**
- `set_connection_state` - activate or deactivate a connection profile by UUID
- `add_connection` - create a new connection profile
- `modify_connection` - update an existing connection profile by UUID
- `delete_connection` - delete a connection profile by UUID
- `set_hostname` - set the persistent system hostname

## Safety features

Mutating operations that can affect connectivity (`set_connection_state`, `modify_connection`, `delete_connection`) run through a transaction wrapper that:

- Creates a NetworkManager checkpoint before applying any change, so it can be rolled back.
- Verifies the MCP session is still responsive after the change; if not, the change is rolled back automatically.
- Compares connectivity before and after the change. If connectivity has degraded and the client supports elicitation, the user is prompted to confirm or roll back the change. If the client doesn't support elicitation, the change is kept but a warning is included in the response.
- Rolls back the checkpoint if an unexpected error occurs while applying the change.
