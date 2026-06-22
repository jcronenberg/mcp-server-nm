#!/usr/bin/python3

import dbus
import asyncio
from typing import Any
from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import PingRequest, EmptyResult, ClientCapabilities, ElicitationCapability

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
    addresses: list[str] = Field(default_factory=list)
    gateway: str | None = None
    dns: list[str] = Field(default_factory=list)

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
        """Parses D-Bus IP config data into a validated IPConfig model.

        Handles both runtime (AddressData/NameserverData/Gateway) and settings
        (address-data/dns-data/gateway) shapes.
        """
        raw = data.get("AddressData") or data.get("address-data") or []
        dns_raw = data.get("NameserverData") or data.get("dns-data") or []

        addresses = [f"{a['address']}/{a['prefix']}" for a in raw]

        dns = []
        for d in dns_raw:
            if isinstance(d, dict):
                dns.append(d["address"])
            else:
                dns.append(d)

        return IPConfig(
            method=data.get("method"),
            addresses=addresses,
            gateway=data.get("Gateway") or data.get("gateway"),
            dns=dns,
        )

    def build_ip_settings(self, cfg: IPConfig) -> dict:
        """Build an NM ipv4/ipv6 settings dict from an IPConfig."""
        out: dict = {}
        if cfg.method:
            out["method"] = cfg.method
        if cfg.addresses:
            addr_list = []
            for addr in cfg.addresses:
                host, prefix = addr.split("/", 1)
                addr_list.append(dbus.Dictionary({
                    "address": host,
                    "prefix": dbus.UInt32(int(prefix)),
                }, signature="sv"))
            out["address-data"] = dbus.Array(addr_list, signature="a{sv}")
        if cfg.gateway:
            out["gateway"] = cfg.gateway
        if cfg.dns:
            out["dns-data"] = dbus.Array(cfg.dns, signature="s")
        return out

    def get_ip_config(self, path: str, iface_name: str) -> IPConfig:
        if path == "/":
            return IPConfig()
        return self.parse_ip_config(self.get_all(path, iface_name))

    def find_active_path(self, uuid: str) -> str | None:
        for ac_path in self.get_prop(NM_PATH, NM, "ActiveConnections"):
            if self.get_prop(ac_path, f"{NM}.Connection.Active", "Uuid") == uuid:
                return ac_path
        return None

    def build_connection_info(self, config: dict, active: bool) -> "ConnectionInfo":
        s_con = config.get("connection", {})
        return ConnectionInfo(
            name=s_con.get("id"),
            uuid=s_con.get("uuid"),
            type=s_con.get("type"),
            interface_name=s_con.get("interface-name"),
            active=active,
            ipv4=self.parse_ip_config(config.get("ipv4", {})),
            ipv6=self.parse_ip_config(config.get("ipv6", {})),
        )

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

            # Skip the comparison if either reading is Unknown (0):
            # NM may not have run a connectivity check yet, or the check is disabled.
            if 0 in (initial_conn, new_conn) or new_conn >= initial_conn:
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

        except Exception:
            self._rollback()
            raise

    def _rollback(self):
        if self.checkpoint:
            self.client.manager.CheckpointRollback(self.checkpoint)

mcp = FastMCP("NetworkManager MCP Server")

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
        uuid = config.get("connection", {}).get("uuid")
        info = nm.build_connection_info(config, active=uuid in active_info)

        if uuid in active_info:
            ac = active_info[uuid]
            # Overlay active runtime values over stored settings.
            # Only overlay when there is an actual IP config object (path != "/");
            # use runtime values directly so that empty lists aren't mistaken for
            # "no data" and replaced by stale stored-settings values.
            ip4_path = ac["ip4"]
            if ip4_path != "/":
                runtime4 = nm.get_ip_config(ip4_path, f"{NM}.IP4Config")
                info.ipv4.addresses = runtime4.addresses
                info.ipv4.gateway = runtime4.gateway
                info.ipv4.dns = runtime4.dns

            ip6_path = ac["ip6"]
            if ip6_path != "/":
                runtime6 = nm.get_ip_config(ip6_path, f"{NM}.IP6Config")
                info.ipv6.addresses = runtime6.addresses
                info.ipv6.gateway = runtime6.gateway
                info.ipv6.dns = runtime6.dns

        connections.append(info)

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
        if active:
            settings_path = nm.settings.GetConnectionByUuid(connection_uuid)
            nm.manager.ActivateConnection(settings_path, "/", "/")
        else:
            ac_path = nm.find_active_path(connection_uuid)
            if ac_path:
                nm.manager.DeactivateConnection(ac_path)

    return await tx.run(action)

@mcp.tool()
async def add_connection(
    name: str,
    conn_type: str,
    interface_name: str | None = None,
    ipv4: IPConfig | None = None,
    ipv6: IPConfig | None = None,
) -> ConnectionInfo:
    """
    Creates a new connection profile.

    Args:
        name: Display name for the connection.
        conn_type: NM connection type string, e.g. "802-3-ethernet", "vlan", "bridge".
        interface_name: Optional interface to bind the profile to.
        ipv4: IPv4 configuration. Defaults to DHCP ('auto').
        ipv6: IPv6 configuration. Defaults to autoconf ('auto').

    Returns:
        ConnectionInfo for the new profile.
    """
    if ipv4 is None:
        ipv4 = IPConfig(method="auto")
    if ipv6 is None:
        ipv6 = IPConfig(method="auto")

    s_con = {"id": name, "type": conn_type}
    if interface_name:
        s_con["interface-name"] = interface_name

    settings = dbus.Dictionary({
        "connection": dbus.Dictionary(s_con, signature="sv"),
        conn_type: dbus.Dictionary({}, signature="sv"),
        "ipv4": dbus.Dictionary(nm.build_ip_settings(ipv4), signature="sv"),
        "ipv6": dbus.Dictionary(nm.build_ip_settings(ipv6), signature="sv"),
    }, signature="sa{sv}")
    path = nm.settings.AddConnection(settings)
    config = dbus_to_python(nm.iface(path, f"{NM}.Settings.Connection").GetSettings())
    return nm.build_connection_info(config, active=False)

@mcp.tool()
async def modify_connection(
    uuid: str,
    name: str | None = None,
    interface_name: str | None = None,
    ipv4: IPConfig | None = None,
    ipv6: IPConfig | None = None,
    *,
    ctx: Context,
) -> TransactionResult:
    """
    Updates an existing connection profile by UUID. Provided ipv4/ipv6 sections
    REPLACE the existing ones entirely, so include all fields you want to keep.
    If the connection is currently active, the new settings are reapplied to
    each underlying device without bouncing the link. Wrapped in a safety
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

        ac_path = nm.find_active_path(uuid)
        if ac_path:
            for dev_path in nm.get_prop(ac_path, f"{NM}.Connection.Active", "Devices"):
                nm.iface(dev_path, f"{NM}.Device").Reapply(existing, 0, 0)

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
    nm = NMClient()
    mcp.run(transport="stdio")
