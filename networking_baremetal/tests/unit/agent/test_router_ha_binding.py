# Copyright (c) 2026 Red Hat, Inc.
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

"""Unit tests for Router HA Binding Manager."""

from unittest import mock

from neutron.common.ovn import constants as ovn_const
from neutron.tests import base as tests_base
from neutron_lib import constants as n_const
from openstack import exceptions as sdk_exc
from ovsdbapp.backend.ovs_idl import idlutils
from ovsdbapp import exceptions as ovs_exc
from tooz import hashring

from networking_baremetal.agent import router_ha_binding


class FakePort:
    """Fake Neutron Port object."""

    def __init__(self, port_id, device_owner=n_const.DEVICE_OWNER_ROUTER_INTF):
        self.id = port_id
        self.device_owner = device_owner


class FakeLogicalRouterPort:
    """Fake OVN Logical Router Port object."""

    def __init__(self, name, ha_chassis_group=None):
        self.name = name
        if ha_chassis_group is None:
            self.ha_chassis_group = []
        elif isinstance(ha_chassis_group, list):
            self.ha_chassis_group = ha_chassis_group
        else:
            self.ha_chassis_group = [ha_chassis_group]


class FakeHAChassisGroup:
    """Fake OVN HA_Chassis_Group object."""

    def __init__(self, uuid, external_ids=None):
        self.uuid = uuid
        self.external_ids = external_ids or {}


class TestRouterHABindingManager(tests_base.BaseTestCase):
    """Test cases for RouterHABindingManager."""

    def setUp(self):
        super(TestRouterHABindingManager, self).setUp()

        self.mock_neutron = mock.Mock()
        self.mock_ovn_nb = mock.Mock()
        self.mock_member_manager = mock.Mock()
        self.agent_id = 'test-agent-id'

        # Setup hash ring
        self.mock_hashring = hashring.HashRing([self.agent_id])
        self.mock_member_manager.hashring = self.mock_hashring

        # Setup OVN tables structure
        self.mock_ovn_nb.tables = {
            'HA_Chassis_Group': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
        }

        self.manager = router_ha_binding.RouterHABindingManager(
            neutron_client=self.mock_neutron,
            ovn_nb_idl=self.mock_ovn_nb,
            member_manager=self.mock_member_manager,
            agent_id=self.agent_id
        )

    def test_initialize(self):
        """Test manager initialization."""
        self.assertEqual(self.manager.neutron_client, self.mock_neutron)
        self.assertEqual(self.manager.ovn_nb_idl, self.mock_ovn_nb)
        self.assertEqual(self.manager.member_manager, self.mock_member_manager)
        self.assertEqual(self.manager.agent_id, self.agent_id)

    def test_should_manage_network_owned_by_agent(self):
        """Test _should_manage_network returns True for owned network."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

        result = self.manager._should_manage_network(network_id)

        self.assertTrue(result)

    def test_should_manage_network_not_owned_by_agent(self):
        """Test _should_manage_network returns False for non-owned network."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

        # Create hashring with different agent
        other_hashring = hashring.HashRing(['other-agent-id'])
        self.mock_member_manager.hashring = other_hashring

        result = self.manager._should_manage_network(network_id)

        self.assertFalse(result)

    def test_should_manage_network_handles_hash_ring_error(self):
        """Test _should_manage_network handles hash ring lookup errors."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

        # Simulate hash ring error
        self.mock_member_manager.hashring = None

        result = self.manager._should_manage_network(network_id)

        self.assertFalse(result)

    def test_get_router_interface_ports_success(self):
        """Test _get_router_interface_ports returns router ports."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        port1 = FakePort('port-1')
        port2 = FakePort('port-2')

        self.mock_neutron.network.ports.return_value = iter([port1, port2])

        result = self.manager._get_router_interface_ports(network_id)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].id, 'port-1')
        self.assertEqual(result[1].id, 'port-2')
        self.mock_neutron.network.ports.assert_called_once_with(
            network_id=network_id,
            device_owner=n_const.DEVICE_OWNER_ROUTER_INTF
        )

    def test_get_router_interface_ports_empty(self):
        """Test _get_router_interface_ports returns empty list."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

        self.mock_neutron.network.ports.return_value = iter([])

        result = self.manager._get_router_interface_ports(network_id)

        self.assertEqual(len(result), 0)

    def test_get_router_interface_ports_handles_exception(self):
        """Test _get_router_interface_ports handles SDK exceptions."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

        self.mock_neutron.network.ports.side_effect = \
            sdk_exc.OpenStackCloudException("API error")

        with self.assertRaises(sdk_exc.OpenStackCloudException):
            self.manager._get_router_interface_ports(network_id)

    def test_get_current_ha_chassis_group_with_list(self):
        """Test _get_current_ha_chassis_group with list value."""
        lrp = FakeLogicalRouterPort(
            'lrp-test', ha_chassis_group=['ha-group-1'])

        result = self.manager._get_current_ha_chassis_group(lrp)

        self.assertEqual(result, 'ha-group-1')

    def test_get_current_ha_chassis_group_with_single_value(self):
        """Test _get_current_ha_chassis_group with single value."""
        lrp = FakeLogicalRouterPort(
            'lrp-test', ha_chassis_group='ha-group-1')

        result = self.manager._get_current_ha_chassis_group(lrp)

        self.assertEqual(result, 'ha-group-1')

    def test_get_current_ha_chassis_group_empty(self):
        """Test _get_current_ha_chassis_group with empty list."""
        lrp = FakeLogicalRouterPort('lrp-test', ha_chassis_group=[])

        result = self.manager._get_current_ha_chassis_group(lrp)

        self.assertIsNone(result)

    def test_get_current_ha_chassis_group_no_attribute(self):
        """Test _get_current_ha_chassis_group without ha_chassis_group attr."""
        lrp = mock.Mock(spec=['name'])
        lrp.name = 'lrp-test'

        result = self.manager._get_current_ha_chassis_group(lrp)

        self.assertIsNone(result)

    def test_update_lrp_ha_chassis_group_success(self):
        """Test _update_lrp_ha_chassis_group updates LRP successfully."""
        port_id = 'port-1'
        ha_chassis_group = 'ha-group-1'
        network_id = 'network-1'

        # Mock LRP with no HA chassis group
        lrp = FakeLogicalRouterPort('lrp-port-1', ha_chassis_group=[])
        mock_lrp_get = mock.Mock()
        mock_lrp_get.execute.return_value = lrp
        self.mock_ovn_nb.lrp_get.return_value = mock_lrp_get

        # Mock lrp_set_ha_chassis_group
        mock_set_ha = mock.Mock()
        mock_set_ha.execute.return_value = None
        self.mock_ovn_nb.lrp_set_ha_chassis_group.return_value = mock_set_ha

        result = self.manager._update_lrp_ha_chassis_group(
            port_id, ha_chassis_group, network_id)

        self.assertTrue(result)
        self.mock_ovn_nb.lrp_get.assert_called_once_with('lrp-port-1')
        self.mock_ovn_nb.lrp_set_ha_chassis_group.assert_called_once_with(
            'lrp-port-1', ha_chassis_group)

    def test_update_lrp_ha_chassis_group_already_correct(self):
        """Test _update_lrp_ha_chassis_group skips if already correct."""
        port_id = 'port-1'
        ha_chassis_group = 'ha-group-1'
        network_id = 'network-1'

        lrp = FakeLogicalRouterPort(
            'lrp-port-1', ha_chassis_group=['ha-group-1'])
        mock_lrp_get = mock.Mock()
        mock_lrp_get.execute.return_value = lrp
        self.mock_ovn_nb.lrp_get.return_value = mock_lrp_get

        result = self.manager._update_lrp_ha_chassis_group(
            port_id, ha_chassis_group, network_id)

        self.assertFalse(result)
        self.mock_ovn_nb.lrp_get.assert_called_once_with('lrp-port-1')
        self.mock_ovn_nb.lrp_set_ha_chassis_group.assert_not_called()

    def test_update_lrp_ha_chassis_group_not_found(self):
        """Test _update_lrp_ha_chassis_group handles LRP not found."""
        port_id = 'port-1'
        ha_chassis_group = 'ha-group-1'
        network_id = 'network-1'

        # Mock LRP not found
        mock_lrp_get = mock.Mock()
        mock_lrp_get.execute.side_effect = idlutils.RowNotFound(
            table='Logical_Router_Port', col='name', match='lrp-port-1')
        self.mock_ovn_nb.lrp_get.return_value = mock_lrp_get

        result = self.manager._update_lrp_ha_chassis_group(
            port_id, ha_chassis_group, network_id)

        self.assertFalse(result)
        self.mock_ovn_nb.lrp_set_ha_chassis_group.assert_not_called()

    def test_update_lrp_ha_chassis_group_updates_from_different_group(self):
        """Test _update_lrp_ha_chassis_group updates from different group."""
        port_id = 'port-1'
        ha_chassis_group = 'ha-group-2'
        network_id = 'network-1'

        lrp = FakeLogicalRouterPort(
            'lrp-port-1', ha_chassis_group=['ha-group-1'])
        mock_lrp_get = mock.Mock()
        mock_lrp_get.execute.return_value = lrp
        self.mock_ovn_nb.lrp_get.return_value = mock_lrp_get

        # Mock lrp_set_ha_chassis_group
        mock_set_ha = mock.Mock()
        mock_set_ha.execute.return_value = None
        self.mock_ovn_nb.lrp_set_ha_chassis_group.return_value = mock_set_ha

        result = self.manager._update_lrp_ha_chassis_group(
            port_id, ha_chassis_group, network_id)

        self.assertTrue(result)
        self.mock_ovn_nb.lrp_set_ha_chassis_group.assert_called_once_with(
            'lrp-port-1', ha_chassis_group)

    def test_bind_lrp_to_ha_group_success(self):
        """Test _bind_lrp_to_ha_group calls update method."""
        port_id = 'port-1'
        ha_chassis_group = 'ha-group-1'
        network_id = 'network-1'

        with mock.patch.object(
                self.manager, '_update_lrp_ha_chassis_group',
                autospec=True, return_value=True) as mock_update:
            self.manager._bind_lrp_to_ha_group(
                port_id, ha_chassis_group, network_id)

            mock_update.assert_called_once_with(
                port_id, ha_chassis_group, network_id)

    def test_bind_lrp_to_ha_group_handles_ovsdb_exception(self):
        """Test _bind_lrp_to_ha_group handles OvsdbAppException."""
        port_id = 'port-1'
        ha_chassis_group = 'ha-group-1'
        network_id = 'network-1'

        with mock.patch.object(
                self.manager, '_update_lrp_ha_chassis_group',
                autospec=True,
                side_effect=ovs_exc.OvsdbAppException()):
            with self.assertRaises(ovs_exc.OvsdbAppException):
                self.manager._bind_lrp_to_ha_group(
                    port_id, ha_chassis_group, network_id)

    def test_bind_lrp_to_ha_group_handles_runtime_error(self):
        """Test _bind_lrp_to_ha_group handles RuntimeError."""
        port_id = 'port-1'
        ha_chassis_group = 'ha-group-1'
        network_id = 'network-1'

        with mock.patch.object(
                self.manager, '_update_lrp_ha_chassis_group',
                autospec=True,
                side_effect=RuntimeError("Runtime error")):
            with self.assertRaises(RuntimeError):
                self.manager._bind_lrp_to_ha_group(
                    port_id, ha_chassis_group, network_id)

    def test_bind_lrp_to_ha_group_handles_attribute_error(self):
        """Test _bind_lrp_to_ha_group handles AttributeError."""
        port_id = 'port-1'
        ha_chassis_group = 'ha-group-1'
        network_id = 'network-1'

        with mock.patch.object(
                self.manager, '_update_lrp_ha_chassis_group',
                autospec=True,
                side_effect=AttributeError("Attribute error")):
            with self.assertRaises(AttributeError):
                self.manager._bind_lrp_to_ha_group(
                    port_id, ha_chassis_group, network_id)

    def test_bind_router_interfaces_for_network_success(self):
        """Test bind_router_interfaces_for_network happy path."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        ha_chassis_group = 'ha-group-1'

        port1 = FakePort('port-1')
        port2 = FakePort('port-2')

        with mock.patch.object(
                self.manager, '_should_manage_network',
                autospec=True, return_value=True):
            with mock.patch.object(
                    self.manager, '_get_router_interface_ports',
                    autospec=True, return_value=[port1, port2]):
                with mock.patch.object(
                        self.manager, '_bind_lrp_to_ha_group',
                        autospec=True) as mock_bind:
                    self.manager.bind_router_interfaces_for_network(
                        network_id, ha_chassis_group)

                    self.assertEqual(mock_bind.call_count, 2)
                    mock_bind.assert_any_call('port-1', ha_chassis_group,
                                              network_id)
                    mock_bind.assert_any_call('port-2', ha_chassis_group,
                                              network_id)

    def test_bind_router_interfaces_for_network_not_managed(self):
        """Test skips non-managed network."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        ha_chassis_group = 'ha-group-1'

        with mock.patch.object(
                self.manager, '_should_manage_network',
                autospec=True, return_value=False):
            with mock.patch.object(
                    self.manager, '_get_router_interface_ports',
                    autospec=True) as mock_get_ports:
                self.manager.bind_router_interfaces_for_network(
                    network_id, ha_chassis_group)

                mock_get_ports.assert_not_called()

    def test_bind_router_interfaces_for_network_no_router_ports(self):
        """Test bind_router_interfaces_for_network with no router ports."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        ha_chassis_group = 'ha-group-1'

        with mock.patch.object(
                self.manager, '_should_manage_network',
                autospec=True, return_value=True):
            with mock.patch.object(
                    self.manager, '_get_router_interface_ports',
                    autospec=True, return_value=[]):
                with mock.patch.object(
                        self.manager, '_bind_lrp_to_ha_group',
                        autospec=True) as mock_bind:
                    self.manager.bind_router_interfaces_for_network(
                        network_id, ha_chassis_group)

                    mock_bind.assert_not_called()

    def test_bind_router_interfaces_for_network_handles_port_bind_error(
            self):
        """Test handles port bind errors."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        ha_chassis_group = 'ha-group-1'

        port1 = FakePort('port-1')
        port2 = FakePort('port-2')

        with mock.patch.object(
                self.manager, '_should_manage_network',
                autospec=True, return_value=True):
            with mock.patch.object(
                    self.manager, '_get_router_interface_ports',
                    autospec=True, return_value=[port1, port2]):
                with mock.patch.object(
                        self.manager, '_bind_lrp_to_ha_group',
                        autospec=True) as mock_bind:
                    mock_bind.side_effect = [
                        ovs_exc.OvsdbAppException(),
                        None
                    ]

                    self.manager.bind_router_interfaces_for_network(
                        network_id, ha_chassis_group)

                    self.assertEqual(mock_bind.call_count, 2)

    def test_bind_router_interfaces_for_network_handles_query_error(self):
        """Test handles query errors."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        ha_chassis_group = 'ha-group-1'

        with mock.patch.object(
                self.manager, '_should_manage_network',
                autospec=True, return_value=True):
            with mock.patch.object(
                    self.manager, '_get_router_interface_ports',
                    autospec=True,
                    side_effect=sdk_exc.OpenStackCloudException(
                        "API error")):
                self.manager.bind_router_interfaces_for_network(
                    network_id, ha_chassis_group)

    def test_get_networks_with_ha_chassis_groups_success(self):
        """Test _get_networks_with_ha_chassis_groups returns groups."""
        network1_id = 'network-1'
        network2_id = 'network-2'

        ha_group1 = FakeHAChassisGroup(
            'ha-group-1',
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network1_id}
        )
        ha_group2 = FakeHAChassisGroup(
            'ha-group-2',
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network2_id}
        )

        table = self.mock_ovn_nb.tables['HA_Chassis_Group']
        table.rows.values.return_value = [ha_group1, ha_group2]

        result = self.manager._get_networks_with_ha_chassis_groups()

        self.assertEqual(len(result), 2)
        self.assertEqual(result[network1_id], 'ha-group-1')
        self.assertEqual(result[network2_id], 'ha-group-2')

    def test_get_networks_with_ha_chassis_groups_accepts_unified_groups(
            self):
        """Test accepts unified HA groups (network + router).

        When both network-level and unified groups exist for the same network,
        the last one found is used. In unified HA chassis group scenarios,
        the same group is used for both the network and router, which is the
        correct behavior for proper router interface binding.
        """
        network_id = 'network-1'
        router_id = 'router-1'

        network_group = FakeHAChassisGroup(
            'ha-group-network',
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id}
        )
        router_group = FakeHAChassisGroup(
            'ha-group-router',
            external_ids={
                ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id,
                ovn_const.OVN_ROUTER_ID_EXT_ID_KEY: router_id
            }
        )

        table = self.mock_ovn_nb.tables['HA_Chassis_Group']
        table.rows.values.return_value = [network_group, router_group]

        result = self.manager._get_networks_with_ha_chassis_groups()

        self.assertEqual(len(result), 1)
        # The last group found is used (router_group in this case)
        self.assertEqual(result[network_id], 'ha-group-router')

    def test_get_networks_with_ha_chassis_groups_empty(self):
        """Test _get_networks_with_ha_chassis_groups with no groups."""
        table = self.mock_ovn_nb.tables['HA_Chassis_Group']
        table.rows.values.return_value = []

        result = self.manager._get_networks_with_ha_chassis_groups()

        self.assertEqual(len(result), 0)

    def test_get_networks_with_ha_chassis_groups_no_table(self):
        """Test _get_networks_with_ha_chassis_groups when table missing."""
        self.mock_ovn_nb.tables = {}

        result = self.manager._get_networks_with_ha_chassis_groups()

        self.assertEqual(len(result), 0)

    def test_get_networks_with_ha_chassis_groups_no_tables_attr(self):
        """Test without tables attribute."""
        delattr(self.mock_ovn_nb, 'tables')

        result = self.manager._get_networks_with_ha_chassis_groups()

        self.assertEqual(len(result), 0)

    def test_get_networks_with_ha_chassis_groups_handles_row_no_ext_ids(
            self):
        """Test handles rows without external_ids."""
        network_id = 'network-1'

        good_group = FakeHAChassisGroup(
            'ha-group-1',
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id}
        )
        bad_group = mock.Mock(spec=['uuid'])
        bad_group.uuid = 'ha-group-2'

        table = self.mock_ovn_nb.tables['HA_Chassis_Group']
        table.rows.values.return_value = [good_group, bad_group]

        result = self.manager._get_networks_with_ha_chassis_groups()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[network_id], 'ha-group-1')

    def test_reconcile_success(self):
        """Test reconcile processes networks and updates ports."""
        network1_id = 'network-1'
        network2_id = 'network-2'
        ha_group1 = 'ha-group-1'
        ha_group2 = 'ha-group-2'

        port1 = FakePort('port-1')
        port2 = FakePort('port-2')

        with mock.patch.object(
                self.manager, '_get_networks_with_ha_chassis_groups',
                autospec=True,
                return_value={network1_id: ha_group1,
                              network2_id: ha_group2}):
            with mock.patch.object(
                    self.manager, '_should_manage_network',
                    autospec=True, return_value=True):
                with mock.patch.object(
                        self.manager, '_get_router_interface_ports',
                        autospec=True, return_value=[port1, port2]):
                    with mock.patch.object(
                            self.manager,
                            '_update_lrp_ha_chassis_group',
                            autospec=True,
                            return_value=True) as mock_update:
                        self.manager.reconcile()

                        self.assertEqual(mock_update.call_count, 4)

    def test_reconcile_no_networks(self):
        """Test reconcile with no networks."""
        with mock.patch.object(
                self.manager, '_get_networks_with_ha_chassis_groups',
                autospec=True, return_value={}):
            with mock.patch.object(
                    self.manager, '_should_manage_network',
                    autospec=True) as mock_should_manage:
                self.manager.reconcile()

                mock_should_manage.assert_not_called()

    def test_reconcile_skips_non_managed_networks(self):
        """Test reconcile skips networks not managed by agent."""
        network1_id = 'network-1'
        network2_id = 'network-2'
        ha_group1 = 'ha-group-1'
        ha_group2 = 'ha-group-2'

        with mock.patch.object(
                self.manager, '_get_networks_with_ha_chassis_groups',
                autospec=True,
                return_value={network1_id: ha_group1,
                              network2_id: ha_group2}):
            with mock.patch.object(
                    self.manager, '_should_manage_network',
                    autospec=True,
                    side_effect=lambda nid: nid == network1_id):
                with mock.patch.object(
                        self.manager, '_get_router_interface_ports',
                        autospec=True,
                        return_value=[FakePort('port-1')]) as mock_get_ports:
                    with mock.patch.object(
                            self.manager,
                            '_update_lrp_ha_chassis_group',
                            autospec=True, return_value=True):
                        self.manager.reconcile()

                        mock_get_ports.assert_called_once_with(network1_id)

    def test_reconcile_skips_networks_without_router_ports(self):
        """Test reconcile skips networks without router ports."""
        network_id = 'network-1'
        ha_group = 'ha-group-1'

        with mock.patch.object(
                self.manager, '_get_networks_with_ha_chassis_groups',
                autospec=True, return_value={network_id: ha_group}):
            with mock.patch.object(
                    self.manager, '_should_manage_network',
                    autospec=True, return_value=True):
                with mock.patch.object(
                        self.manager, '_get_router_interface_ports',
                        autospec=True, return_value=[]):
                    with mock.patch.object(
                            self.manager,
                            '_update_lrp_ha_chassis_group',
                            autospec=True) as mock_update:
                        self.manager.reconcile()

                        mock_update.assert_not_called()

    def test_reconcile_handles_port_update_errors(self):
        """Test reconcile continues after port update errors."""
        network_id = 'network-1'
        ha_group = 'ha-group-1'

        port1 = FakePort('port-1')
        port2 = FakePort('port-2')

        with mock.patch.object(
                self.manager, '_get_networks_with_ha_chassis_groups',
                autospec=True, return_value={network_id: ha_group}):
            with mock.patch.object(
                    self.manager, '_should_manage_network',
                    autospec=True, return_value=True):
                with mock.patch.object(
                        self.manager, '_get_router_interface_ports',
                        autospec=True, return_value=[port1, port2]):
                    with mock.patch.object(
                            self.manager,
                            '_update_lrp_ha_chassis_group',
                            autospec=True) as mock_update:
                        mock_update.side_effect = [
                            ovs_exc.OvsdbAppException(),
                            True
                        ]

                        self.manager.reconcile()

                        self.assertEqual(mock_update.call_count, 2)

    def test_reconcile_handles_query_errors(self):
        """Test reconcile handles Neutron query errors."""
        network_id = 'network-1'
        ha_group = 'ha-group-1'

        with mock.patch.object(
                self.manager, '_get_networks_with_ha_chassis_groups',
                autospec=True, return_value={network_id: ha_group}):
            with mock.patch.object(
                    self.manager, '_should_manage_network',
                    autospec=True, return_value=True):
                with mock.patch.object(
                        self.manager, '_get_router_interface_ports',
                        autospec=True,
                        side_effect=sdk_exc.OpenStackCloudException(
                            "API error")):
                    self.manager.reconcile()

    def test_reconcile_handles_general_exception(self):
        """Test reconcile handles general exceptions."""
        with mock.patch.object(
                self.manager, '_get_networks_with_ha_chassis_groups',
                autospec=True,
                side_effect=Exception("Unexpected error")):
            self.manager.reconcile()

    def test_reconcile_counts_updated_ports(self):
        """Test reconcile correctly counts updated ports."""
        network_id = 'network-1'
        ha_group = 'ha-group-1'

        port1 = FakePort('port-1')
        port2 = FakePort('port-2')
        port3 = FakePort('port-3')

        with mock.patch.object(
                self.manager, '_get_networks_with_ha_chassis_groups',
                autospec=True, return_value={network_id: ha_group}):
            with mock.patch.object(
                    self.manager, '_should_manage_network',
                    autospec=True, return_value=True):
                with mock.patch.object(
                        self.manager, '_get_router_interface_ports',
                        autospec=True,
                        return_value=[port1, port2, port3]):
                    with mock.patch.object(
                            self.manager,
                            '_update_lrp_ha_chassis_group',
                            autospec=True) as mock_update:
                        mock_update.side_effect = [True, False, True]

                        self.manager.reconcile()

                        self.assertEqual(mock_update.call_count, 3)


class TestHAChassisGroupNetworkEvent(tests_base.BaseTestCase):
    """Test cases for HAChassisGroupNetworkEvent."""

    def setUp(self):
        super(TestHAChassisGroupNetworkEvent, self).setUp()

        # Create mock agent with required attributes
        self.mock_agent = mock.MagicMock()
        self.mock_agent.agent_id = 'test-agent-id'

        # Create mock member manager with hash ring
        self.mock_member_manager = mock.MagicMock()
        self.mock_hashring = hashring.HashRing(['test-agent-id'])
        self.mock_member_manager.hashring = self.mock_hashring
        self.mock_agent.member_manager = self.mock_member_manager

        # Create mock router HA binding manager
        self.mock_router_ha_binding = mock.Mock()
        self.mock_agent.router_ha_binding = self.mock_router_ha_binding

        # Create event instance
        from networking_baremetal.agent.ovn_events import \
            HAChassisGroupNetworkEvent
        self.event = HAChassisGroupNetworkEvent(self.mock_agent)

    def _create_mock_row(self, **kwargs):
        """Helper to create a mock HA_Chassis_Group row."""
        row = mock.MagicMock()
        row._table.name = 'HA_Chassis_Group'
        for key, value in kwargs.items():
            setattr(row, key, value)
        return row

    def test_event_initialization(self):
        """Test HAChassisGroupNetworkEvent initialization."""
        self.assertEqual(self.event.agent, self.mock_agent)
        self.assertEqual(self.event.agent_id, 'test-agent-id')
        self.assertEqual(self.event.hashring, self.mock_hashring)
        self.assertEqual(self.event.event_name, 'HAChassisGroupNetworkEvent')

        # Verify event is watching CREATE and UPDATE on HA_Chassis_Group
        from ovsdbapp.backend.ovs_idl import event as row_event
        self.assertIn(row_event.RowEvent.ROW_CREATE, self.event.events)
        self.assertIn(row_event.RowEvent.ROW_UPDATE, self.event.events)
        self.assertEqual(self.event.table, 'HA_Chassis_Group')

    def test_event_inherits_from_base_event(self):
        """Test HAChassisGroupNetworkEvent inherits from BaseEvent."""
        from neutron.plugins.ml2.drivers.ovn.mech_driver.ovsdb import \
            ovsdb_monitor
        from ovsdbapp.backend.ovs_idl import event as row_event
        self.assertIsInstance(self.event, ovsdb_monitor.BaseEvent)
        self.assertIsInstance(self.event, row_event.RowEvent)

    def test_match_fn_network_level_group_owned_by_agent(self):
        """Test match_fn returns True for network-level group."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            uuid='ha-group-1',
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id}
        )

        from ovsdbapp.backend.ovs_idl import event as row_event
        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertTrue(result)

    def test_match_fn_accepts_unified_ha_group(self):
        """Test match_fn accepts unified HA groups (network + router)."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        router_id = 'router-1'
        row = self._create_mock_row(
            uuid='ha-group-1',
            external_ids={
                ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id,
                ovn_const.OVN_ROUTER_ID_EXT_ID_KEY: router_id
            }
        )

        from ovsdbapp.backend.ovs_idl import event as row_event
        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertTrue(result)

    def test_match_fn_rejects_group_without_network_id(self):
        """Test match_fn rejects groups without network_id."""
        row = self._create_mock_row(
            uuid='ha-group-1',
            external_ids={}
        )

        from ovsdbapp.backend.ovs_idl import event as row_event
        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertFalse(result)

    def test_match_fn_rejects_group_without_external_ids(self):
        """Test match_fn rejects groups without external_ids attribute."""
        row = self._create_mock_row(uuid='ha-group-1')
        del row.external_ids

        from ovsdbapp.backend.ovs_idl import event as row_event
        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertFalse(result)

    def test_match_fn_rejects_group_not_owned_by_agent(self):
        """Test match_fn rejects groups not owned by agent (hash ring)."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            uuid='ha-group-1',
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id}
        )

        # Create hashring with different agent
        other_hashring = hashring.HashRing(['other-agent-id'])
        self.event.hashring = other_hashring

        from ovsdbapp.backend.ovs_idl import event as row_event
        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertFalse(result)

    def test_match_fn_rejects_wrong_table(self):
        """Test match_fn rejects rows from wrong table."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            uuid='ha-group-1',
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id}
        )
        row._table.name = 'Logical_Router_Port'  # Wrong table

        from ovsdbapp.backend.ovs_idl import event as row_event
        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertFalse(result)

    def test_match_fn_accepts_create_events(self):
        """Test match_fn accepts CREATE events."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            uuid='ha-group-1',
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id}
        )

        from ovsdbapp.backend.ovs_idl import event as row_event
        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertTrue(result)

    def test_match_fn_accepts_update_events(self):
        """Test match_fn accepts UPDATE events."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            uuid='ha-group-1',
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id}
        )

        from ovsdbapp.backend.ovs_idl import event as row_event
        result = self.event.matches(row_event.RowEvent.ROW_UPDATE, row)

        self.assertTrue(result)

    def test_match_fn_rejects_delete_events(self):
        """Test match_fn rejects DELETE events."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            uuid='ha-group-1',
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id}
        )

        from ovsdbapp.backend.ovs_idl import event as row_event
        result = self.event.matches(row_event.RowEvent.ROW_DELETE, row)

        self.assertFalse(result)

    def test_run_triggers_router_interface_binding(self):
        """Test run() triggers router interface binding."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        ha_group_uuid = 'ha-group-1'
        row = self._create_mock_row(
            uuid=ha_group_uuid,
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id}
        )

        from ovsdbapp.backend.ovs_idl import event as row_event
        self.event.run(row_event.RowEvent.ROW_CREATE, row, None)

        mock_bind = (self.mock_router_ha_binding.
                     bind_router_interfaces_for_network)
        mock_bind.assert_called_once_with(network_id, ha_group_uuid)

    def test_run_handles_missing_router_ha_binding_manager(self):
        """Test run() handles missing router HA binding manager."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            uuid='ha-group-1',
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id}
        )

        # Remove router_ha_binding attribute
        self.mock_agent.router_ha_binding = None

        from ovsdbapp.backend.ovs_idl import event as row_event
        # Should not raise exception
        self.event.run(row_event.RowEvent.ROW_CREATE, row, None)

    def test_run_handles_missing_router_ha_binding_attribute(self):
        """Test run() handles missing router_ha_binding attribute."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            uuid='ha-group-1',
            external_ids={ovn_const.OVN_NETWORK_ID_EXT_ID_KEY: network_id}
        )

        # Remove router_ha_binding attribute entirely
        delattr(self.mock_agent, 'router_ha_binding')

        from ovsdbapp.backend.ovs_idl import event as row_event
        # Should not raise exception
        self.event.run(row_event.RowEvent.ROW_CREATE, row, None)

    def test_run_handles_attribute_error(self):
        """Test run() handles AttributeError gracefully."""
        row = self._create_mock_row(uuid='ha-group-1')
        # Missing external_ids will cause AttributeError
        del row.external_ids

        from ovsdbapp.backend.ovs_idl import event as row_event
        # Should not raise exception
        self.event.run(row_event.RowEvent.ROW_CREATE, row, None)

    def test_run_handles_key_error(self):
        """Test run() handles KeyError gracefully."""
        row = self._create_mock_row(
            uuid='ha-group-1',
            external_ids={}  # No network_id key
        )

        from ovsdbapp.backend.ovs_idl import event as row_event
        # Should not raise exception (network_id will be None)
        self.event.run(row_event.RowEvent.ROW_CREATE, row, None)
