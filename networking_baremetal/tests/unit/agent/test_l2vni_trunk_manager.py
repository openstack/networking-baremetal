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

    def __init__(self, name, system_id, external_ids=None, other_config=None,
                 hostname=None):
        # In real OVN, chassis.name IS the system-id
        # Use system_id as the name to match real behavior
        self.name = system_id
        self.external_ids = external_ids or {}
        self.other_config = other_config or {}
        self.hostname = hostname
        # Don't store system-id in external_ids - that's not where it is
        # in real OVN (it's the name field)


class FakeLogicalRouterPort:
    """Fake OVN Logical Router Port object."""

    def __init__(self, name, gateway_chassis_list, networks=None,
                 ha_chassis_group=None):
        self.name = name
        self.gateway_chassis = gateway_chassis_list
        self.networks = networks or []
        # ha_chassis_group is a list with 0 or 1 HA_Chassis_Group object
        if ha_chassis_group:
            self.ha_chassis_group = [ha_chassis_group]
        else:
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
        self.binding_profile = binding_profile or {}
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
                 physical_network, segment_id=None):
        self.network_id = network_id
        self.network_type = network_type
        self.segmentation_id = segmentation_id
        self.physical_network = physical_network
        self.id = segment_id or f"segment-{network_id}-{segmentation_id}"


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
        chassis = FakeChassis(
            'chassis-1', 'system-1',
            other_config={'ovn-bridge-mappings': 'physnet1:br-ex'})
        ha_chassis = FakeHAChassis('system-1')
        ha_group = FakeHAChassisGroup('ha_group_1', [ha_chassis])
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = [ha_group]

        # Setup Southbound chassis
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = [chassis]

        # Mock trunks with anchor port that has local_link_information
        local_link = {
            'switch_id': '00:11:22:33:44:55',
            'port_id': 'Ethernet1/5',
            'switch_info': 'switch1'
        }
        anchor_port = FakePort(
            'anchor-port-id',
            l2vni_trunk_manager.DEVICE_OWNER_L2VNI_ANCHOR,
            binding_profile={
                'system_id': 'system-1',
                'physical_network': 'physnet1',
                'local_link_information': [local_link]
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
        chassis1 = FakeChassis(
            'chassis-1', 'system-id-1',
            other_config={'ovn-bridge-mappings':
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
        vlan_info = result[('system-id-1', 'physnet1')][100]
        # VNI should be None since no overlay segment exists
        self.assertIsNone(vlan_info['vni'])
        # segment_id should be present
        self.assertEqual('segment-network-id-1-100', vlan_info['segment_id'])

    @mock.patch('neutron.common.ovn.utils.ovn_name', autospec=True)
    def test_calculate_required_vlans_captures_vni(self, mock_ovn_name):
        """Test VLAN calculation captures VNI from overlay segments."""
        # Mock ovn_name to return the expected logical switch name
        mock_ovn_name.return_value = 'neutron-network-id-1'

        # Setup HA chassis group
        chassis1 = FakeChassis(
            'chassis-1', 'system-id-1',
            other_config={'ovn-bridge-mappings':
                          'physnet1:br-ex,physnet2:br-data'})
        ha_chassis = FakeHAChassis('system-id-1')
        ha_group = FakeHAChassisGroup('ha_group_1', [ha_chassis])
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = [ha_group]

        # Setup Southbound chassis
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = [chassis1]

        # Setup router port
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

        # Mock segments: VLAN + VXLAN overlay
        vlan_segment = FakeSegment('network-id-1', n_const.TYPE_VLAN, 100,
                                   'physnet1')
        vxlan_segment = FakeSegment('network-id-1', n_const.TYPE_VXLAN, 5000,
                                    None)
        self.mock_neutron.network.segments.return_value = [vlan_segment,
                                                           vxlan_segment]

        result = self.manager._calculate_required_vlans()

        # Should find VLAN 100 on physnet1 for system-id-1 with VNI 5000
        self.assertIn(('system-id-1', 'physnet1'), result)
        self.assertIn(100, result[('system-id-1', 'physnet1')])
        vlan_info = result[('system-id-1', 'physnet1')][100]
        self.assertEqual(5000, vlan_info['vni'])
        self.assertEqual('segment-network-id-1-100', vlan_info['segment_id'])

    def test_reconcile_subports_adds_missing_subports(self):
        """Test subport reconciliation adds missing subports."""
        # Setup trunk with no subports
        trunk = FakeTrunk('trunk-id', 'anchor-port-id',
                          sub_ports=[])
        trunk_map = {('system-1', 'physnet1'): 'trunk-id'}

        # Mock get_trunk to return the trunk
        self.mock_neutron.network.get_trunk.return_value = trunk

        # Setup required VLANs with VNI mapping
        required_vlans = {
            ('system-1', 'physnet1'): {
                100: {'vni': 5000, 'segment_id': 'segment-id-100'},
                200: {'vni': 5001, 'segment_id': 'segment-id-200'}
            }
        }

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
                '_get_local_link_information',
                autospec=True,
                return_value={'switch_id': '00:11:22:33:44:55',
                              'port_id': 'Ethernet1'}):
            self.manager._reconcile_subports(trunk_map, required_vlans)

        # Should create 2 subports
        self.assertEqual(2, self.mock_neutron.network.create_port.call_count)

        # Verify binding_profile contains segment_id and VNI for both subports
        calls = self.mock_neutron.network.create_port.call_args_list
        for call in calls:
            kwargs = call[1]
            self.assertIn('binding_profile', kwargs)
            binding_profile = kwargs['binding_profile']
            self.assertIn('physical_network', binding_profile)
            self.assertEqual('physnet1', binding_profile['physical_network'])
            self.assertIn('segment_id', binding_profile)
            # segment_id should be either segment-id-100 or segment-id-200
            self.assertIn(binding_profile['segment_id'],
                          ['segment-id-100', 'segment-id-200'])
            self.assertIn('vni', binding_profile)
            # VNI should be either 5000 or 5001
            self.assertIn(binding_profile['vni'], [5000, 5001])

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
        required_vlans = {
            ('system-1', 'physnet1'): {
                100: {'vni': 5000, 'segment_id': 'segment-id-100'}
            }
        }

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

    def test_reconcile_subports_without_vni(self):
        """Test subport creation without VNI for pure VLAN networks."""
        # Setup trunk with no subports
        trunk = FakeTrunk('trunk-id', 'anchor-port-id',
                          sub_ports=[])
        trunk_map = {('system-1', 'physnet1'): 'trunk-id'}

        # Mock get_trunk to return the trunk
        self.mock_neutron.network.get_trunk.return_value = trunk

        # Setup required VLANs without VNI (None values)
        required_vlans = {
            ('system-1', 'physnet1'): {
                100: {'vni': None, 'segment_id': 'segment-id-100'},
                200: {'vni': None, 'segment_id': 'segment-id-200'}
            }
        }

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
                '_get_local_link_information',
                autospec=True,
                return_value={'switch_id': '00:11:22:33:44:55',
                              'port_id': 'Ethernet1'}):
            self.manager._reconcile_subports(trunk_map, required_vlans)

        # Should create 2 subports
        self.assertEqual(2, self.mock_neutron.network.create_port.call_count)

        # Verify binding_profile contains segment_id but NOT VNI for pure
        # VLAN networks
        calls = self.mock_neutron.network.create_port.call_args_list
        for call in calls:
            kwargs = call[1]
            self.assertIn('binding_profile', kwargs)
            binding_profile = kwargs['binding_profile']
            self.assertIn('physical_network', binding_profile)
            self.assertEqual('physnet1', binding_profile['physical_network'])
            self.assertIn('segment_id', binding_profile)
            self.assertIn(binding_profile['segment_id'],
                          ['segment-id-100', 'segment-id-200'])
            # VNI should NOT be in binding_profile when it's None
            self.assertNotIn('vni', binding_profile)

    def test_get_local_link_from_ovn_lldp_success(self):
        """Test local_link_information retrieval from OVN LLDP data."""
        # Mock chassis with bridge mappings
        chassis = FakeChassis('chassis-1', 'system-id-1')
        chassis.other_config['ovn-bridge-mappings'] = 'physnet1:br-physnet1'
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = [chassis]

        # Mock port with LLDP data on the correct bridge
        port = mock.Mock()
        port.chassis = chassis
        port.external_ids = {
            'lldp_chassis_id': '00:11:22:33:44:55',
            'lldp_port_id': 'Ethernet1/1',
            'lldp_system_name': 'switch.example.com'
        }
        # Mock interface on the bridge
        iface = mock.Mock()
        iface.name = 'br-physnet1'
        port.interfaces = [iface]
        self.mock_ovn_sb.tables['Port'].rows.values\
            .return_value = [port]

        result = self.manager._get_lldp_from_ovn(
            'system-id-1', 'physnet1')

        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertEqual(1, len(result))
        self.assertEqual('00:11:22:33:44:55', result[0]['switch_id'])
        self.assertEqual('Ethernet1/1', result[0]['port_id'])
        self.assertEqual('switch.example.com', result[0]['switch_info'])

    def test_get_local_link_from_ovn_lldp_multiple_ports_lag(self):
        """Test OVN LLDP aggregates multiple ports for LAG/bonding."""
        # Mock chassis with bridge mappings
        chassis = FakeChassis('chassis-1', 'system-id-1')
        chassis.other_config['ovn-bridge-mappings'] = 'physnet1:br-physnet1'
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = [chassis]

        # Mock two ports with LLDP data on the same bridge (LAG scenario)
        port1 = mock.Mock()
        port1.chassis = chassis
        port1.external_ids = {
            'lldp_chassis_id': '00:11:22:33:44:55',
            'lldp_port_id': 'Ethernet1/3',
            'lldp_system_name': 'switch.example.com'
        }
        iface1 = mock.Mock()
        iface1.name = 'br-physnet1'
        port1.interfaces = [iface1]

        port2 = mock.Mock()
        port2.chassis = chassis
        port2.external_ids = {
            'lldp_chassis_id': '00:11:22:33:44:55',
            'lldp_port_id': 'Ethernet1/5',
            'lldp_system_name': 'switch.example.com'
        }
        iface2 = mock.Mock()
        iface2.name = 'br-physnet1'
        port2.interfaces = [iface2]

        self.mock_ovn_sb.tables['Port'].rows.values\
            .return_value = [port1, port2]

        result = self.manager._get_lldp_from_ovn(
            'system-id-1', 'physnet1')

        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertEqual(2, len(result))
        self.assertEqual('00:11:22:33:44:55', result[0]['switch_id'])
        self.assertEqual('Ethernet1/3', result[0]['port_id'])
        self.assertEqual('00:11:22:33:44:55', result[1]['switch_id'])
        self.assertEqual('Ethernet1/5', result[1]['port_id'])

    def test_get_local_link_from_ovn_lldp_chassis_not_found(self):
        """Test OVN LLDP when chassis not found."""
        # Mock empty chassis list
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = []

        result = self.manager._get_lldp_from_ovn(
            'nonexistent-system-id', 'physnet1')

        self.assertIsNone(result)

    def test_get_local_link_from_ovn_lldp_filters_wrong_bridge(self):
        """Test OVN LLDP filters out ports on different bridges."""
        # Mock chassis with multiple bridge mappings
        chassis = FakeChassis('chassis-1', 'system-id-1')
        chassis.other_config['ovn-bridge-mappings'] = (
            'physnet1:br-physnet1,physnet2:br-physnet2')
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = [chassis]

        # Mock ports on different bridges
        port1 = mock.Mock()
        port1.chassis = chassis
        port1.external_ids = {
            'lldp_chassis_id': '00:11:22:33:44:55',
            'lldp_port_id': 'Ethernet1/1',
            'lldp_system_name': 'switch1.example.com'
        }
        iface1 = mock.Mock()
        iface1.name = 'br-physnet1'
        port1.interfaces = [iface1]

        port2 = mock.Mock()
        port2.chassis = chassis
        port2.external_ids = {
            'lldp_chassis_id': 'aa:bb:cc:dd:ee:ff',
            'lldp_port_id': 'Ethernet2/1',
            'lldp_system_name': 'switch2.example.com'
        }
        iface2 = mock.Mock()
        iface2.name = 'br-physnet2'
        port2.interfaces = [iface2]

        self.mock_ovn_sb.tables['Port'].rows.values\
            .return_value = [port1, port2]

        # Query for physnet1 should only return port1
        result = self.manager._get_lldp_from_ovn(
            'system-id-1', 'physnet1')

        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertEqual(1, len(result))
        self.assertEqual('00:11:22:33:44:55', result[0]['switch_id'])
        self.assertEqual('Ethernet1/1', result[0]['port_id'])
        self.assertEqual('switch1.example.com', result[0]['switch_info'])

    def test_get_local_link_from_ironic_multiple_ports_lag(self):
        """Test Ironic aggregates multiple ports for LAG/bonding."""
        # Mock two Ironic ports with same physnet (LAG scenario)
        local_link_conn1 = {
            'switch_id': 'aa:bb:cc:dd:ee:ff',
            'port_id': 'GigabitEthernet1/0/1',
            'switch_info': 'ironic-switch'
        }
        local_link_conn2 = {
            'switch_id': 'aa:bb:cc:dd:ee:ff',
            'port_id': 'GigabitEthernet1/0/2',
            'switch_info': 'ironic-switch'
        }
        ironic_port1 = FakeIronicPort(
            'node-id-1', 'physnet1', local_link_conn1)
        ironic_port2 = FakeIronicPort(
            'node-id-1', 'physnet1', local_link_conn2)
        ironic_node = FakeIronicNode('node-id-1', 'system-id-1')

        self.mock_ironic.nodes.return_value = [ironic_node]
        self.mock_ironic.ports.return_value = [ironic_port1, ironic_port2]

        result = self.manager._get_local_link_from_ironic(
            'system-id-1', 'physnet1')

        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertEqual(2, len(result))
        self.assertEqual('aa:bb:cc:dd:ee:ff', result[0]['switch_id'])
        self.assertEqual('GigabitEthernet1/0/1', result[0]['port_id'])
        self.assertEqual('aa:bb:cc:dd:ee:ff', result[1]['switch_id'])
        self.assertEqual('GigabitEthernet1/0/2', result[1]['port_id'])

    def test_get_local_link_from_ironic_success(self):
        """Test local_link_information retrieval from Ironic."""
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
        self.assertIsInstance(result, list)
        self.assertEqual(1, len(result))
        self.assertEqual('aa:bb:cc:dd:ee:ff', result[0]['switch_id'])
        self.assertEqual('GigabitEthernet1/0/1', result[0]['port_id'])

        # Verify efficient querying - nodes() called with fields filter
        self.mock_ironic.nodes.assert_called_once_with(
            fields=['uuid', 'properties'])
        # Verify ports() called with node_uuid and fields filter
        self.mock_ironic.ports.assert_called_once_with(
            node_uuid='node-id-1',
            fields=['physical_network', 'local_link_connection'])

    def test_aggregate_ironic_ports_for_physnet_single_port(self):
        """Test port aggregation helper with single port."""
        cache_entry = {
            'cached_at': 1234567890.0,
            'node_uuid': 'node-1',
            'ports': [
                {
                    'physnet': 'physnet1',
                    'local_link': {
                        'switch_id': 'aa:bb:cc:dd:ee:ff',
                        'port_id': 'Ethernet1/1'
                    }
                }
            ]
        }

        result = self.manager._aggregate_ironic_ports_for_physnet(
            cache_entry, 'physnet1', 'system-1', 'test-source')

        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertEqual(1, len(result))
        self.assertEqual('aa:bb:cc:dd:ee:ff', result[0]['switch_id'])

    def test_aggregate_ironic_ports_for_physnet_multiple_ports(self):
        """Test port aggregation helper with multiple ports (LAG)."""
        cache_entry = {
            'cached_at': 1234567890.0,
            'node_uuid': 'node-1',
            'ports': [
                {
                    'physnet': 'physnet1',
                    'local_link': {
                        'switch_id': 'aa:bb:cc:dd:ee:ff',
                        'port_id': 'Ethernet1/1'
                    }
                },
                {
                    'physnet': 'physnet1',
                    'local_link': {
                        'switch_id': 'aa:bb:cc:dd:ee:ff',
                        'port_id': 'Ethernet1/2'
                    }
                },
                {
                    'physnet': 'physnet2',
                    'local_link': {
                        'switch_id': 'aa:bb:cc:dd:ee:ff',
                        'port_id': 'Ethernet2/1'
                    }
                }
            ]
        }

        result = self.manager._aggregate_ironic_ports_for_physnet(
            cache_entry, 'physnet1', 'system-1', 'test-source')

        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertEqual(2, len(result))
        self.assertEqual('Ethernet1/1', result[0]['port_id'])
        self.assertEqual('Ethernet1/2', result[1]['port_id'])

    def test_aggregate_ironic_ports_for_physnet_no_match(self):
        """Test port aggregation helper with no matching physnet."""
        cache_entry = {
            'cached_at': 1234567890.0,
            'node_uuid': 'node-1',
            'ports': [
                {
                    'physnet': 'physnet2',
                    'local_link': {
                        'switch_id': 'aa:bb:cc:dd:ee:ff',
                        'port_id': 'Ethernet1/1'
                    }
                }
            ]
        }

        result = self.manager._aggregate_ironic_ports_for_physnet(
            cache_entry, 'physnet1', 'system-1', 'test-source')

        self.assertIsNone(result)

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
        self.assertIsInstance(result1, list)
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

    def test_get_local_link_information_tiered_fallback(self):
        """Test tiered local_link_information discovery."""
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
                return_value=[{'switch_id': 'from-ironic',
                               'port_id': 'port1'}]), \
            mock.patch.object(
                self.manager,
                '_get_local_link_from_config',
                autospec=True,
                return_value=[{'switch_id': 'from-config',
                               'port_id': 'port2'}]):

            # OVN returns None, should fall back to Ironic
            result = self.manager._get_local_link_information(
                'system-1', 'physnet1')

            self.assertIsInstance(result, list)
            self.assertEqual('from-ironic', result[0]['switch_id'])

    @mock.patch('builtins.open', new_callable=mock.mock_open,
                read_data='''
network_nodes:
  - system_id: system-1
    trunks:
      - physical_network: physnet1
        local_link_information:
          switch_id: "11:22:33:44:55:66"
          port_id: "Ethernet1"
          switch_info: "config-switch"
''')
    def test_get_local_link_from_config_success(self, mock_file):
        """Test local_link_information retrieval from YAML config."""
        cfg.CONF.set_override('l2vni_network_nodes_config',
                              '/etc/neutron/l2vni_network_nodes.yaml',
                              group='l2vni')

        result = self.manager._get_local_link_from_config(
            'system-1', 'physnet1')

        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertEqual(1, len(result))
        self.assertEqual('11:22:33:44:55:66', result[0]['switch_id'])
        self.assertEqual('Ethernet1', result[0]['port_id'])

    @mock.patch('builtins.open', new_callable=mock.mock_open,
                read_data='''
network_nodes:
  - system_id: system-1
    trunks:
      - physical_network: physnet1
        local_link_information:
          - switch_id: "22:57:f8:dd:03:01"
            port_id: "Ethernet1/3"
            switch_info: "leaf01.netlab.example.com"
          - switch_id: "22:57:f8:dd:03:01"
            port_id: "Ethernet1/5"
            switch_info: "leaf01.netlab.example.com"
''')
    def test_get_local_link_from_config_list_format_lag(self, mock_file):
        """Test YAML config with list of links for LAG/bonding."""
        cfg.CONF.set_override('l2vni_network_nodes_config',
                              '/etc/neutron/l2vni_network_nodes.yaml',
                              group='l2vni')

        result = self.manager._get_local_link_from_config(
            'system-1', 'physnet1')

        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertEqual(2, len(result))
        self.assertEqual('22:57:f8:dd:03:01', result[0]['switch_id'])
        self.assertEqual('Ethernet1/3', result[0]['port_id'])
        self.assertEqual('22:57:f8:dd:03:01', result[1]['switch_id'])
        self.assertEqual('Ethernet1/5', result[1]['port_id'])

    @mock.patch('builtins.open', new_callable=mock.mock_open,
                read_data='''
network_nodes:
  - hostname: test-hostname
    trunks:
      - physical_network: physnet1
        local_link_information:
          switch_id: "aa:bb:cc:dd:ee:ff"
          port_id: "Ethernet2"
          switch_info: "hostname-switch"
''')
    def test_get_local_link_from_config_hostname_fallback(self, mock_file):
        """Test local_link_information retrieval using hostname fallback."""
        cfg.CONF.set_override('l2vni_network_nodes_config',
                              '/etc/neutron/l2vni_network_nodes.yaml',
                              group='l2vni')

        # Mock chassis with hostname
        chassis = FakeChassis('chassis-1', 'system-uuid-123')
        chassis.hostname = 'test-hostname'
        self.mock_ovn_sb.tables['Chassis'].rows.values.return_value = [chassis]

        result = self.manager._get_local_link_from_config(
            'system-uuid-123', 'physnet1')

        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertEqual(1, len(result))
        self.assertEqual('aa:bb:cc:dd:ee:ff', result[0]['switch_id'])
        self.assertEqual('Ethernet2', result[0]['port_id'])

    @mock.patch('builtins.open', new_callable=mock.mock_open,
                read_data='''
network_nodes:
  - system_id: system-1
    trunks:
      - physical_network: physnet1
        local_link_connection:
          switch_id: "11:22:33:44:55:66"
          port_id: "Ethernet1"
''')
    def test_get_local_link_from_config_deprecated_name_warning(
            self, mock_file):
        """Test deprecation warning for old local_link_connection name."""
        cfg.CONF.set_override('l2vni_network_nodes_config',
                              '/etc/neutron/l2vni_network_nodes.yaml',
                              group='l2vni')

        with self.assertLogs(
                'networking_baremetal.agent.l2vni_trunk_manager',
                level='WARNING') as log:
            result = self.manager._get_local_link_from_config(
                'system-1', 'physnet1')

            self.assertIsNotNone(result)
            self.assertIn("deprecated 'local_link_connection'",
                          log.output[0])
            self.assertIn("'local_link_information'", log.output[0])

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

        # Mock ha_group network lookup and local_link_information
        with mock.patch.object(
                self.manager,
                '_find_ha_group_network_for_chassis',
                autospec=True,
                return_value='network-id'), \
             mock.patch.object(
                self.manager,
                '_get_local_link_information',
                autospec=True,
                return_value=None):
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
                    other_config={'ovn-bridge-mappings': 'physnet1:br-ex'})
        ha_chassis = FakeHAChassis('system-1')
        ha_group = FakeHAChassisGroup('group1', [ha_chassis])
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = [ha_group]

        # Mock empty segments
        self.mock_neutron.network.segments.return_value = []

        result = self.manager._calculate_required_vlans()

        # Should handle gracefully and return empty or minimal result
        self.assertIsInstance(result, dict)

    def test_anchor_port_creation_includes_local_link_information(self):
        """Test anchor port creation includes local_link_information."""
        system_id = 'system-1'
        physnet = 'physnet1'

        # Mock no existing port
        self.mock_neutron.network.ports.return_value = []

        # Mock ha_group network
        ha_network = FakeNetwork('ha-net-id', 'l2vni-ha-group-group1')
        self.mock_neutron.network.networks.return_value = [ha_network]

        # Setup OVN data for ha_group lookup
        chassis = FakeChassis('chassis-1', system_id, hostname='host1')
        ha_chassis = FakeHAChassis(system_id)
        ha_group = FakeHAChassisGroup('group1', [ha_chassis])
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = [ha_group]
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = [chassis]

        # Mock local_link_information discovery
        local_link = {
            'switch_id': '00:11:22:33:44:55',
            'port_id': 'Ethernet1/5',
            'switch_info': 'switch1'
        }
        with mock.patch.object(
                self.manager,
                '_get_local_link_information',
                autospec=True,
                return_value=[local_link]):

            # Mock port creation
            created_port = FakePort(
                'anchor-port-id',
                l2vni_trunk_manager.DEVICE_OWNER_L2VNI_ANCHOR)
            self.mock_neutron.network.create_port.return_value = created_port

            result = self.manager._find_or_create_anchor_port(
                system_id, physnet)

        # Should create port with local_link_information in binding profile
        self.assertEqual('anchor-port-id', result)
        self.mock_neutron.network.create_port.assert_called_once()
        call_kwargs = self.mock_neutron.network.create_port.call_args[1]
        self.assertIn('binding_profile', call_kwargs)
        self.assertIn('local_link_information',
                      call_kwargs['binding_profile'])
        self.assertEqual(
            [local_link],
            call_kwargs['binding_profile']['local_link_information'])

    def test_anchor_port_creation_with_multiple_links_lag(self):
        """Test anchor port creation with multiple links for LAG/bonding."""
        system_id = 'system-1'
        physnet = 'physnet1'

        # Mock no existing port
        self.mock_neutron.network.ports.return_value = []

        # Mock ha_group network
        ha_network = FakeNetwork('ha-net-id', 'l2vni-ha-group-group1')
        self.mock_neutron.network.networks.return_value = [ha_network]

        # Setup OVN data for ha_group lookup
        chassis = FakeChassis('chassis-1', system_id, hostname='host1')
        ha_chassis = FakeHAChassis(system_id)
        ha_group = FakeHAChassisGroup('group1', [ha_chassis])
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = [ha_group]
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = [chassis]

        # Mock local_link_information discovery with multiple links
        local_links = [
            {
                'switch_id': '00:11:22:33:44:55',
                'port_id': 'Ethernet1/3',
                'switch_info': 'switch1'
            },
            {
                'switch_id': '00:11:22:33:44:55',
                'port_id': 'Ethernet1/5',
                'switch_info': 'switch1'
            }
        ]
        with mock.patch.object(
                self.manager,
                '_get_local_link_information',
                autospec=True,
                return_value=local_links):

            # Mock port creation
            created_port = FakePort(
                'anchor-port-id',
                l2vni_trunk_manager.DEVICE_OWNER_L2VNI_ANCHOR)
            self.mock_neutron.network.create_port.return_value = created_port

            result = self.manager._find_or_create_anchor_port(
                system_id, physnet)

        # Should create port with multiple links in binding profile
        self.assertEqual('anchor-port-id', result)
        self.mock_neutron.network.create_port.assert_called_once()
        call_kwargs = self.mock_neutron.network.create_port.call_args[1]
        self.assertIn('binding_profile', call_kwargs)
        self.assertIn('local_link_information',
                      call_kwargs['binding_profile'])
        self.assertEqual(
            local_links,
            call_kwargs['binding_profile']['local_link_information'])

    def test_anchor_port_creation_without_local_link_information(self):
        """Test anchor port creation when local_link_information is N/A."""
        system_id = 'system-1'
        physnet = 'physnet1'

        # Mock no existing port
        self.mock_neutron.network.ports.return_value = []

        # Mock ha_group network
        ha_network = FakeNetwork('ha-net-id', 'l2vni-ha-group-group1')
        self.mock_neutron.network.networks.return_value = [ha_network]

        # Setup OVN data
        chassis = FakeChassis('chassis-1', system_id, hostname='host1')
        ha_chassis = FakeHAChassis(system_id)
        ha_group = FakeHAChassisGroup('group1', [ha_chassis])
        self.mock_ovn_nb.tables['HA_Chassis_Group'].rows.values\
            .return_value = [ha_group]
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = [chassis]

        # Mock local_link_information discovery returns None
        with mock.patch.object(
                self.manager,
                '_get_local_link_information',
                autospec=True,
                return_value=None):

            # Mock port creation
            created_port = FakePort(
                'anchor-port-id',
                l2vni_trunk_manager.DEVICE_OWNER_L2VNI_ANCHOR)
            self.mock_neutron.network.create_port.return_value = created_port

            result = self.manager._find_or_create_anchor_port(
                system_id, physnet)

        # Should still create port, but without local_link_information
        self.assertEqual('anchor-port-id', result)
        self.mock_neutron.network.create_port.assert_called_once()
        call_kwargs = self.mock_neutron.network.create_port.call_args[1]
        self.assertIn('binding_profile', call_kwargs)
        self.assertNotIn('local_link_information',
                         call_kwargs['binding_profile'])

    def test_anchor_port_reconciliation_adds_missing_local_link(self):
        """Test reconciliation updates anchor port missing LLC.

        Updates existing anchor ports that are missing local_link_information
        in their binding profile.
        """
        system_id = 'system-1'
        physnet = 'physnet1'

        # Mock existing port WITHOUT local_link_information
        existing_port = FakePort('anchor-port-id',
                                 l2vni_trunk_manager.DEVICE_OWNER_L2VNI_ANCHOR,
                                 binding_profile={'system_id': system_id,
                                                  'physical_network': physnet})
        self.mock_neutron.network.ports.return_value = [existing_port]

        # Mock local_link_information discovery
        local_link = {
            'switch_id': '00:11:22:33:44:55',
            'port_id': 'Ethernet1/5',
            'switch_info': 'switch1'
        }
        with mock.patch.object(
                self.manager,
                '_get_local_link_information',
                autospec=True,
                return_value=[local_link]):

            result = self.manager._find_or_create_anchor_port(
                system_id, physnet)

        # Should return existing port and update it
        self.assertEqual('anchor-port-id', result)
        self.mock_neutron.network.update_port.assert_called_once()
        call_args = self.mock_neutron.network.update_port.call_args
        self.assertEqual('anchor-port-id', call_args[0][0])
        updated_profile = call_args[1]['binding_profile']
        self.assertIn('local_link_information', updated_profile)
        self.assertEqual([local_link],
                         updated_profile['local_link_information'])

    def test_anchor_port_reconciliation_skips_correct_ports(self):
        """Test reconciliation skips correctly configured anchor ports.

        Verifies that anchor ports with local_link_information already set
        are not updated.
        """
        system_id = 'system-1'
        physnet = 'physnet1'

        local_link = {
            'switch_id': '00:11:22:33:44:55',
            'port_id': 'Ethernet1/5',
            'switch_info': 'switch1'
        }

        # Mock existing port WITH local_link_information
        existing_port = FakePort('anchor-port-id',
                                 l2vni_trunk_manager.DEVICE_OWNER_L2VNI_ANCHOR,
                                 binding_profile={
                                     'system_id': system_id,
                                     'physical_network': physnet,
                                     'local_link_information': [local_link]
                                 })
        self.mock_neutron.network.ports.return_value = [existing_port]

        result = self.manager._find_or_create_anchor_port(system_id, physnet)

        # Should return existing port without updating
        self.assertEqual('anchor-port-id', result)
        self.mock_neutron.network.update_port.assert_not_called()

    def test_existing_trunk_reconciles_anchor_port(self):
        """Test that existing trunks still reconcile their anchor ports.

        Verifies that when a trunk already exists, _find_or_create_trunk()
        still calls _find_or_create_anchor_port() to reconcile the anchor
        port's binding profile. This ensures existing trunks created before
        the local_link_information fix get updated.
        """
        system_id = 'system-1'
        physnet = 'physnet1'

        # Mock existing anchor port WITHOUT local_link_information
        existing_anchor_port = FakePort(
            'anchor-port-id',
            l2vni_trunk_manager.DEVICE_OWNER_L2VNI_ANCHOR,
            binding_profile={
                'system_id': system_id,
                'physical_network': physnet
            })

        # Mock existing trunk
        existing_trunk = FakeTrunk(
            'trunk-id',
            'anchor-port-id',
            name='l2vni-trunk-system-1-physnet1')

        self.mock_neutron.network.ports.return_value = [existing_anchor_port]
        self.mock_neutron.network.trunks.return_value = [existing_trunk]

        # Mock local_link_information discovery
        local_link = {
            'switch_id': '00:11:22:33:44:55',
            'port_id': 'Ethernet1/5',
            'switch_info': 'switch1'
        }
        with mock.patch.object(
                self.manager,
                '_get_local_link_information',
                autospec=True,
                return_value=[local_link]):

            result = self.manager._find_or_create_trunk(system_id, physnet)

        # Should return existing trunk
        self.assertEqual('trunk-id', result)

        # Should have updated the anchor port with local_link_information
        self.mock_neutron.network.update_port.assert_called_once()
        call_args = self.mock_neutron.network.update_port.call_args
        self.assertEqual('anchor-port-id', call_args[0][0])
        updated_profile = call_args[1]['binding_profile']
        self.assertIn('local_link_information', updated_profile)
        self.assertEqual([local_link],
                         updated_profile['local_link_information'])

    def test_subport_creation_sets_binding_host_id(self):
        """Test subport creation sets binding:host_id to chassis hostname."""
        trunk_id = 'trunk-id'
        system_id = 'system-1'
        physnet = 'physnet1'
        vlan_id = 100
        anchor_network_id = 'anchor-net-id'
        segment_id = 'segment-id-1'

        # Setup chassis with hostname
        chassis = FakeChassis('chassis-1', system_id, hostname='devstack')
        self.mock_ovn_sb.tables['Chassis'].rows.values\
            .return_value = [chassis]

        # Mock port creation
        created_port = FakePort('subport-id',
                                l2vni_trunk_manager.DEVICE_OWNER_L2VNI_SUBPORT)
        self.mock_neutron.network.create_port.return_value = created_port

        self.manager._add_subport(trunk_id, system_id, physnet, vlan_id,
                                  anchor_network_id, segment_id)

        # Should create port and set binding:host_id
        self.mock_neutron.network.create_port.assert_called_once()
        self.mock_neutron.network.update_port.assert_called_once()

        # Check update_port was called with binding:host_id
        update_call = self.mock_neutron.network.update_port.call_args
        self.assertEqual('subport-id', update_call[0][0])
        self.assertIn('binding:host_id', update_call[1])
        self.assertEqual('devstack', update_call[1]['binding:host_id'])

    def test_subport_creation_without_hostname(self):
        """Test subport creation when hostname cannot be determined."""
        trunk_id = 'trunk-id'
        system_id = 'system-1'
        physnet = 'physnet1'
        vlan_id = 100
        anchor_network_id = 'anchor-net-id'
        segment_id = 'segment-id-1'

        # Mock empty chassis table (hostname lookup fails)
        self.mock_ovn_sb.tables['Chassis'].rows.values.return_value = []

        # Mock port creation
        created_port = FakePort('subport-id',
                                l2vni_trunk_manager.DEVICE_OWNER_L2VNI_SUBPORT)
        self.mock_neutron.network.create_port.return_value = created_port

        self.manager._add_subport(trunk_id, system_id, physnet, vlan_id,
                                  anchor_network_id, segment_id)

        # Should create port but NOT call update_port (no hostname)
        self.mock_neutron.network.create_port.assert_called_once()
        # update_port should not be called since we have no hostname
        self.mock_neutron.network.update_port.assert_not_called()

    def test_get_vni_and_segment_for_network_with_both(self):
        """Test _get_vni_and_segment_for_network returns both values."""
        network_id = 'network-id-1'
        physnet = 'physnet1'
        vlan_id = 100

        # Mock segments: VLAN + VXLAN overlay
        vlan_segment = FakeSegment(network_id, n_const.TYPE_VLAN, vlan_id,
                                   physnet, segment_id='seg-vlan-100')
        vxlan_segment = FakeSegment(network_id, n_const.TYPE_VXLAN, 5000,
                                    None, segment_id='seg-vxlan-5000')
        self.mock_neutron.network.segments.return_value = [vlan_segment,
                                                           vxlan_segment]

        vni, segment_id = self.manager._get_vni_and_segment_for_network(
            network_id, physnet, vlan_id)

        self.assertEqual(5000, vni)
        self.assertEqual('seg-vlan-100', segment_id)

    def test_get_vni_and_segment_for_network_vlan_only(self):
        """Test _get_vni_and_segment_for_network with VLAN only."""
        network_id = 'network-id-1'
        physnet = 'physnet1'
        vlan_id = 100

        # Mock only VLAN segment (no overlay)
        vlan_segment = FakeSegment(network_id, n_const.TYPE_VLAN, vlan_id,
                                   physnet, segment_id='seg-vlan-100')
        self.mock_neutron.network.segments.return_value = [vlan_segment]

        vni, segment_id = self.manager._get_vni_and_segment_for_network(
            network_id, physnet, vlan_id)

        self.assertIsNone(vni)
        self.assertEqual('seg-vlan-100', segment_id)

    def test_get_vni_and_segment_for_network_no_match(self):
        """Test _get_vni_and_segment_for_network with no matching segment."""
        network_id = 'network-id-1'
        physnet = 'physnet1'
        vlan_id = 100

        # Mock segment with different VLAN ID
        vlan_segment = FakeSegment(network_id, n_const.TYPE_VLAN, 200,
                                   physnet, segment_id='seg-vlan-200')
        self.mock_neutron.network.segments.return_value = [vlan_segment]

        vni, segment_id = self.manager._get_vni_and_segment_for_network(
            network_id, physnet, vlan_id)

        self.assertIsNone(vni)
        self.assertIsNone(segment_id)

    def test_reconcile_trunk_subports_skips_when_segment_id_missing(self):
        """Test subport creation is skipped when segment_id is missing."""
        trunk_id = 'trunk-id'
        system_id = 'system-1'
        physnet = 'physnet1'
        anchor_network_id = 'anchor-net-id'

        # Setup trunk with no subports
        trunk = FakeTrunk(trunk_id, 'anchor-port-id', sub_ports=[])
        self.mock_neutron.network.get_trunk.return_value = trunk

        # VLAN info missing segment_id (simulates bug or old data)
        vlan_vni_map = {100: {'vni': 5000, 'segment_id': None}}

        self.manager._reconcile_trunk_subports(
            trunk_id, system_id, physnet, vlan_vni_map, anchor_network_id)

        # Should NOT create port when segment_id is missing
        self.mock_neutron.network.create_port.assert_not_called()


class TestL2VNITrunkManagerTargetedReconciliation(tests_base.BaseTestCase):
    """Tests for targeted single-VLAN reconciliation."""

    def setUp(self):
        super().setUp()
        agent_config.register_agent_opts(CONF)
        CONF.set_override('l2vni_subport_anchor_network', 'l2vni-subports',
                          group='l2vni')
        CONF.set_override('l2vni_auto_create_networks', True, group='l2vni')

    def _create_manager(self):
        """Create L2VNITrunkManager with mocked dependencies."""
        neutron = mock.Mock()
        ovn_nb_idl = mock.Mock()
        ovn_sb_idl = mock.Mock()
        ironic = mock.Mock()

        ovn_nb_idl.tables = {
            'HA_Chassis_Group': mock.Mock(rows=mock.Mock(values=mock.Mock(
                return_value=[]))),
            'Logical_Switch_Port': mock.Mock(rows=mock.Mock(values=mock.Mock(
                return_value=[]))),
            'Logical_Router_Port': mock.Mock(rows=mock.Mock(values=mock.Mock(
                return_value=[])))
        }
        ovn_sb_idl.tables = {
            'Chassis': mock.Mock(rows=mock.Mock(values=mock.Mock(
                return_value=[])))
        }

        return l2vni_trunk_manager.L2VNITrunkManager(
            neutron, ovn_nb_idl, ovn_sb_idl, ironic)

    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_ensure_infrastructure_networks', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_subport_anchor_network_id', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_all_chassis_with_physnet', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_find_or_create_trunk', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_ensure_single_subport', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_vni_and_segment_for_network', autospec=True)
    def test_reconcile_single_vlan_add_action(
            self, mock_get_vni_segment, mock_ensure_subport, mock_find_trunk,
            mock_get_chassis, mock_get_anchor, mock_ensure_infra):
        """Test targeted reconciliation adds subport for single VLAN."""
        manager = self._create_manager()

        mock_get_anchor.return_value = 'anchor-net-id'
        mock_get_chassis.return_value = {'chassis-1', 'chassis-2'}
        mock_get_vni_segment.return_value = (5000, 'segment-id-1')

        trunk_map = {}

        def find_trunk_side_effect(self, system_id, physnet):
            trunk_id = f'trunk-{system_id}'
            trunk_map[system_id] = trunk_id
            return trunk_id

        mock_find_trunk.side_effect = find_trunk_side_effect

        manager.reconcile_single_vlan('net-1', 'physnet1', 100, action='add')

        mock_ensure_infra.assert_called_once_with(manager)
        mock_get_anchor.assert_called_once_with(manager)
        mock_get_chassis.assert_called_once_with(manager, 'physnet1')
        mock_get_vni_segment.assert_called_once_with(
            manager, 'net-1', 'physnet1', 100)
        self.assertEqual(2, mock_find_trunk.call_count)
        self.assertEqual(2, mock_ensure_subport.call_count)

        for system_id in ['chassis-1', 'chassis-2']:
            trunk_id = trunk_map[system_id]
            mock_ensure_subport.assert_any_call(
                manager, trunk_id, system_id, 'physnet1', 100,
                'anchor-net-id', 'segment-id-1', vni=5000)

    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_ensure_infrastructure_networks', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_subport_anchor_network_id', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_all_chassis_with_physnet', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_find_or_create_trunk', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_remove_single_subport', autospec=True)
    def test_reconcile_single_vlan_remove_action(
            self, mock_remove_subport, mock_find_trunk, mock_get_chassis,
            mock_get_anchor, mock_ensure_infra):
        """Test targeted reconciliation removes subport for single VLAN."""
        manager = self._create_manager()

        mock_get_anchor.return_value = 'anchor-net-id'
        mock_get_chassis.return_value = {'chassis-1'}
        mock_find_trunk.return_value = 'trunk-1'

        manager.reconcile_single_vlan(
            'net-1', 'physnet1', 200, action='remove')

        mock_ensure_infra.assert_called_once_with(manager)
        mock_get_anchor.assert_called_once_with(manager)
        mock_get_chassis.assert_called_once_with(manager, 'physnet1')
        mock_find_trunk.assert_called_once_with(
            manager, 'chassis-1', 'physnet1')
        mock_remove_subport.assert_called_once_with(
            manager, 'trunk-1', 'chassis-1', 'physnet1', 200)

    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_ensure_infrastructure_networks', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_subport_anchor_network_id', autospec=True)
    def test_reconcile_single_vlan_no_anchor_network(
            self, mock_get_anchor, mock_ensure_infra):
        """Test reconciliation exits early if anchor network missing."""
        manager = self._create_manager()
        mock_get_anchor.return_value = None

        manager.reconcile_single_vlan('net-1', 'physnet1', 100, action='add')

        mock_ensure_infra.assert_called_once_with(manager)
        mock_get_anchor.assert_called_once_with(manager)

    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_ensure_infrastructure_networks', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_subport_anchor_network_id', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_all_chassis_with_physnet', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_vni_and_segment_for_network', autospec=True)
    def test_reconcile_single_vlan_no_chassis_with_physnet(
            self, mock_get_vni_segment, mock_get_chassis, mock_get_anchor,
            mock_ensure_infra):
        """Test reconciliation exits early if no chassis with physnet."""
        manager = self._create_manager()
        mock_get_anchor.return_value = 'anchor-net-id'
        mock_get_chassis.return_value = set()
        mock_get_vni_segment.return_value = (5000, 'segment-id-1')

        manager.reconcile_single_vlan('net-1', 'physnet1', 100, action='add')

        mock_ensure_infra.assert_called_once_with(manager)
        mock_get_chassis.assert_called_once_with(manager, 'physnet1')
        mock_get_vni_segment.assert_called_once_with(
            manager, 'net-1', 'physnet1', 100)

    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_ensure_infrastructure_networks', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_subport_anchor_network_id', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_all_chassis_with_physnet', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_find_or_create_trunk', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_ensure_single_subport', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_vni_and_segment_for_network', autospec=True)
    def test_reconcile_single_vlan_trunk_creation_fails(
            self, mock_get_vni_segment, mock_ensure_subport, mock_find_trunk,
            mock_get_chassis, mock_get_anchor, mock_ensure_infra):
        """Test reconciliation continues if trunk creation fails."""
        manager = self._create_manager()

        mock_get_anchor.return_value = 'anchor-net-id'
        mock_get_chassis.return_value = {'chassis-1', 'chassis-2'}
        mock_get_vni_segment.return_value = (5000, 'segment-id-1')

        def find_trunk_side_effect(self, system_id, physnet):
            if system_id == 'chassis-1':
                return None
            return f'trunk-{system_id}'

        mock_find_trunk.side_effect = find_trunk_side_effect

        manager.reconcile_single_vlan('net-1', 'physnet1', 100, action='add')

        self.assertEqual(2, mock_find_trunk.call_count)
        mock_ensure_subport.assert_called_once_with(
            manager, 'trunk-chassis-2', 'chassis-2', 'physnet1', 100,
            'anchor-net-id', 'segment-id-1', vni=5000)

    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_ensure_infrastructure_networks', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_subport_anchor_network_id', autospec=True)
    def test_reconcile_single_vlan_handles_sdk_exception(
            self, mock_get_anchor, mock_ensure_infra):
        """Test reconciliation handles SDK exceptions gracefully."""
        manager = self._create_manager()
        mock_get_anchor.side_effect = sdkexc.SDKException("API error")

        manager.reconcile_single_vlan('net-1', 'physnet1', 100, action='add')

        mock_ensure_infra.assert_called_once_with(manager)

    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_ensure_infrastructure_networks', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_subport_anchor_network_id', autospec=True)
    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_get_vni_and_segment_for_network', autospec=True)
    def test_reconcile_single_vlan_skips_when_segment_id_missing(
            self, mock_get_vni_segment, mock_get_anchor, mock_ensure_infra):
        """Test reconciliation skips when segment_id cannot be found."""
        manager = self._create_manager()

        mock_get_anchor.return_value = 'anchor-net-id'
        # Return vni but no segment_id
        mock_get_vni_segment.return_value = (5000, None)

        manager.reconcile_single_vlan('net-1', 'physnet1', 100, action='add')

        mock_ensure_infra.assert_called_once_with(manager)
        mock_get_anchor.assert_called_once_with(manager)
        mock_get_vni_segment.assert_called_once_with(
            manager, 'net-1', 'physnet1', 100)
        # Should return early, not proceed to chassis lookup

    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_add_subport', autospec=True)
    def test_ensure_single_subport_creates_when_missing(self, mock_add):
        """Test _ensure_single_subport creates subport if missing."""
        manager = self._create_manager()

        trunk = mock.Mock()
        trunk.sub_ports = [
            {'port_id': 'port-1', 'segmentation_id': 100},
            {'port_id': 'port-2', 'segmentation_id': 200}
        ]
        manager.neutron.network.get_trunk.return_value = trunk

        manager._ensure_single_subport(
            'trunk-1', 'chassis-1', 'physnet1', 300, 'anchor-net-id',
            'segment-id-1', vni=5000)

        mock_add.assert_called_once_with(
            manager, 'trunk-1', 'chassis-1', 'physnet1', 300, 'anchor-net-id',
            'segment-id-1', vni=5000)

    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_add_subport', autospec=True)
    def test_ensure_single_subport_skips_if_exists(self, mock_add):
        """Test _ensure_single_subport is idempotent."""
        manager = self._create_manager()

        trunk = mock.Mock()
        trunk.sub_ports = [
            {'port_id': 'port-1', 'segmentation_id': 100},
            {'port_id': 'port-2', 'segmentation_id': 200}
        ]
        manager.neutron.network.get_trunk.return_value = trunk

        manager._ensure_single_subport(
            'trunk-1', 'chassis-1', 'physnet1', 100, 'anchor-net-id',
            'segment-id-1')

        mock_add.assert_not_called()

    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_remove_subport', autospec=True)
    def test_remove_single_subport_removes_when_exists(self, mock_remove):
        """Test _remove_single_subport removes existing subport."""
        manager = self._create_manager()

        trunk = mock.Mock()
        trunk.sub_ports = [
            {'port_id': 'port-1', 'segmentation_id': 100},
            {'port_id': 'port-2', 'segmentation_id': 200}
        ]
        manager.neutron.network.get_trunk.return_value = trunk

        manager._remove_single_subport('trunk-1', 'chassis-1', 'physnet1', 100)

        mock_remove.assert_called_once_with(
            manager, 'trunk-1', 'port-1', 'chassis-1', 'physnet1', 100)

    @mock.patch.object(l2vni_trunk_manager.L2VNITrunkManager,
                       '_remove_subport', autospec=True)
    def test_remove_single_subport_skips_if_not_exists(self, mock_remove):
        """Test _remove_single_subport is idempotent."""
        manager = self._create_manager()

        trunk = mock.Mock()
        trunk.sub_ports = [
            {'port_id': 'port-1', 'segmentation_id': 100}
        ]
        manager.neutron.network.get_trunk.return_value = trunk

        manager._remove_single_subport('trunk-1', 'chassis-1', 'physnet1', 200)

        mock_remove.assert_not_called()
