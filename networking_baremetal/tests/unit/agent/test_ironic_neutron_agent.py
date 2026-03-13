# Copyright (c) 2026 Red Hat, Inc.
# All Rights Reserved
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import datetime
from unittest import mock

from neutron.tests import base as tests_base
from neutron_lib import constants as n_const
from oslo_config import cfg
from oslo_utils import timeutils
from tooz import hashring

from networking_baremetal.agent import agent_config
from networking_baremetal.agent import ironic_neutron_agent
from networking_baremetal import constants


CONF = cfg.CONF


class FakePort:
    """Fake Neutron Port object."""

    def __init__(self, port_id, network_id, device_owner,
                 updated_at=None):
        self.id = port_id
        self.network_id = network_id
        self.device_owner = device_owner
        # updated_at should be ISO8601 string like real Neutron ports
        self.updated_at = updated_at or timeutils.utcnow().isoformat()


class FakeLogicalSwitchPort:
    """Fake OVN Logical Switch Port object."""

    def __init__(self, name, ha_chassis_group=None):
        self.name = name
        self.ha_chassis_group = (
            [ha_chassis_group] if ha_chassis_group else [])


class FakeLogicalRouterPort:
    """Fake OVN Logical Router Port object."""

    def __init__(self, name, ha_chassis_group=None):
        self.name = name
        self.ha_chassis_group = (
            [ha_chassis_group] if ha_chassis_group else [])


class FakeOVNCommand:
    """Fake OVN IDL command result."""

    def __init__(self, result):
        self.result = result

    def execute(self, check_error=False):
        return self.result


class TestHAChassisGroupAlignment(tests_base.BaseTestCase):
    """Test cases for HA chassis group alignment reconciliation."""

    def setUp(self):
        super(TestHAChassisGroupAlignment, self).setUp()
        # Register config options
        agent_config.register_baremetal_agent_opts(CONF)

        # Set required config overrides
        CONF.set_override('enable_ha_chassis_group_alignment', True,
                          group='baremetal_agent')

        # Create mock agent with minimal setup
        self.agent = mock.MagicMock(spec=ironic_neutron_agent
                                    .BaremetalNeutronAgent)
        self.agent._ha_alignment_lock = mock.MagicMock()
        self.agent._ha_alignment_lock.acquire.return_value = True

        # Setup agent_id and member_manager with real hashring
        self.agent.agent_id = 'test-agent-id'
        self.agent.member_manager = mock.MagicMock()
        # Create a hashring with our test agent as the only member
        # This means our agent will be responsible for all keys
        self.agent.member_manager.hashring = hashring.HashRing(
            [self.agent.agent_id])

        self.agent.trunk_manager = None

        # Bind the methods we're testing
        self.agent._reconcile_ha_chassis_group_alignment = (
            ironic_neutron_agent.BaremetalNeutronAgent
            ._reconcile_ha_chassis_group_alignment.__get__(self.agent))
        self.agent._align_ha_chassis_group_for_network = (
            ironic_neutron_agent.BaremetalNeutronAgent
            ._align_ha_chassis_group_for_network.__get__(self.agent))
        self.agent._get_neutron_client = mock.MagicMock()

    @mock.patch('networking_baremetal.agent.ovn_client.get_ovn_nb_idl',
                autospec=True)
    def test_reconcile_no_baremetal_ports(self, mock_get_ovn_nb):
        """Test reconciliation when no baremetal ports exist."""
        # Setup mocks
        mock_neutron = mock.MagicMock()
        mock_neutron.network.ports.return_value = []
        self.agent._get_neutron_client.return_value = mock_neutron

        mock_ovn_nb = mock.MagicMock()
        mock_get_ovn_nb.return_value = mock_ovn_nb

        # Execute
        self.agent._reconcile_ha_chassis_group_alignment()

        # Verify - should query for baremetal ports but do nothing else
        mock_neutron.network.ports.assert_called_once_with(
            device_owner=constants.BAREMETAL_NONE)
        # Should not query for router ports
        self.assertEqual(1, mock_neutron.network.ports.call_count)

    @mock.patch('networking_baremetal.agent.ovn_client.get_ovn_nb_idl',
                autospec=True)
    def test_reconcile_with_baremetal_ports(self, mock_get_ovn_nb):
        """Test reconciliation processes baremetal ports correctly."""
        # Setup mocks
        bm_port = FakePort('bm-port-1', 'net-1', constants.BAREMETAL_NONE)
        router_port = FakePort('router-port-1', 'net-1',
                               n_const.DEVICE_OWNER_ROUTER_INTF)

        mock_neutron = mock.MagicMock()
        mock_neutron.network.ports.side_effect = [
            [bm_port],  # First call for baremetal ports
            [router_port]  # Second call for router ports on network
        ]
        self.agent._get_neutron_client.return_value = mock_neutron

        # Setup OVN mocks
        lsp = FakeLogicalSwitchPort('neutron-bm-port-1', 'ha-group-1')
        lrp = FakeLogicalRouterPort('lrp-router-port-1', 'ha-group-2')

        mock_ovn_nb = mock.MagicMock()
        mock_ovn_nb.lsp_get.return_value = FakeOVNCommand(lsp)
        mock_ovn_nb.lrp_get.return_value = FakeOVNCommand(lrp)
        mock_ovn_nb.lrp_set_ha_chassis_group.return_value = (
            FakeOVNCommand(None))
        mock_get_ovn_nb.return_value = mock_ovn_nb

        # Execute
        self.agent._reconcile_ha_chassis_group_alignment()

        # Verify - should update router port's HA chassis group
        mock_ovn_nb.lrp_set_ha_chassis_group.assert_called_once_with(
            'lrp-router-port-1', 'ha-group-1')

    @mock.patch('networking_baremetal.agent.ovn_client.get_ovn_nb_idl',
                autospec=True)
    def test_reconcile_already_aligned(self, mock_get_ovn_nb):
        """Test reconciliation when HA groups already match."""
        # Setup mocks
        bm_port = FakePort('bm-port-1', 'net-1', constants.BAREMETAL_NONE)
        router_port = FakePort('router-port-1', 'net-1',
                               n_const.DEVICE_OWNER_ROUTER_INTF)

        mock_neutron = mock.MagicMock()
        mock_neutron.network.ports.side_effect = [
            [bm_port],
            [router_port]
        ]
        self.agent._get_neutron_client.return_value = mock_neutron

        # Both use the same HA chassis group
        lsp = FakeLogicalSwitchPort('neutron-bm-port-1', 'ha-group-1')
        lrp = FakeLogicalRouterPort('lrp-router-port-1', 'ha-group-1')

        mock_ovn_nb = mock.MagicMock()
        mock_ovn_nb.lsp_get.return_value = FakeOVNCommand(lsp)
        mock_ovn_nb.lrp_get.return_value = FakeOVNCommand(lrp)
        mock_get_ovn_nb.return_value = mock_ovn_nb

        # Execute
        self.agent._reconcile_ha_chassis_group_alignment()

        # Verify - should NOT update since already aligned
        mock_ovn_nb.lrp_set_ha_chassis_group.assert_not_called()

    @mock.patch('networking_baremetal.agent.ovn_client.get_ovn_nb_idl',
                autospec=True)
    def test_reconcile_filters_by_hash_ring(self, mock_get_ovn_nb):
        """Test reconciliation respects hash ring filtering."""
        # Setup mocks
        bm_port = FakePort('bm-port-1', 'net-1', constants.BAREMETAL_NONE)

        mock_neutron = mock.MagicMock()
        mock_neutron.network.ports.return_value = [bm_port]
        self.agent._get_neutron_client.return_value = mock_neutron

        # Setup hashring with multiple agents, but find a network that
        # our test agent doesn't manage
        other_agent_id = 'other-agent-id'
        self.agent.member_manager.hashring = hashring.HashRing(
            [self.agent.agent_id, other_agent_id])

        # Verify that net-1 is NOT managed by our agent
        # (if by chance it is, the test setup is invalid)
        responsible_agents = self.agent.member_manager.hashring[
            'net-1'.encode('utf-8')]
        self.assertNotIn(self.agent.agent_id, responsible_agents,
                         "Test setup error: net-1 should not be managed "
                         "by test agent")

        mock_ovn_nb = mock.MagicMock()
        mock_get_ovn_nb.return_value = mock_ovn_nb

        # Execute
        self.agent._reconcile_ha_chassis_group_alignment()

        # Verify - should not query for router ports (only baremetal ports)
        self.assertEqual(1, mock_neutron.network.ports.call_count)

    @mock.patch('networking_baremetal.agent.ovn_client.get_ovn_nb_idl',
                autospec=True)
    def test_reconcile_with_time_window(self, mock_get_ovn_nb):
        """Test reconciliation respects time window filtering."""
        # Enable time window filtering
        CONF.set_override(
            'limit_ha_chassis_group_alignment_to_recent_changes_only',
            True, group='baremetal_agent')
        CONF.set_override('ha_chassis_group_alignment_window', 600,
                          group='baremetal_agent')

        # Setup ports - one recent, one old
        now = timeutils.utcnow()
        recent_port = FakePort(
            'bm-port-recent', 'net-1', constants.BAREMETAL_NONE,
            updated_at=now.isoformat())
        old_dt = now - datetime.timedelta(seconds=700)
        old_port = FakePort(
            'bm-port-old', 'net-2', constants.BAREMETAL_NONE,
            updated_at=old_dt.isoformat())

        mock_neutron = mock.MagicMock()
        mock_neutron.network.ports.return_value = [recent_port, old_port]
        self.agent._get_neutron_client.return_value = mock_neutron

        mock_ovn_nb = mock.MagicMock()
        lsp = FakeLogicalSwitchPort('neutron-bm-port-recent', 'ha-group-1')
        mock_ovn_nb.lsp_get.return_value = FakeOVNCommand(lsp)
        mock_get_ovn_nb.return_value = mock_ovn_nb

        # Execute
        self.agent._reconcile_ha_chassis_group_alignment()

        # Verify - should only process recent port (net-1), old port (net-2)
        # should be filtered out by time window
        # We verify this indirectly by checking OVN queries
        # Note: OVN prefixes port names with "neutron-"
        mock_ovn_nb.lsp_get.assert_called_once_with('neutron-bm-port-recent')

    @mock.patch('networking_baremetal.agent.ovn_client.get_ovn_nb_idl',
                autospec=True)
    def test_reconcile_lock_already_held(self, mock_get_ovn_nb):
        """Test reconciliation skips when lock is held."""
        # Lock is already held
        self.agent._ha_alignment_lock.acquire.return_value = False

        mock_neutron = mock.MagicMock()
        self.agent._get_neutron_client.return_value = mock_neutron

        # Execute
        self.agent._reconcile_ha_chassis_group_alignment()

        # Verify - should not query Neutron at all
        mock_neutron.network.ports.assert_not_called()

    @mock.patch('networking_baremetal.agent.ovn_client.get_ovn_nb_idl',
                autospec=True)
    def test_reconcile_ovn_connection_failure(self, mock_get_ovn_nb):
        """Test reconciliation handles OVN connection failure."""
        mock_get_ovn_nb.side_effect = RuntimeError("Connection failed")

        # Execute - should not raise
        self.agent._reconcile_ha_chassis_group_alignment()

        # Verify lock is released
        self.agent._ha_alignment_lock.release.assert_called_once()

    def test_align_network_no_ha_group_on_bm_port(self):
        """Test alignment skips when baremetal port has no HA group."""
        bm_port = FakePort('bm-port-1', 'net-1', constants.BAREMETAL_NONE)

        mock_neutron = mock.MagicMock()
        mock_ovn_nb = mock.MagicMock()

        # Baremetal port has no HA chassis group
        lsp = FakeLogicalSwitchPort('neutron-bm-port-1', None)
        mock_ovn_nb.lsp_get.return_value = FakeOVNCommand(lsp)

        # Execute
        self.agent._align_ha_chassis_group_for_network(
            'net-1', [bm_port], mock_neutron, mock_ovn_nb)

        # Verify - should not query for router ports
        mock_neutron.network.ports.assert_not_called()

    def test_align_network_no_router_ports(self):
        """Test alignment when network has no router ports."""
        bm_port = FakePort('bm-port-1', 'net-1', constants.BAREMETAL_NONE)

        mock_neutron = mock.MagicMock()
        mock_neutron.network.ports.return_value = []  # No router ports

        mock_ovn_nb = mock.MagicMock()
        lsp = FakeLogicalSwitchPort('neutron-bm-port-1', 'ha-group-1')
        mock_ovn_nb.lsp_get.return_value = FakeOVNCommand(lsp)

        # Execute
        self.agent._align_ha_chassis_group_for_network(
            'net-1', [bm_port], mock_neutron, mock_ovn_nb)

        # Verify - should query for router ports but not update anything
        mock_neutron.network.ports.assert_called_once_with(
            network_id='net-1',
            device_owner=n_const.DEVICE_OWNER_ROUTER_INTF)
        mock_ovn_nb.lrp_set_ha_chassis_group.assert_not_called()

    def test_align_network_router_port_not_in_ovn(self):
        """Test alignment when router port not found in OVN."""
        bm_port = FakePort('bm-port-1', 'net-1', constants.BAREMETAL_NONE)
        router_port = FakePort('router-port-1', 'net-1',
                               n_const.DEVICE_OWNER_ROUTER_INTF)

        mock_neutron = mock.MagicMock()
        mock_neutron.network.ports.return_value = [router_port]

        mock_ovn_nb = mock.MagicMock()
        lsp = FakeLogicalSwitchPort('neutron-bm-port-1', 'ha-group-1')
        mock_ovn_nb.lsp_get.return_value = FakeOVNCommand(lsp)
        # Router port not found in OVN
        mock_ovn_nb.lrp_get.return_value = FakeOVNCommand(None)

        # Execute
        self.agent._align_ha_chassis_group_for_network(
            'net-1', [bm_port], mock_neutron, mock_ovn_nb)

        # Verify - should not try to update
        mock_ovn_nb.lrp_set_ha_chassis_group.assert_not_called()

    def test_align_network_handles_exceptions(self):
        """Test alignment handles exceptions gracefully."""
        bm_port = FakePort('bm-port-1', 'net-1', constants.BAREMETAL_NONE)
        router_port = FakePort('router-port-1', 'net-1',
                               n_const.DEVICE_OWNER_ROUTER_INTF)

        mock_neutron = mock.MagicMock()
        mock_neutron.network.ports.return_value = [router_port]

        mock_ovn_nb = mock.MagicMock()
        lsp = FakeLogicalSwitchPort('neutron-bm-port-1', 'ha-group-1')
        mock_ovn_nb.lsp_get.return_value = FakeOVNCommand(lsp)

        # Simulate error when updating
        lrp = FakeLogicalRouterPort('lrp-router-port-1', 'ha-group-2')
        mock_ovn_nb.lrp_get.return_value = FakeOVNCommand(lrp)
        mock_ovn_nb.lrp_set_ha_chassis_group.side_effect = RuntimeError(
            "Update failed")

        # Execute - should not raise
        self.agent._align_ha_chassis_group_for_network(
            'net-1', [bm_port], mock_neutron, mock_ovn_nb)

        # Verify - attempted to update
        mock_ovn_nb.lrp_set_ha_chassis_group.assert_called_once()

    def test_align_network_continues_after_missing_ports(self):
        """Test alignment continues when some BM ports missing from OVN.

        This tests LP#2144061 - when some baremetal ports don't exist in
        OVN (RowNotFound), the reconciliation should continue checking
        other ports rather than short-circuiting.
        """
        from ovsdbapp.backend.ovs_idl import idlutils

        # Create multiple baremetal ports
        bm_port_1 = FakePort('bm-port-1', 'net-1',
                             constants.BAREMETAL_NONE)
        bm_port_2 = FakePort('bm-port-2', 'net-1',
                             constants.BAREMETAL_NONE)
        router_port = FakePort('router-port-1', 'net-1',
                               n_const.DEVICE_OWNER_ROUTER_INTF)

        mock_neutron = mock.MagicMock()
        mock_neutron.network.ports.return_value = [router_port]

        mock_ovn_nb = mock.MagicMock()

        # First port lookup fails (port missing from OVN)
        # Second port lookup succeeds and has HA chassis group
        # Note: OVN prefixes port names with "neutron-"
        def lsp_get_side_effect(port_name):
            if port_name == 'neutron-bm-port-1':
                # Simulate RowNotFound for missing port
                raise idlutils.RowNotFound(table='Logical_Switch_Port',
                                           col='name', match=port_name)
            elif port_name == 'neutron-bm-port-2':
                # Second port exists and has HA chassis group
                lsp = FakeLogicalSwitchPort('neutron-bm-port-2',
                                            'ha-group-1')
                return FakeOVNCommand(lsp)

        mock_ovn_nb.lsp_get.side_effect = lsp_get_side_effect

        # Router port needs alignment
        lrp = FakeLogicalRouterPort('lrp-router-port-1', 'ha-group-2')
        mock_ovn_nb.lrp_get.return_value = FakeOVNCommand(lrp)
        mock_ovn_nb.lrp_set_ha_chassis_group.return_value = FakeOVNCommand(
            None)

        # Execute - should not raise
        self.agent._align_ha_chassis_group_for_network(
            'net-1', [bm_port_1, bm_port_2], mock_neutron, mock_ovn_nb)

        # Verify - should have found HA chassis group from second port
        # and updated router port
        mock_ovn_nb.lrp_set_ha_chassis_group.assert_called_once_with(
            'lrp-router-port-1', 'ha-group-1')

    def test_align_network_skips_when_all_ports_missing(self):
        """Test alignment skips when all BM ports missing from OVN."""
        from ovsdbapp.backend.ovs_idl import idlutils

        bm_port = FakePort('bm-port-1', 'net-1', constants.BAREMETAL_NONE)

        mock_neutron = mock.MagicMock()
        mock_ovn_nb = mock.MagicMock()

        # Port lookup fails - port missing from OVN
        # Note: OVN prefixes port names with "neutron-"
        mock_ovn_nb.lsp_get.side_effect = idlutils.RowNotFound(
            table='Logical_Switch_Port', col='name',
            match='neutron-bm-port-1')

        # Execute
        self.agent._align_ha_chassis_group_for_network(
            'net-1', [bm_port], mock_neutron, mock_ovn_nb)

        # Verify - should not query for router ports since we couldn't
        # find any baremetal ports in OVN
        mock_neutron.network.ports.assert_not_called()


class TestBaremetalAgentConfig(tests_base.BaseTestCase):
    """Test cases for baremetal agent configuration options."""

    def setUp(self):
        super(TestBaremetalAgentConfig, self).setUp()
        # Register options for testing
        agent_config.register_baremetal_agent_opts(CONF)

    def test_register_baremetal_agent_opts(self):
        """Test baremetal agent options are registered correctly."""
        self.assertIn('baremetal_agent', CONF)

    def test_enable_ha_chassis_group_alignment_default(self):
        """Test enable_ha_chassis_group_alignment default value."""
        self.assertTrue(CONF.baremetal_agent
                        .enable_ha_chassis_group_alignment)

    def test_ha_chassis_group_alignment_interval_default(self):
        """Test ha_chassis_group_alignment_interval default value."""
        self.assertEqual(600, CONF.baremetal_agent
                         .ha_chassis_group_alignment_interval)

    def test_ha_chassis_group_alignment_interval_minimum(self):
        """Test ha_chassis_group_alignment_interval respects minimum."""
        # Should raise error if set below minimum (min is 60)
        self.assertRaises(ValueError,
                          CONF.set_override,
                          'ha_chassis_group_alignment_interval', 30,
                          group='baremetal_agent')

    def test_limit_ha_alignment_to_recent_changes_default(self):
        """Test limit_ha_alignment_to_recent_changes_only default."""
        self.assertTrue(
            CONF.baremetal_agent
            .limit_ha_chassis_group_alignment_to_recent_changes_only)

    def test_ha_chassis_group_alignment_window_default(self):
        """Test ha_chassis_group_alignment_window default value."""
        self.assertEqual(1200, CONF.baremetal_agent
                         .ha_chassis_group_alignment_window)

    def test_list_opts(self):
        """Test list_opts returns correct format."""
        opts = agent_config.list_opts()
        self.assertIsInstance(opts, list)
        self.assertEqual(2, len(opts))

        # Verify l2vni group
        group_name, options = opts[0]
        self.assertEqual('l2vni', group_name)
        self.assertEqual(agent_config.L2VNI_OPTS, options)

        # Verify baremetal_agent group
        group_name, options = opts[1]
        self.assertEqual('baremetal_agent', group_name)
        self.assertEqual(agent_config.BAREMETAL_AGENT_OPTS, options)

    def test_all_options_have_help_text(self):
        """Test all configuration options have help text."""
        for opt in agent_config.BAREMETAL_AGENT_OPTS:
            self.assertIsNotNone(opt.help)
            self.assertGreater(len(opt.help), 0)

    def test_boolean_options_have_defaults(self):
        """Test boolean options have explicit default values."""
        boolean_opts = [opt for opt in agent_config.BAREMETAL_AGENT_OPTS
                        if isinstance(opt, cfg.BoolOpt)]
        for opt in boolean_opts:
            self.assertIsNotNone(opt.default)
