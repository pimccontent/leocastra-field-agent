#!/usr/bin/env python3
"""Unit tests for bond_policy (no kit/SSH required)."""

import unittest

from bond_policy import path_is_wedged_dead, slash24_equal, uplink_kind_is_bondable


class BondPolicyTests(unittest.TestCase):
    def test_ethernet_not_bondable_on_public_ingest(self):
        self.assertFalse(uplink_kind_is_bondable("ethernet", lan_lab=False))
        self.assertTrue(uplink_kind_is_bondable("ethernet", lan_lab=True))

    def test_cellular_always_bondable(self):
        self.assertTrue(uplink_kind_is_bondable("cellular", lan_lab=False))
        self.assertTrue(uplink_kind_is_bondable("wifi", lan_lab=False))

    def test_slash24_modem_vs_kit_lan(self):
        self.assertTrue(slash24_equal("192.168.0.122", "192.168.0.100"))
        self.assertFalse(slash24_equal("10.208.35.111", "192.168.0.100"))
        self.assertFalse(slash24_equal("bad", "192.168.0.100"))

    def test_wedge_requires_peer_and_dead_stats(self):
        self.assertFalse(
            path_is_wedged_dead(inflight=2000, kbps=0, peer_ok=False)
        )
        self.assertFalse(
            path_is_wedged_dead(inflight=100, kbps=500, peer_ok=True)
        )
        self.assertTrue(
            path_is_wedged_dead(inflight=900, kbps=25, peer_ok=True)
        )
        self.assertTrue(
            path_is_wedged_dead(inflight=1200, kbps=0, peer_ok=True)
        )


if __name__ == "__main__":
    unittest.main()
