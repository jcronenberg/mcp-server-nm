#!/usr/bin/python3

import dbus
import asyncio
import logging
from typing import Any
from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import PingRequest, EmptyResult, ClientCapabilities, ElicitationCapability

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(name)s - %(message)s'
)
logger = logging.getLogger("mcp-server-nm")

NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
PROPS = "org.freedesktop.DBus.Properties"

DEVICE_TYPES = {
    0: "Unknown", 1: "Ethernet", 2: "Wi-Fi", 5: "Bluetooth", 6: "OLPC",
    7: "WiMAX", 8: "Modem", 9: "InfiniBand", 10: "Bond", 11: "VLAN",
    12: "ADSL", 13: "Bridge", 14: "Generic", 15: "Team", 16: "TUN",
    17: "IPTunnel", 18: "MACVLAN", 19: "VXLAN", 20: "Veth",
}

DEVICE_STATES = {
    0: "Unknown", 10: "Unmanaged", 20: "Unavailable", 30: "Disconnected",
    40: "Prepare", 50: "Config", 60: "Need Auth", 70: "IP Config",
    80: "IP Check", 90: "Secondaries", 100: "Activated", 110: "Deactivating", 120: "Failed",
}

CONNECTIVITY_STATES = {
    0: "Unknown", 1: "None", 2: "Portal", 3: "Limited", 4: "Full",
}

class DeviceInfo(BaseModel):
    interface: str
    type: str
    state: str
    mac_address: str | None = None

class IPConfig(BaseModel):
    method: str | None = None
    addresses: list[str] = []
    gateway: str | None = None
    dns: list[str] = []

class ConnectionInfo(BaseModel):
    name: str
    uuid: str
    type: str
    interface_name: str | None = None
    active: bool
    ipv4: IPConfig
    ipv6: IPConfig

class ConnectionConfirm(BaseModel):
    confirm: bool = Field(alias="Confirm?", title="Confirm?")

class TransactionResult(BaseModel):
    status: str
    message: str

def dbus_to_python(data):
    """Recursively convert D-Bus types to standard Python types."""
    if isinstance(data, (dbus.String, dbus.ObjectPath)):
        return str(data)
    elif isinstance(data, (dbus.Int16, dbus.Int32, dbus.Int64, dbus.UInt16, dbus.UInt32, dbus.UInt64, dbus.Byte)):
        return int(data)
    elif isinstance(data, dbus.Boolean):
        return bool(data)
    elif isinstance(data, dbus.Double):
        return float(data)
    elif isinstance(data, dbus.Array):
        return [dbus_to_python(item) for item in data]
    elif isinstance(data, dbus.Dictionary):
        return {dbus_to_python(key): dbus_to_python(value) for key, value in data.items()}
    return data

class NMClient:
    """Centralized NetworkManager D-Bus client."""
    def __init__(self):
        self.bus = dbus.SystemBus()
        self.manager = self.iface(NM_PATH, NM)
        self.settings = self.iface(f"{NM_PATH}/Settings", f"{NM}.Settings")

    def iface(self, path: str, name: str) -> dbus.Interface:
        return dbus.Interface(self.bus.get_object(NM, path), name)

    def get_prop(self, path: str, iface_name: str, prop: str) -> Any:
        return dbus_to_python(self.iface(path, PROPS).Get(iface_name, prop))

    def get_all(self, path: str, iface_name: str) -> dict[str, Any]:
        return dbus_to_python(self.iface(path, PROPS).GetAll(iface_name))

    def get_connectivity(self) -> int:
        return self.get_prop(NM_PATH, NM, "Connectivity")

    def parse_ip_config(self, data: dict[str, Any]) -> IPConfig:
        """Parses D-Bus IP config data into a validated IPConfig model."""
        # Handles both 'AddressData' (runtime) and 'address-data' (settings)
        raw = data.get("AddressData", data.get("address-data", []))
        dns_raw = data.get("NameserverData") or data.get("dns-data") or data.get("dns") or []
        return IPConfig(
            method=data.get("method"),
            addresses=[f"{a['address']}/{a['prefix']}" for a in raw],
            gateway=data.get("Gateway") or data.get("gateway"),
            dns=[d["address"] if isinstance(d, dict) else d for d in dns_raw],
        )

    def build_ip_settings(self, cfg: IPConfig) -> dict:
        """Build an NM ipv4/ipv6 settings dict from an IPConfig."""
        out: dict = {}
        if cfg.method:
            out["method"] = cfg.method
        if cfg.addresses:
            out["address-data"] = dbus.Array([
                dbus.Dictionary({
                    "address": addr.split("/")[0],
                    "prefix": dbus.UInt32(int(addr.split("/")[1])),
                }, signature="sv")
                for addr in cfg.addresses
            ], signature="a{sv}")
        if cfg.gateway:
            out["gateway"] = cfg.gateway
        if cfg.dns:
            out["dns"] = dbus.Array(cfg.dns, signature="s")
        return out

    def get_ip_config(self, path: str, iface_name: str) -> IPConfig:
        if path == "/":
            return IPConfig()
        try:
            return self.parse_ip_config(self.get_all(path, iface_name))
        except Exception as e:
            logger.error(f"Failed to get IP config for {path}: {e}")
            return IPConfig()

class NMTransaction:
    """Helper for safe NetworkManager changes with rollback support."""
    def __init__(self, client: NMClient, ctx: Context, timeout=60):
        self.client = client
        self.ctx = ctx
        self.timeout = timeout
        self.checkpoint = None

    async def run(self, action_fn) -> TransactionResult:
        self.checkpoint = None
        try:
            initial_conn = self.client.get_connectivity()
            devices = self.client.manager.GetDevices()
            self.checkpoint = self.client.manager.CheckpointCreate(devices, self.timeout, 0)

            await action_fn()
            await asyncio.sleep(2)

            try:
                await asyncio.wait_for(self.ctx.session.send_request(PingRequest(), result_type=EmptyResult), timeout=5)
            except asyncio.TimeoutError:
                self.client.manager.CheckpointRollback(self.checkpoint)
                return TransactionResult(status="error", message="MCP Session unresponsive after change. Changes rolled back.")

            new_conn = self.client.get_connectivity()

            if new_conn >= initial_conn:
                self.client.manager.CheckpointDestroy(self.checkpoint)
                return TransactionResult(status="success", message="Changes applied and committed.")

            can_elicit = self.ctx.session.check_client_capability(ClientCapabilities(elicitation=ElicitationCapability()))
            if not can_elicit:
                self.client.manager.CheckpointDestroy(self.checkpoint)
                return TransactionResult(status="success", message=f"Applied. Warning: Connectivity is {CONNECTIVITY_STATES.get(new_conn)}.")

            prompt = f"Warning: Connectivity dropped to {CONNECTIVITY_STATES.get(new_conn)}. Keep changes?"
            response = await self.ctx.elicit(message=prompt, schema=ConnectionConfirm)
            data = getattr(response, "data", response)
            confirm = getattr(data, "confirm", False)

            if confirm:
                self.client.manager.CheckpointDestroy(self.checkpoint)
                return TransactionResult(status="success", message="Changes committed by user.")
            self.client.manager.CheckpointRollback(self.checkpoint)
            return TransactionResult(status="rollback", message="Changes rolled back by user.")

        except Exception as e:
            logger.exception(f"Error during transaction: {e}")
            if self.checkpoint:
                try:
                    self.client.manager.CheckpointRollback(self.checkpoint)
                except Exception as rb_err:
                    logger.error(f"Failed to rollback after transaction error: {rb_err}")
            raise

mcp = FastMCP("NetworkManager MCP Server")
nm = NMClient()

@mcp.tool()
async def get_connectivity() -> str:
    """
    Gets the global network connectivity state.
    """
    return CONNECTIVITY_STATES.get(nm.get_connectivity(), "Unknown")

@mcp.tool()
async def get_devices() -> list[DeviceInfo]:
    """
    Gets a list of all network devices.

    Returns:
        - interface
        - type
        - state
        - mac_address
    """
    devices = []
    for d_path in nm.manager.GetDevices():
        p = nm.get_all(d_path, f"{NM}.Device")
        devices.append(DeviceInfo(
            interface=p.get("Interface"),
            type=DEVICE_TYPES.get(p.get("DeviceType"), "Unknown"),
            state=DEVICE_STATES.get(p.get("State"), "Unknown"),
            mac_address=p.get("HwAddress")
        ))
    return devices

@mcp.tool()
async def get_connections() -> list[ConnectionInfo]:
    """
    Gets all configured connection profiles.

    Returns:
        - name
        - uuid
        - type
        - interface_name
        - active
        - ipv4
        - ipv6

        IPConfig objects contain:
        - method
        - addresses
        - gateway
        - dns
    """
    active_info = {}
    for ac_path in nm.get_prop(NM_PATH, NM, "ActiveConnections"):
        ac_p = nm.get_all(ac_path, f"{NM}.Connection.Active")
        active_info[ac_p.get("Uuid")] = {"ip4": ac_p.get("Ip4Config"), "ip6": ac_p.get("Ip6Config")}

    connections = []
    for c_path in nm.settings.ListConnections():
        config = dbus_to_python(nm.iface(c_path, f"{NM}.Settings.Connection").GetSettings())
        s_con = config.get("connection", {})
        uuid = s_con.get("uuid")

        ipv4_data = nm.parse_ip_config(config.get("ipv4", {}))
        ipv6_data = nm.parse_ip_config(config.get("ipv6", {}))

        if uuid in active_info:
            info = active_info[uuid]
            # Overlay active configuration (e.g. DHCP addresses) over stored settings
            for cfg, ip_path, iface in [
                (ipv4_data, info["ip4"], f"{NM}.IP4Config"),
                (ipv6_data, info["ip6"], f"{NM}.IP6Config"),
            ]:
                runtime = nm.get_ip_config(ip_path, iface)
                cfg.addresses = runtime.addresses or cfg.addresses
                cfg.gateway = runtime.gateway or cfg.gateway
                cfg.dns = runtime.dns or cfg.dns

        connections.append(ConnectionInfo(
            name=s_con.get("id"), uuid=uuid, type=s_con.get("type"),
            interface_name=s_con.get("interface-name"), active=(uuid in active_info),
            ipv4=ipv4_data, ipv6=ipv6_data
        ))

    return connections

@mcp.tool()
async def set_connection_state(connection_uuid: str, active: bool, ctx: Context) -> TransactionResult:
    """
    Activates or deactivates a connection profile by UUID.
    Includes safety checkpoint and connectivity check with interactive confirmation with the user.

    Args:
        connection_uuid: The UUID of the connection to activate or deactivate.
        active: Set to True to activate (bring up) or False to deactivate (bring down).

    Returns:
        - status
        - message
    """
    tx = NMTransaction(nm, ctx)

    async def action():
        settings_path = nm.settings.GetConnectionByUuid(connection_uuid)
        if active:
            nm.manager.ActivateConnection(settings_path, "/", "/")
        else:
            for ac_path in nm.get_prop(NM_PATH, NM, "ActiveConnections"):
                if nm.get_prop(ac_path, f"{NM}.Connection.Active", "Uuid") == connection_uuid:
                    nm.manager.DeactivateConnection(ac_path)
                    break

    return await tx.run(action)

@mcp.tool()
async def add_connection(
    name: str,
    type: str,
    interface_name: str | None = None,
    ipv4: IPConfig | None = None,
    ipv6: IPConfig | None = None,
) -> ConnectionInfo:
    """
    Creates a new connection profile.

    Args:
        name: Display name for the connection.
        type: NM connection type string, e.g. "802-3-ethernet", "vlan", "bridge".
        interface_name: Optional interface to bind the profile to.
        ipv4: IPv4 configuration. Defaults to DHCP ('auto').
        ipv6: IPv6 configuration. Defaults to autoconf ('auto').

    Returns:
        ConnectionInfo for the new profile.
    """
    s_con = {"id": name, "type": type}
    if interface_name:
        s_con["interface-name"] = interface_name

    settings = dbus.Dictionary({
        "connection": dbus.Dictionary(s_con, signature="sv"),
        type: dbus.Dictionary({}, signature="sv"),
        "ipv4": dbus.Dictionary(nm.build_ip_settings(ipv4 or IPConfig(method="auto")), signature="sv"),
        "ipv6": dbus.Dictionary(nm.build_ip_settings(ipv6 or IPConfig(method="auto")), signature="sv"),
    }, signature="sa{sv}")
    path = nm.settings.AddConnection(settings)
    config = dbus_to_python(nm.iface(path, f"{NM}.Settings.Connection").GetSettings())
    s_con_r = config.get("connection", {})
    return ConnectionInfo(
        name=s_con_r.get("id"), uuid=s_con_r.get("uuid"), type=s_con_r.get("type"),
        interface_name=s_con_r.get("interface-name"), active=False,
        ipv4=nm.parse_ip_config(config.get("ipv4", {})),
        ipv6=nm.parse_ip_config(config.get("ipv6", {})),
    )

@mcp.tool()
async def modify_connection(
    uuid: str,
    ctx: Context,
    name: str | None = None,
    interface_name: str | None = None,
    ipv4: IPConfig | None = None,
    ipv6: IPConfig | None = None,
) -> TransactionResult:
    """
    Updates an existing connection profile by UUID. Provided ipv4/ipv6 sections
    REPLACE the existing ones entirely, so include all fields you want to keep.
    Re-activates the connection if it is currently active. Wrapped in a safety
    checkpoint with rollback on connectivity loss.

    Args:
        uuid: UUID of the connection to modify.
        name: New display name (optional).
        interface_name: New interface binding (optional).
        ipv4: New IPv4 configuration (replaces existing section).
        ipv6: New IPv6 configuration (replaces existing section).

    Returns:
        - status
        - message
    """
    tx = NMTransaction(nm, ctx)

    async def action():
        path = nm.settings.GetConnectionByUuid(uuid)
        conn = nm.iface(path, f"{NM}.Settings.Connection")
        existing = conn.GetSettings()

        if name is not None:
            existing["connection"]["id"] = name
        if interface_name is not None:
            existing["connection"]["interface-name"] = interface_name
        if ipv4 is not None:
            existing["ipv4"] = dbus.Dictionary(nm.build_ip_settings(ipv4), signature="sv")
        if ipv6 is not None:
            existing["ipv6"] = dbus.Dictionary(nm.build_ip_settings(ipv6), signature="sv")

        conn.Update(existing)

        for ac_path in nm.get_prop(NM_PATH, NM, "ActiveConnections"):
            if nm.get_prop(ac_path, f"{NM}.Connection.Active", "Uuid") == uuid:
                nm.manager.DeactivateConnection(ac_path)
                nm.manager.ActivateConnection(path, "/", "/")
                break

    return await tx.run(action)

@mcp.tool()
async def delete_connection(uuid: str, ctx: Context) -> TransactionResult:
    """
    Deletes a connection profile by UUID.

    Args:
        uuid: UUID of the connection to delete.

    Returns:
        - status
        - message
    """
    tx = NMTransaction(nm, ctx)

    async def action():
        path = nm.settings.GetConnectionByUuid(uuid)
        nm.iface(path, f"{NM}.Settings.Connection").Delete()

    return await tx.run(action)

@mcp.tool()
async def get_hostname() -> str:
    """
    Gets the persistent system hostname.
    """
    return nm.get_prop(f"{NM_PATH}/Settings", f"{NM}.Settings", "Hostname")

@mcp.tool()
async def set_hostname(hostname: str) -> TransactionResult:
    """
    Sets the persistent system hostname.

    Args:
        hostname: The new hostname. Pass an empty string to clear it.

    Returns:
        - status
        - message
    """
    nm.settings.SaveHostname(hostname)
    return TransactionResult(status="success", message=f"Hostname set to '{hostname}'.")

if __name__ == "__main__":
    mcp.run(transport="stdio")
