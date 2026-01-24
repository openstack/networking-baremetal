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

from unittest import mock

from neutron.tests import base as tests_base
from neutron_lib import constants as n_const
from openstack import exceptions as sdkexc
from oslo_config import cfg

from networking_baremetal.agent import agent_config
from networking_baremetal.agent import l2vni_trunk_manager


CONF = cfg.CONF


class FakeHAChassis:
    """Fake OVN HA_Chassis object (member of HA_Chassis_Group)."""

    def __init__(self, chassis_name):
        self.chassis_name = chassis_name


class FakeHAChassisGroup:
    """Fake OVN HA Chassis Group object."""

    def __init__(self, name, chassis_list, uuid=None):
        self.name = name
        self.ha_chassis = chassis_list


class FakeChassis:
    """Fake OVN Chassis object.

    In real OVN, the chassis name IS the system-id (UUID).
    For test backwards compatibility, we accept both parameters
    but use system_id as the name since that's what code expects.
    """

    def __init__(self, name, system_id, external_ids=None):
        # In real OVN, chassis.name IS the system-id
        # Use system_id as the name to match real behavior
        self.name = system_id
        self.external_ids = external_ids or {}
        # Don't store system-id in external_ids - that's not where it is
        # in real OVN (it's the name field)


class FakeLogicalRouterPort:
    """Fake OVN Logical Router Port object."""

    def __init__(self, name, gateway_chassis_list, networks=None):
        self.name = name
        self.gateway_chassis = gateway_chassis_list
        self.networks = networks or []
        self.ha_chassis_group = []


class FakeLogicalSwitchPort:
    """Fake OVN Logical Switch Port object."""

    def __init__(self, name, lsp_type, options=None, external_ids=None):
        self.name = name
        self.type = lsp_type
        self.options = options or {}
        self.external_ids = external_ids or {}


class FakeLogicalSwitch:
    """Fake OVN Logical Switch object."""

    def __init__(self, name, external_ids=None):
        self.name = name
        self.external_ids = external_ids or {}


class FakePort:
    """Fake Neutron Port object."""

    def __init__(self, port_id, device_owner, binding_profile=None,
                 device_id=None):
        self.id = port_id
        self.device_owner = device_owner
        self.binding = {'profile': binding_profile or {}}
        self.device_id = device_id


class FakeTrunk:
    """Fake Neutron Trunk object."""

    def __init__(self, trunk_id, port_id, name='', sub_ports=None):
        self.id = trunk_id
        self.port_id = port_id
        self.name = name
        self.sub_ports = sub_ports or []


class FakeNetwork:
    """Fake Neutron Network object."""

    def __init__(self, network_id, name=''):
        self.id = network_id
        self.name = name


class FakeSegment:
    """Fake Neutron Segment object."""

    def __init__(self, network_id, network_type, segmentation_id,
                 physical_network):
        self.network_id = network_id
        self.network_type = network_type
        self.segmentation_id = segmentation_id
        self.physical_network = physical_network


class FakeIronicPort:
    """Fake Ironic Port object."""

    def __init__(self, node_id, physical_network, local_link_connection):
        self.node_id = node_id
        self.physical_network = physical_network
        self.local_link_connection = local_link_connection


class FakeIronicNode:
    """Fake Ironic Node object."""

    def __init__(self, node_id, system_id):
        self.id = node_id
        self.uuid = node_id
        self.properties = {'system_id': system_id}


class TestL2VNITrunkManager(tests_base.BaseTestCase):
    """Test cases for L2VNI Trunk Manager."""

    def setUp(self):
        super(TestL2VNITrunkManager, self).setUp()

        # Register L2VNI config options
        agent_config.register_l2vni_opts(cfg.CONF)

        self.mock_neutron = mock.Mock()
        self.mock_ovn_nb = mock.Mock()
        self.mock_ovn_sb = mock.Mock()
        self.mock_ironic = mock.Mock()

        # Setup OVN tables structure
        # Tables now use .rows.values() pattern
        self.mock_ovn_nb.tables = {
            'HA_Chassis_Group': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
            'Logical_Router_Port': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
            'Logical_Switch_Port': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
            'Logical_Switch': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
        }

        self.mock_ovn_sb.tables = {
            'Chassis': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
            'Port': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
        }

        # Note: member_manager is None, so _should_manage_chassis returns True
        # for all chassis (single agent mode)
        self.manager = l2vni_trunk_manager.L2VNITrunkManager(
            neutron_client=self.mock_neutron,
            ovn_nb_idl=self.mock_ovn_nb,
            ovn_sb_idl=self.mock_ovn_sb,
            ironic_client=self.mock_ironic,
            member_manager=None,
            agent_id=None
        )

    def test_initialize(self):
        """Test trunk manager initialization."""
        self.assertIsNotNone(self.manager.neutron)
        self.assertIsNotNone(self.manager.ovn_nb_idl)
        self.assertIsNotNone(self.manager.ovn_sb_idl)
        self.assertIsNotNone(self.manager.ironic)

    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_ensure_infrastructure_networks', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_discover_trunks', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_calculate_required_vlans', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_reconcile_subports', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_cleanup_unused_infrastructure', autospec=True)
    def test_reconcile_full_workflow(self, mock_cleanup,
                                     mock_reconcile_subports,
                                     mock_calculate_vlans,
                                     mock_discover_trunks,
                                     mock_ensure_infra):
        """Test full reconciliation workflow."""
        mock_discover_trunks.return_value = {}
        mock_calculate_vlans.return_value = {}

        self.manager.reconcile()

        mock_ensure_infra.assert_called_once()
        mock_discover_trunks.assert_called_once()
        mock_calculate_vlans.assert_called_once()
        mock_reconcile_subports.assert_called_once()
        mock_cleanup.assert_called_once()

    def test_ensure_infrastructure_networks_auto_create_enabled(self):
        """Test infrastructure network creation when auto-create enabled."""
        cfg.CONF.set_override('l2vni_auto_create_networks', True,
                              group='l2vni')

        # Mock chassis and add to SB table
        chassis = FakeChassis('chassis-1', 'system-id-1')
        self.mock_ovn_sb.tables['Chassis'].rows.values.return_value = [
            chassis]

        # Mock HA chassis group with proper structure
        ha_chassis = FakeHAChassis('system-id-1')
        ha_group = FakeHAChassisGroup('ha_group_1', [ha_chassis])
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = [ha_group]

        # Mock network doesn't exist
        self.mock_neutron.network.networks.return_value = []
        self.mock_neutron.network.create_network.return_value = FakeNetwork(
            'network-id-1', 'l2vni-ha-group-ha_group_1')

        self.manager._ensure_infrastructure_networks()

        # Should create ha_chassis_group network
        self.mock_neutron.network.create_network.assert_called()
        # Check that ha_group network was created (might be multiple calls)
        calls = self.mock_neutron.network.create_network.call_args_list
        network_names = [call.kwargs['name'] for call in calls]
        self.assertIn('l2vni-ha-group-ha_group_1', network_names)
        for call in calls:
            if call.kwargs['name'] == 'l2vni-ha-group-ha_group_1':
                self.assertEqual('geneve',
                                 call.kwargs.get('provider_network_type'))

    def test_ensure_infrastructure_networks_auto_create_disabled(self):
        """Test infrastructure network creation when auto-create disabled."""
        cfg.CONF.set_override('l2vni_auto_create_networks', False,
                              group='l2vni')

        # Mock chassis and add to SB table
        chassis = FakeChassis('chassis-1', 'system-id-1')
        self.mock_ovn_sb.tables['Chassis'].rows.values.return_value = [
            chassis]

        # Mock HA chassis group with proper structure
        ha_chassis = FakeHAChassis('system-id-1')
        ha_group = FakeHAChassisGroup('ha_group_1', [ha_chassis])
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = [ha_group]

        self.mock_neutron.network.networks.return_value = []

        self.manager._ensure_infrastructure_networks()

        # Should not create network
        self.mock_neutron.network.create_network.assert_not_called()

    def test_ensure_subport_anchor_network_creates_when_missing(self):
        """Test subport anchor network creation when it doesn't exist."""
        cfg.CONF.set_override('l2vni_auto_create_networks', True,
                              group='l2vni')
        cfg.CONF.set_override('l2vni_subport_anchor_network',
                              'anchor-network',
                              group='l2vni')

        # Mock network doesn't exist
        self.mock_neutron.network.networks.return_value = []
        self.mock_neutron.network.create_network.return_value = FakeNetwork(
            'anchor-net-id', 'anchor-network')

        self.manager._ensure_subport_anchor_network()

        # Should create network
        self.mock_neutron.network.create_network.assert_called_once()
        call_kwargs = self.mock_neutron.network.create_network.call_args.kwargs
        self.assertEqual('anchor-network', call_kwargs['name'])
        self.assertEqual('geneve', call_kwargs['provider_network_type'])

    def test_ensure_subport_anchor_network_reuses_existing(self):
        """Test subport anchor network reuses existing network."""
        cfg.CONF.set_override('l2vni_subport_anchor_network',
                              'anchor-network',
                              group='l2vni')

        # Mock network exists
        existing_network = FakeNetwork('existing-id', 'anchor-network')
        self.mock_neutron.network.networks.return_value = [existing_network]

        result = self.manager._ensure_subport_anchor_network()

        # Should not create new network
        self.mock_neutron.network.create_network.assert_not_called()
        self.assertEqual('existing-id', result)

    def test_ensure_subport_anchor_network_fails_on_misconfiguration(self):
        """Test subport anchor network fails with error on type mismatch."""
        from openstack import exceptions as sdkexc

        cfg.CONF.set_override('l2vni_auto_create_networks', True,
                              group='l2vni')
        cfg.CONF.set_override('l2vni_subport_anchor_network',
                              'anchor-network',
                              group='l2vni')
        cfg.CONF.set_override('l2vni_subport_anchor_network_type',
                              'geneve',
                              group='l2vni')

        # Mock network doesn't exist
        self.mock_neutron.network.networks.return_value = []

        # Network creation fails due to misconfiguration
        self.mock_neutron.network.create_network.side_effect = \
            sdkexc.BadRequestException("geneve not supported")

        # Should raise the exception rather than fallback
        self.assertRaises(sdkexc.BadRequestException,
                          self.manager._ensure_subport_anchor_network)

        # Should only attempt once (no fallback)
        self.assertEqual(1,
                         self.mock_neutron.network.create_network.call_count)

        # Verify it attempted with configured type
        call_kwargs = (
            self.mock_neutron.network.create_network.call_args.kwargs)
        self.assertEqual('anchor-network', call_kwargs['name'])
        self.assertEqual('geneve', call_kwargs['provider_network_type'])

    def test_ensure_ha_group_network_fails_on_misconfiguration(self):
        """Test HA group network fails with error on type mismatch."""
        from openstack import exceptions as sdkexc

        cfg.CONF.set_override('l2vni_auto_create_networks', True,
                              group='l2vni')
        cfg.CONF.set_override('l2vni_subport_anchor_network_type',
                              'geneve',
                              group='l2vni')

        # Create fake HA chassis group
        ha_chassis = FakeHAChassis('system-1')
        ha_group = FakeHAChassisGroup('ha_group_test', [ha_chassis])

        # Mock network doesn't exist
        self.mock_neutron.network.networks.return_value = []

        # Network creation fails due to misconfiguration
        self.mock_neutron.network.create_network.side_effect = \
            sdkexc.BadRequestException("geneve not supported")

        # Should raise the exception rather than fallback
        self.assertRaises(sdkexc.BadRequestException,
                          self.manager._ensure_ha_group_network,
                          ha_group)

        # Should only attempt once (no fallback)
        self.assertEqual(1,
                         self.mock_neutron.network.create_network.call_count)

        # Verify it attempted with configured type
        call_kwargs = (
            self.mock_neutron.network.create_network.call_args.kwargs)
        self.assertEqual('l2vni-ha-group-ha_group_test',
                         call_kwargs['name'])
        self.assertEqual('geneve', call_kwargs['provider_network_type'])

    def test_discover_trunks_finds_existing_trunks(self):
        """Test trunk discovery finds existing L2VNI trunks."""
        # Setup HA chassis group
        # In real OVN, chassis name IS the system-id
        chassis = FakeChassis('chassis-1', 'system-1',
                              {'ovn-bridge-mappings': 'physnet1:br-ex'})
        ha_chassis = FakeHAChassis('system-1')
        ha_group = FakeHAChassisGroup('ha_group_1', [ha_chassis])
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = [ha_group]

        # Setup Southbound chassis
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = [chassis]

        # Mock trunks
        anchor_port = FakePort(
            'anchor-port-id',
            l2vni_trunk_manager.DEVICE_OWNER_L2VNI_ANCHOR,
            binding_profile={
                'system_id': 'system-1',
                'physical_network': 'physnet1'
            }
        )
        trunk = FakeTrunk('trunk-id-1', 'anchor-port-id',
                          name='l2vni-trunk-system-1-physnet1')

        self.mock_neutron.network.ports.return_value = [anchor_port]
        self.mock_neutron.network.trunks.return_value = [trunk]

        result = self.manager._discover_trunks()

        self.assertEqual(1, len(result))
        self.assertIn(('system-1', 'physnet1'), result)
        self.assertEqual('trunk-id-1', result[('system-1', 'physnet1')])

    def test_discover_trunks_ignores_non_l2vni_trunks(self):
        """Test trunk discovery ignores non-L2VNI device owners."""
        # Mock port with wrong device owner
        port = FakePort('port-id', 'network:dhcp')
        trunk = FakeTrunk('trunk-id', 'port-id')

        self.mock_neutron.ports.return_value = [port]
        self.mock_neutron.trunks.return_value = [trunk]

        result = self.manager._discover_trunks()

        self.assertEqual(0, len(result))

    @mock.patch('neutron.common.ovn.utils.ovn_name', autospec=True)
    def test_calculate_required_vlans_from_ha_groups(self, mock_ovn_name):
        """Test VLAN calculation from HA chassis groups."""
        # Mock ovn_name to return the expected logical switch name
        mock_ovn_name.return_value = 'neutron-network-id-1'

        # Setup HA chassis group
        chassis1 = FakeChassis('chassis-1', 'system-id-1',
                               {'ovn-bridge-mappings':
                                'physnet1:br-ex,physnet2:br-data'})
        ha_chassis = FakeHAChassis('system-id-1')
        ha_group = FakeHAChassisGroup('ha_group_1', [ha_chassis])
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = [ha_group]

        # Setup Southbound chassis (needed for _get_all_chassis_with_physnet)
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = [chassis1]

        # Setup router port with gateway chassis
        lrp = FakeLogicalRouterPort(
            'lrp-1',
            [mock.Mock(chassis=chassis1)],
            networks=['192.168.1.1/24']
        )
        self.mock_ovn_nb.tables['Logical_Router_Port'].rows.values\
            .return_value = [lrp]

        # Mock logical switch port (localnet)
        lsp = FakeLogicalSwitchPort(
            'provnet-physnet1',
            'localnet',
            options={'network_name': 'physnet1'},
            external_ids={'neutron:network_id': 'network-id-1'}
        )
        self.mock_ovn_nb.tables['Logical_Switch_Port'].rows.values\
            .return_value = [lsp]

        # Mock logical switch
        ls = FakeLogicalSwitch(
            'neutron-network-id-1',
            external_ids={'neutron:network_id': 'network-id-1'}
        )
        ls.ports = [lsp]
        self.mock_ovn_nb.tables['Logical_Switch'].rows.values\
            .return_value = [ls]

        # Mock segment
        segment = FakeSegment('network-id-1', n_const.TYPE_VLAN, 100,
                              'physnet1')
        self.mock_neutron.network.segments.return_value = [segment]

        result = self.manager._calculate_required_vlans()

        # Should find VLAN 100 on physnet1 for system-id-1
        self.assertIn(('system-id-1', 'physnet1'), result)
        self.assertIn(100, result[('system-id-1', 'physnet1')])

    def test_reconcile_subports_adds_missing_subports(self):
        """Test subport reconciliation adds missing subports."""
        # Setup trunk with no subports
        trunk = FakeTrunk('trunk-id', 'anchor-port-id',
                          sub_ports=[])
        trunk_map = {('system-1', 'physnet1'): 'trunk-id'}

        # Mock get_trunk to return the trunk
        self.mock_neutron.network.get_trunk.return_value = trunk

        # Setup required VLANs
        required_vlans = {('system-1', 'physnet1'): {100, 200}}

        # Mock anchor network
        cfg.CONF.set_override('l2vni_subport_anchor_network',
                              'anchor-network',
                              group='l2vni')
        anchor_network = FakeNetwork('anchor-net-id', 'anchor-network')
        self.mock_neutron.network.networks.return_value = [anchor_network]

        # Mock port creation
        self.mock_neutron.network.create_port.return_value = FakePort(
            'new-port-id',
            l2vni_trunk_manager.DEVICE_OWNER_L2VNI_SUBPORT
        )

        # Mock local link connection discovery
        with mock.patch.object(
                self.manager,
                '_get_local_link_connection',
                autospec=True,
                return_value={'switch_id': '00:11:22:33:44:55',
                              'port_id': 'Ethernet1'}):
            self.manager._reconcile_subports(trunk_map, required_vlans)

        # Should create 2 subports
        self.assertEqual(2, self.mock_neutron.network.create_port.call_count)

        # Should add subports to trunk (one call per VLAN)
        add_subports = self.mock_neutron.network.add_trunk_subports
        self.assertEqual(2, add_subports.call_count)

    def test_reconcile_subports_removes_extra_subports(self):
        """Test subport reconciliation removes extra subports."""
        # Setup trunk with extra subport
        existing_subport1 = {'port_id': 'subport-1',
                             'segmentation_id': 100,
                             'segmentation_type': 'vlan'}
        existing_subport2 = {'port_id': 'subport-2',
                             'segmentation_id': 200,
                             'segmentation_type': 'vlan'}
        trunk = FakeTrunk('trunk-id', 'anchor-port-id',
                          sub_ports=[existing_subport1, existing_subport2])
        trunk_map = {('system-1', 'physnet1'): 'trunk-id'}

        # Mock get_trunk to return the trunk
        self.mock_neutron.network.get_trunk.return_value = trunk

        # Only VLAN 100 is required, VLAN 200 should be removed
        required_vlans = {('system-1', 'physnet1'): {100}}

        cfg.CONF.set_override('l2vni_subport_anchor_network',
                              'anchor-network',
                              group='l2vni')
        anchor_network = FakeNetwork('anchor-net-id', 'anchor-network')
        self.mock_neutron.network.networks.return_value = [anchor_network]

        self.manager._reconcile_subports(trunk_map, required_vlans)

        # Should remove subport-2
        self.mock_neutron.network.delete_trunk_subports.assert_called_once()
        remove_args = (
            self.mock_neutron.network.delete_trunk_subports.call_args[0][0])
        self.assertEqual('trunk-id', remove_args)

    def test_get_local_link_from_ovn_lldp_success(self):
        """Test local_link_connection retrieval from OVN LLDP data."""
        # Mock chassis with bridge mappings
        chassis = FakeChassis('chassis-1', 'system-id-1')
        chassis.external_ids['ovn-bridge-mappings'] = 'physnet1:br-physnet1'
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = [chassis]

        # Mock port with LLDP data
        port = mock.Mock()
        port.chassis = chassis  # Should be chassis object, not list
        port.external_ids = {
            'lldp_chassis_id': '00:11:22:33:44:55',
            'lldp_port_id': 'Ethernet1/1',
            'lldp_system_name': 'switch.example.com'
        }
        self.mock_ovn_sb.tables['Port'].rows.values\
            .return_value = [port]

        result = self.manager._get_lldp_from_ovn(
            'system-id-1', 'physnet1')

        self.assertIsNotNone(result)
        self.assertEqual('00:11:22:33:44:55', result['switch_id'])
        self.assertEqual('Ethernet1/1', result['port_id'])
        self.assertEqual('switch.example.com', result['switch_info'])

    def test_get_local_link_from_ovn_lldp_chassis_not_found(self):
        """Test OVN LLDP when chassis not found."""
        # Mock empty chassis list
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = []

        result = self.manager._get_lldp_from_ovn(
            'nonexistent-system-id', 'physnet1')

        self.assertIsNone(result)

    def test_get_local_link_from_ironic_success(self):
        """Test local_link_connection retrieval from Ironic."""
        # Mock Ironic port and node
        local_link_conn = {
            'switch_id': 'aa:bb:cc:dd:ee:ff',
            'port_id': 'GigabitEthernet1/0/1',
            'switch_info': 'ironic-switch'
        }
        ironic_port = FakeIronicPort(
            'node-id-1', 'physnet1', local_link_conn)
        ironic_node = FakeIronicNode('node-id-1', 'system-id-1')

        # Mock the new efficient query pattern
        self.mock_ironic.nodes.return_value = [ironic_node]
        self.mock_ironic.ports.return_value = [ironic_port]

        result = self.manager._get_local_link_from_ironic(
            'system-id-1', 'physnet1')

        self.assertIsNotNone(result)
        self.assertEqual('aa:bb:cc:dd:ee:ff', result['switch_id'])
        self.assertEqual('GigabitEthernet1/0/1', result['port_id'])

        # Verify efficient querying - nodes() called with fields filter
        self.mock_ironic.nodes.assert_called_once_with(
            fields=['uuid', 'properties'])
        # Verify ports() called with node_uuid and fields filter
        self.mock_ironic.ports.assert_called_once_with(
            node_uuid='node-id-1',
            fields=['physical_network', 'local_link_connection'])

    def test_get_local_link_from_ironic_node_not_found(self):
        """Test Ironic fallback when node not found."""
        # Mock empty nodes list - no node with matching system_id
        self.mock_ironic.nodes.return_value = []

        result = self.manager._get_local_link_from_ironic(
            'nonexistent-system-id', 'physnet1')

        self.assertIsNone(result)
        # Should not call ports() if no node found
        self.mock_ironic.ports.assert_not_called()

    def test_get_local_link_from_ironic_uses_cache(self):
        """Test that Ironic data is cached per system_id."""
        cfg.CONF.set_override('ironic_cache_ttl', 3600, group='l2vni')

        local_link_conn = {
            'switch_id': 'aa:bb:cc:dd:ee:ff',
            'port_id': 'GigabitEthernet1/0/1',
        }
        ironic_port = FakeIronicPort(
            'node-id-1', 'physnet1', local_link_conn)
        ironic_node = FakeIronicNode('node-id-1', 'system-id-1')

        self.mock_ironic.nodes.return_value = [ironic_node]
        self.mock_ironic.ports.return_value = [ironic_port]

        # First call - should query Ironic
        result1 = self.manager._get_local_link_from_ironic(
            'system-id-1', 'physnet1')
        self.assertIsNotNone(result1)
        self.assertEqual(1, self.mock_ironic.nodes.call_count)
        self.assertEqual(1, self.mock_ironic.ports.call_count)

        # Second call - should use cache
        result2 = self.manager._get_local_link_from_ironic(
            'system-id-1', 'physnet1')
        self.assertIsNotNone(result2)
        self.assertEqual(result1, result2)
        # Still only 1 call - cache was used
        self.assertEqual(1, self.mock_ironic.nodes.call_count)
        self.assertEqual(1, self.mock_ironic.ports.call_count)

    def test_get_local_link_from_ironic_cache_expires(self):
        """Test that Ironic cache expires after TTL."""
        import time

        # Manually inject an expired cache entry to test expiration
        # (we can't set TTL < 300 due to config validation)
        local_link_conn = {
            'switch_id': 'aa:bb:cc:dd:ee:ff',
            'port_id': 'GigabitEthernet1/0/1',
        }
        ironic_port = FakeIronicPort(
            'node-id-1', 'physnet1', local_link_conn)
        ironic_node = FakeIronicNode('node-id-1', 'system-id-1')

        self.mock_ironic.nodes.return_value = [ironic_node]
        self.mock_ironic.ports.return_value = [ironic_port]

        # Manually create an expired cache entry (timestamped in the past)
        self.manager._ironic_cache['system-id-1'] = {
            'cached_at': time.time() - 4000,  # Expired (> 3600s default)
            'node_uuid': 'node-id-1',
            'ports': [{'physnet': 'physnet1', 'local_link': local_link_conn}]
        }

        # Call should detect expired cache and refresh
        result = self.manager._get_local_link_from_ironic(
            'system-id-1', 'physnet1')
        self.assertIsNotNone(result)

        # Should have queried Ironic to refresh expired cache
        self.assertEqual(1, self.mock_ironic.nodes.call_count)
        self.assertEqual(1, self.mock_ironic.ports.call_count)

    def test_get_local_link_from_ironic_with_conductor_group_filter(self):
        """Test Ironic query uses conductor_group filter when configured."""
        cfg.CONF.set_override('ironic_conductor_group',
                              'group1',
                              group='l2vni')

        ironic_node = FakeIronicNode('node-id-1', 'system-id-1')
        self.mock_ironic.nodes.return_value = [ironic_node]
        self.mock_ironic.ports.return_value = []

        self.manager._get_local_link_from_ironic('system-id-1', 'physnet1')

        # Verify conductor_group filter was passed
        self.mock_ironic.nodes.assert_called_once_with(
            fields=['uuid', 'properties'],
            conductor_group='group1')

    def test_get_local_link_from_ironic_with_shard_filter(self):
        """Test Ironic query uses shard filter when configured."""
        cfg.CONF.set_override('ironic_shard', 'shard1', group='l2vni')

        ironic_node = FakeIronicNode('node-id-1', 'system-id-1')
        self.mock_ironic.nodes.return_value = [ironic_node]
        self.mock_ironic.ports.return_value = []

        self.manager._get_local_link_from_ironic('system-id-1', 'physnet1')

        # Verify shard filter was passed
        self.mock_ironic.nodes.assert_called_once_with(
            fields=['uuid', 'properties'],
            shard='shard1')

    def test_get_local_link_connection_tiered_fallback(self):
        """Test tiered local_link_connection discovery."""
        # Setup mocks for tiered fallback
        with mock.patch.object(
                self.manager,
                '_get_lldp_from_ovn',
                autospec=True,
                return_value=None), \
            mock.patch.object(
                self.manager,
                '_get_local_link_from_ironic',
                autospec=True,
                return_value={'switch_id': 'from-ironic',
                              'port_id': 'port1'}), \
            mock.patch.object(
                self.manager,
                '_get_local_link_from_config',
                autospec=True,
                return_value={'switch_id': 'from-config',
                              'port_id': 'port2'}):

            # OVN returns None, should fall back to Ironic
            result = self.manager._get_local_link_connection(
                'system-1', 'physnet1')

            self.assertEqual('from-ironic', result['switch_id'])

    @mock.patch('builtins.open', new_callable=mock.mock_open,
                read_data='''
network_nodes:
  - system_id: system-1
    trunks:
      - physical_network: physnet1
        local_link_connection:
          switch_id: "11:22:33:44:55:66"
          port_id: "Ethernet1"
          switch_info: "config-switch"
''')
    def test_get_local_link_from_config_success(self, mock_file):
        """Test local_link_connection retrieval from YAML config."""
        cfg.CONF.set_override('l2vni_network_nodes_config',
                              '/etc/neutron/l2vni_network_nodes.yaml',
                              group='l2vni')

        result = self.manager._get_local_link_from_config(
            'system-1', 'physnet1')

        self.assertIsNotNone(result)
        self.assertEqual('11:22:33:44:55:66', result['switch_id'])
        self.assertEqual('Ethernet1', result['port_id'])

    def test_cleanup_orphaned_trunks_removes_deleted_chassis(self):
        """Test cleanup removes trunks for deleted chassis."""
        # Mock existing trunk for chassis that no longer exists
        trunk = FakeTrunk('orphan-trunk-id', 'orphan-port-id',
                          name='l2vni-trunk-deleted-system-physnet1')

        self.mock_neutron.network.trunks.return_value = [trunk]

        # No valid chassis/physnet combinations
        valid_chassis_physnets = set()

        self.manager._cleanup_orphaned_trunks(valid_chassis_physnets)

        # Should delete trunk and port
        self.mock_neutron.network.delete_trunk.assert_called_once_with(
            'orphan-trunk-id')
        self.mock_neutron.network.delete_port.assert_called_once_with(
            'orphan-port-id')

    def test_cleanup_orphaned_networks_removes_unused_ha_networks(self):
        """Test cleanup removes ha_chassis_group networks with no groups."""
        # Mock network with l2vni-ha prefix
        orphan_network = FakeNetwork('orphan-net-id',
                                     'l2vni-ha-group-deleted_group')
        self.mock_neutron.network.networks.return_value = [orphan_network]

        # No HA chassis groups exist
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = []

        # Mock no L2VNI ports on network
        self.mock_neutron.network.ports.return_value = []

        self.manager._cleanup_orphaned_networks()

        # Should delete network
        self.mock_neutron.network.delete_network.assert_called_once_with(
            'orphan-net-id')

    def test_cleanup_orphaned_networks_skips_networks_with_ports(self):
        """Test cleanup skips networks that have active L2VNI ports."""
        # Mock network with L2VNI anchor port
        network = FakeNetwork('net-id', 'l2vni-ha-group-group1')
        port = FakePort('port-id',
                        l2vni_trunk_manager.DEVICE_OWNER_L2VNI_ANCHOR)
        self.mock_neutron.network.networks.return_value = [network]
        self.mock_neutron.network.ports.return_value = [port]

        # No HA chassis groups
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = []

        self.manager._cleanup_orphaned_networks()

        # Should not delete network with L2VNI ports
        self.mock_neutron.network.delete_network.assert_not_called()


class TestL2VNITrunkManagerEdgeCases(tests_base.BaseTestCase):
    """Test edge cases and error handling."""

    def setUp(self):
        super(TestL2VNITrunkManagerEdgeCases, self).setUp()

        # Register L2VNI config options
        agent_config.register_l2vni_opts(cfg.CONF)

        self.mock_neutron = mock.Mock()
        self.mock_ovn_nb = mock.Mock()
        self.mock_ovn_sb = mock.Mock()
        self.mock_ironic = mock.Mock()

        # Setup OVN tables structure
        # Tables now use .rows.values() pattern
        self.mock_ovn_nb.tables = {
            'HA_Chassis_Group': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
            'Logical_Router_Port': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
            'Logical_Switch_Port': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
            'Logical_Switch': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
        }

        self.mock_ovn_sb.tables = {
            'Chassis': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
            'Port': mock.Mock(
                rows=mock.Mock(values=mock.Mock(return_value=[]))),
        }

        # Note: member_manager is None, so _should_manage_chassis returns True
        # for all chassis (single agent mode)
        self.manager = l2vni_trunk_manager.L2VNITrunkManager(
            neutron_client=self.mock_neutron,
            ovn_nb_idl=self.mock_ovn_nb,
            ovn_sb_idl=self.mock_ovn_sb,
            ironic_client=self.mock_ironic,
            member_manager=None,
            agent_id=None
        )

    def test_reconcile_handles_exception_gracefully(self):
        """Test reconciliation handles exceptions without crashing."""
        with mock.patch.object(
                self.manager,
                '_ensure_infrastructure_networks',
                autospec=True,
                side_effect=Exception('Test error')):
            # Should log exception but not raise
            try:
                self.manager.reconcile()
            except Exception:
                self.fail('reconcile() raised exception unexpectedly')

    def test_create_trunk_handles_neutron_error(self):
        """Test trunk creation handles Neutron API errors."""
        # Mock trunk doesn't exist
        self.mock_neutron.network.trunks.return_value = []

        # Mock anchor port creation succeeds but trunk creation fails
        self.mock_neutron.network.ports.return_value = []
        self.mock_neutron.network.create_port.return_value = mock.Mock(
            id='anchor-port-id')
        self.mock_neutron.network.create_trunk.side_effect = (
            sdkexc.SDKException('Neutron error'))

        # Mock ha_group network lookup
        with mock.patch.object(
                self.manager,
                '_find_ha_group_network_for_chassis',
                autospec=True,
                return_value='network-id'):
            result = self.manager._find_or_create_trunk(
                'system-1', 'physnet1')
            self.assertIsNone(result)

    def test_get_local_link_from_config_file_not_found(self):
        """Test config file fallback when file doesn't exist."""
        cfg.CONF.set_override('l2vni_network_nodes_config',
                              '/nonexistent/path/config.yaml',
                              group='l2vni')

        result = self.manager._get_local_link_from_config(
            'system-1', 'physnet1')

        self.assertIsNone(result)

    def test_calculate_required_vlans_handles_missing_segment(self):
        """Test VLAN calculation handles missing segment data."""
        # Setup minimal OVN data
        FakeChassis('chassis-1', 'system-1',
                    {'ovn-bridge-mappings': 'physnet1:br-ex'})
        ha_chassis = FakeHAChassis('system-1')
        ha_group = FakeHAChassisGroup('group1', [ha_chassis])
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = [ha_group]

        # Mock empty segments
        self.mock_neutron.network.segments.return_value = []

        result = self.manager._calculate_required_vlans()

        # Should handle gracefully and return empty or minimal result
        self.assertIsInstance(result, dict)
