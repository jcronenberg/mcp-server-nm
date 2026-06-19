#!/usr/bin/env python3
"""Unit tests for pure functions in main.py — no NetworkManager or root required."""

import os
import sys
import unittest

import dbus

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import NMClient, IPConfig, dbus_to_python


def make_client():
    """Create an NMClient without a D-Bus connection (skips __init__)."""
    return NMClient.__new__(NMClient)


class TestDbusToPython(unittest.TestCase):
    def test_string(self):
        result = dbus_to_python(dbus.String("hello"))
        self.assertEqual(result, "hello")
        self.assertIs(type(result), str)

    def test_object_path(self):
        result = dbus_to_python(dbus.ObjectPath("/org/freedesktop/NM"))
        self.assertEqual(result, "/org/freedesktop/NM")
        self.assertIs(type(result), str)

    def test_integers(self):
        for cls, val in [(dbus.Int32, -1), (dbus.UInt32, 42), (dbus.UInt64, 2**40)]:
            result = dbus_to_python(cls(val))
            self.assertEqual(result, val)
            self.assertIs(type(result), int)

    def test_boolean(self):
        self.assertIs(dbus_to_python(dbus.Boolean(True)), True)
        self.assertIs(dbus_to_python(dbus.Boolean(False)), False)

    def test_double(self):
        self.assertAlmostEqual(dbus_to_python(dbus.Double(3.14)), 3.14)

    def test_array(self):
        arr = dbus.Array([dbus.String("a"), dbus.String("b")], signature="s")
        self.assertEqual(dbus_to_python(arr), ["a", "b"])

    def test_dictionary(self):
        d = dbus.Dictionary({"key": dbus.UInt32(1)}, signature="sv")
        self.assertEqual(dbus_to_python(d), {"key": 1})

    def test_nested(self):
        inner = dbus.Dictionary({"x": dbus.Boolean(True)}, signature="sv")
        outer = dbus.Dictionary({"inner": inner}, signature="sv")
        self.assertEqual(dbus_to_python(outer), {"inner": {"x": True}})


class TestParseIPConfig(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    def test_empty(self):
        cfg = self.client.parse_ip_config({})
        self.assertIsNone(cfg.method)
        self.assertEqual(cfg.addresses, [])
        self.assertIsNone(cfg.gateway)
        self.assertEqual(cfg.dns, [])

    def test_method_only(self):
        cfg = self.client.parse_ip_config({"method": "auto"})
        self.assertEqual(cfg.method, "auto")

    def test_runtime_shape(self):
        # Runtime D-Bus properties use capitalized keys
        cfg = self.client.parse_ip_config({
            "AddressData": [{"address": "192.168.1.10", "prefix": 24}],
            "Gateway": "192.168.1.1",
            "NameserverData": [{"address": "8.8.8.8"}, {"address": "8.8.4.4"}],
        })
        self.assertEqual(cfg.addresses, ["192.168.1.10/24"])
        self.assertEqual(cfg.gateway, "192.168.1.1")
        self.assertEqual(cfg.dns, ["8.8.8.8", "8.8.4.4"])

    def test_settings_shape(self):
        # Stored settings use lowercase hyphenated keys
        cfg = self.client.parse_ip_config({
            "method": "manual",
            "address-data": [{"address": "10.0.0.1", "prefix": 8}],
            "gateway": "10.0.0.254",
            "dns-data": ["1.1.1.1"],
        })
        self.assertEqual(cfg.method, "manual")
        self.assertEqual(cfg.addresses, ["10.0.0.1/8"])
        self.assertEqual(cfg.gateway, "10.0.0.254")
        self.assertEqual(cfg.dns, ["1.1.1.1"])

    def test_dns_string_entries(self):
        cfg = self.client.parse_ip_config({"dns-data": ["9.9.9.9", "149.112.112.112"]})
        self.assertEqual(cfg.dns, ["9.9.9.9", "149.112.112.112"])

    def test_multiple_addresses(self):
        cfg = self.client.parse_ip_config({
            "address-data": [
                {"address": "192.168.0.1", "prefix": 24},
                {"address": "10.0.0.1", "prefix": 8},
            ]
        })
        self.assertEqual(cfg.addresses, ["192.168.0.1/24", "10.0.0.1/8"])


class TestBuildIPSettings(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    def _build(self, cfg):
        """Build settings and round-trip through dbus_to_python for plain-dict assertions."""
        raw = self.client.build_ip_settings(cfg)
        return dbus_to_python(dbus.Dictionary(raw, signature="sv"))

    def test_empty(self):
        self.assertEqual(self._build(IPConfig()), {})

    def test_method(self):
        result = self._build(IPConfig(method="auto"))
        self.assertEqual(result["method"], "auto")

    def test_addresses(self):
        result = self._build(IPConfig(addresses=["192.168.1.5/24"]))
        self.assertEqual(result["address-data"], [{"address": "192.168.1.5", "prefix": 24}])

    def test_multiple_addresses(self):
        result = self._build(IPConfig(addresses=["10.0.0.1/8", "10.0.0.2/8"]))
        self.assertEqual(result["address-data"], [
            {"address": "10.0.0.1", "prefix": 8},
            {"address": "10.0.0.2", "prefix": 8},
        ])

    def test_gateway(self):
        result = self._build(IPConfig(gateway="192.168.1.1"))
        self.assertEqual(result["gateway"], "192.168.1.1")

    def test_dns(self):
        result = self._build(IPConfig(dns=["8.8.8.8", "8.8.4.4"]))
        self.assertEqual(result["dns-data"], ["8.8.8.8", "8.8.4.4"])

    def test_full_config(self):
        cfg = IPConfig(
            method="manual",
            addresses=["10.0.0.2/24"],
            gateway="10.0.0.1",
            dns=["1.1.1.1"],
        )
        result = self._build(cfg)
        self.assertEqual(result["method"], "manual")
        self.assertEqual(result["address-data"], [{"address": "10.0.0.2", "prefix": 24}])
        self.assertEqual(result["gateway"], "10.0.0.1")
        self.assertEqual(result["dns-data"], ["1.1.1.1"])


class TestBuildConnectionInfo(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    def _config(self, interface_name=None, ipv4=None, ipv6=None):
        s_con = {"id": "my-conn", "uuid": "abc-123", "type": "802-3-ethernet"}
        if interface_name:
            s_con["interface-name"] = interface_name
        return {
            "connection": s_con,
            "ipv4": ipv4 or {},
            "ipv6": ipv6 or {},
        }

    def test_basic_inactive(self):
        info = self.client.build_connection_info(self._config(), active=False)
        self.assertEqual(info.name, "my-conn")
        self.assertEqual(info.uuid, "abc-123")
        self.assertEqual(info.type, "802-3-ethernet")
        self.assertIsNone(info.interface_name)
        self.assertFalse(info.active)

    def test_active_flag(self):
        info = self.client.build_connection_info(self._config(), active=True)
        self.assertTrue(info.active)

    def test_interface_name(self):
        info = self.client.build_connection_info(self._config(interface_name="eth0"), active=False)
        self.assertEqual(info.interface_name, "eth0")

    def test_ipv4_parsed(self):
        config = self._config(ipv4={
            "method": "manual",
            "address-data": [{"address": "10.0.0.1", "prefix": 24}],
            "gateway": "10.0.0.254",
        })
        info = self.client.build_connection_info(config, active=False)
        self.assertEqual(info.ipv4.method, "manual")
        self.assertEqual(info.ipv4.addresses, ["10.0.0.1/24"])
        self.assertEqual(info.ipv4.gateway, "10.0.0.254")

    def test_ipv6_parsed(self):
        config = self._config(ipv6={"method": "auto"})
        info = self.client.build_connection_info(config, active=False)
        self.assertEqual(info.ipv6.method, "auto")


if __name__ == "__main__":
    unittest.main(verbosity=2)
