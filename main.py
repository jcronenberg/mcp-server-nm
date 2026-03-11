#!/usr/bin/python3

import dbus
import asyncio
import os
import logging
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import PingRequest, EmptyResult, ClientCapabilities, ElicitationCapability

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(name)s - %(message)s'
)
logger = logging.getLogger("mcp-server-nm")

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
    mac_address: Optional[str] = None

class IPConfig(BaseModel):
    method: Optional[str] = None
    addresses: List[str] = []

class ConnectionInfo(BaseModel):
    name: str
    uuid: str
    type: str
    interface_name: Optional[str] = None
    active: bool
    ipv4: IPConfig
    ipv6: IPConfig

class DnsEntry(BaseModel):
    servers: List[str]
    priority: int
    interface: str

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
    elif isinstance(data, (list, tuple)):
        return [dbus_to_python(item) for item in data]
    elif isinstance(data, dict):
        return {dbus_to_python(key): dbus_to_python(value) for key, value in data.items()}
    return data

class NMClient:
    """Centralized NetworkManager D-Bus client."""
    def __init__(self):
        self._bus = None
        self._manager = None
        self._props = None

    @property
    def bus(self):
        if not self._bus:
            self._bus = dbus.SystemBus()
        return self._bus

    @property
    def manager(self):
        if not self._manager:
            proxy = self.bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
            self._manager = dbus.Interface(proxy, "org.freedesktop.NetworkManager")
        return self._manager

    @property
    def props(self):
        if not self._props:
            proxy = self.bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
            self._props = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
        return self._props

    def get_connectivity(self) -> int:
        return int(self.props.Get("org.freedesktop.NetworkManager", "Connectivity"))

    def parse_ip_config(self, data: Dict[str, Any]) -> IPConfig:
        """Parses D-Bus IP config data into a validated IPConfig model."""
        # Handles both 'AddressData' (runtime) and 'address-data' (settings)
        raw_addresses = data.get("AddressData", data.get("address-data", []))
        formatted_addresses = [
            f"{a['address']}/{a['prefix']}" for a in raw_addresses
        ]
        return IPConfig(
            method=data.get("method"),
            addresses=formatted_addresses
        )

    def get_ip_config(self, path: str, interface_name: str) -> IPConfig:
        if not path or path == "/": return IPConfig()
        try:
            proxy = self.bus.get_object("org.freedesktop.NetworkManager", path)
            prop_iface = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
            p = dbus_to_python(prop_iface.GetAll(interface_name))
            return self.parse_ip_config(p)
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
        try:
            self.checkpoint = None
            initial_conn = self.client.get_connectivity()
            devices = self.client.manager.GetDevices()
            self.checkpoint = self.client.manager.CheckpointCreate(devices, self.timeout, 1)

            await action_fn()
            await asyncio.sleep(2)

            # Check session health
            try:
                await asyncio.wait_for(self.ctx.session.send_request(PingRequest(), result_type=EmptyResult), timeout=5)
            except asyncio.TimeoutError:
                self.client.manager.CheckpointRollback(self.checkpoint)
                return TransactionResult(status="error", message="MCP Session unresponsive after change. Changes rolled back.")

            # Check connectivity
            new_conn = self.client.get_connectivity()

            if new_conn < initial_conn:
                can_elicit = self.ctx.session.check_client_capability(ClientCapabilities(elicitation=ElicitationCapability()))

                if can_elicit:
                    prompt = f"Warning: Connectivity dropped to {CONNECTIVITY_STATES.get(new_conn)}. Keep changes?"
                    response = await self.ctx.elicit(message=prompt, schema=ConnectionConfirm)
                    data = getattr(response, "data", response)
                    confirm = getattr(data, "confirm", False)

                    if confirm:
                        self.client.manager.CheckpointDestroy(self.checkpoint)
                        return TransactionResult(status="success", message="Changes committed by user.")
                    else:
                        self.client.manager.CheckpointRollback(self.checkpoint)
                        return TransactionResult(status="rollback", message="Changes rolled back by user.")
                else:
                    self.client.manager.CheckpointDestroy(self.checkpoint)
                    return TransactionResult(status="success", message=f"Applied. Warning: Connectivity is {CONNECTIVITY_STATES.get(new_conn)}.")
            else:
                self.client.manager.CheckpointDestroy(self.checkpoint)
                return TransactionResult(status="success", message="Changes applied and committed.")

        except Exception as e:
            logger.exception(f"Error during transaction: {e}")
            if self.checkpoint:
                try:
                    self.client.manager.CheckpointRollback(self.checkpoint)
                except Exception as rb_err:
                    logger.error(f"Failed to rollback after transaction error: {rb_err}")
            raise e

mcp = FastMCP(
    "NetworkManager MCP Server",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8000"))
)
nm = NMClient()

@mcp.tool()
async def get_connectivity() -> str:
    """
    Gets the global network connectivity state.
    """
    return CONNECTIVITY_STATES.get(nm.get_connectivity(), "Unknown")

@mcp.tool()
async def get_dns() -> List[DnsEntry]:
    """
    Gets the system DNS configuration.

    Returns:
        - servers
        - priority
        - interface
    """
    dns_entries = []
    try:
        dns_proxy = nm.bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager/DnsManager")
        dns_data = dbus_to_python(dbus.Interface(dns_proxy, "org.freedesktop.DBus.Properties").Get("org.freedesktop.NetworkManager.DnsManager", "Configuration"))
        for dns in dns_data:
            dns_entries.append(DnsEntry(servers=dns.get("nameservers", []), priority=dns.get("priority"), interface=dns.get("interface")))

    except Exception as e:
        logger.error(f"Failed to get dns: {e}")

    return dns_entries

@mcp.tool()
async def get_devices() -> List[DeviceInfo]:
    """
    Gets a list of all network devices.

    Returns:
        - interface
        - type
        - state
        - mac_address
    """
    devices = []
    try:
        for d_path in nm.manager.GetDevices():
            dev_proxy = nm.bus.get_object("org.freedesktop.NetworkManager", d_path)
            p = dbus_to_python(dbus.Interface(dev_proxy, "org.freedesktop.DBus.Properties").GetAll("org.freedesktop.NetworkManager.Device"))
            devices.append(DeviceInfo(
                interface=p.get("Interface"),
                type=DEVICE_TYPES.get(p.get("DeviceType"), "Unknown"),
                state=DEVICE_STATES.get(p.get("State"), "Unknown"),
                mac_address=p.get("HwAddress")
            ))

    except Exception as e:
        logger.error(f"Failed to get devices: {e}")

    return devices

@mcp.tool()
async def get_connections() -> List[ConnectionInfo]:
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
    """
    active_info = {}
    try:
        for ac_path in nm.props.Get("org.freedesktop.NetworkManager", "ActiveConnections"):
            ac_proxy = nm.bus.get_object("org.freedesktop.NetworkManager", ac_path)
            ac_p = dbus_to_python(dbus.Interface(ac_proxy, "org.freedesktop.DBus.Properties").GetAll("org.freedesktop.NetworkManager.Connection.Active"))
            active_info[str(ac_p.get("Uuid"))] = {"ip4": str(ac_p.get("Ip4Config")), "ip6": str(ac_p.get("Ip6Config"))}

        settings_proxy = nm.bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager/Settings")
        settings_iface = dbus.Interface(settings_proxy, "org.freedesktop.NetworkManager.Settings")

        connections = []
        for c_path in settings_iface.ListConnections():
            con_proxy = nm.bus.get_object("org.freedesktop.NetworkManager", c_path)
            config = dbus_to_python(dbus.Interface(con_proxy, "org.freedesktop.NetworkManager.Settings.Connection").GetSettings())
            s_con = config.get("connection", {})
            uuid = s_con.get("uuid")
            ipv4_s = config.get("ipv4", {})
            ipv6_s = config.get("ipv6", {})

            ipv4_data = nm.parse_ip_config(config.get("ipv4", {}))
            ipv6_data = nm.parse_ip_config(config.get("ipv6", {}))

            if uuid in active_info:
                info = active_info[uuid]
                # Overlay active configuration (e.g. DHCP addresses) over stored settings
                a4 = nm.get_ip_config(info["ip4"], "org.freedesktop.NetworkManager.IP4Config")
                if a4.addresses: ipv4_data.addresses = a4.addresses
                a6 = nm.get_ip_config(info["ip6"], "org.freedesktop.NetworkManager.IP6Config")
                if a6.addresses: ipv6_data.addresses = a6.addresses

            connections.append(ConnectionInfo(
                name=s_con.get("id"), uuid=uuid, type=s_con.get("type"),
                interface_name=s_con.get("interface-name"), active=(uuid in active_info),
                ipv4=ipv4_data, ipv6=ipv6_data
            ))

    except Exception as e:
        logger.error(f"Failed to get connections: {e}")

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
        settings_path = dbus.Interface(nm.bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager/Settings"), "org.freedesktop.NetworkManager.Settings").GetConnectionByUuid(connection_uuid)
        if active:
            nm.manager.ActivateConnection(settings_path, "/", "/")
        else:
            active_paths = nm.props.Get("org.freedesktop.NetworkManager", "ActiveConnections")
            for ac_path in active_paths:
                ac_proxy = nm.bus.get_object("org.freedesktop.NetworkManager", ac_path)
                if str(dbus.Interface(ac_proxy, "org.freedesktop.DBus.Properties").Get("org.freedesktop.NetworkManager.Connection.Active", "Uuid")) == connection_uuid:
                    nm.manager.DeactivateConnection(ac_path)
                    break

    return await tx.run(action)

if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "http" or transport == "streamable-http":
        mcp.run(transport="streamable-http")
    else: mcp.run(transport="stdio")
