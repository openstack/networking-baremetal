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

from neutron.db import provisioning_blocks
from neutron.objects import ports as port_objects
from neutron.tests import base as tests_base
from neutron_lib.api.definitions import portbindings
from neutron_lib.callbacks import resources
from neutron_lib import constants as n_const
from neutron_lib import exceptions as n_exc
from neutron_lib.plugins.ml2 import api
from oslo_config import cfg

from networking_baremetal.plugins.ml2 import baremetal_l2vni_mapping
from networking_baremetal.tests.unit.plugins.ml2 import utils as ml2_utils


class TestL2vniMechanismDriver(tests_base.BaseTestCase):
    """Test cases for L2VNI Mechanism Driver"""

    def setUp(self):
        super(TestL2vniMechanismDriver, self).setUp()
        self.driver = baremetal_l2vni_mapping.L2vniMechanismDriver()
        self.driver.initialize()

    def test_initialize(self):
        """Test driver initialization"""
        self.assertEqual(portbindings.CONNECTIVITY_L2,
                         self.driver.connectivity)

    def test_get_ovn_client_success(self):
        """Test successful OVN client retrieval"""
        mock_plugin = mock.Mock()
        mock_driver = mock.Mock()
        mock_driver.obj._ovn_client = mock.Mock()
        mock_plugin.mechanism_manager.ordered_mech_drivers = [mock_driver]

        with mock.patch('neutron_lib.plugins.directory.get_plugin',
                        autospec=True, return_value=mock_plugin):
            result = self.driver._get_ovn_client
            self.assertIsNotNone(result)
            self.assertEqual(mock_driver.obj._ovn_client, result)

    def test_get_ovn_client_no_mechanism_manager(self):
        """Test OVN client retrieval when plugin has no mechanism_manager"""
        mock_plugin = mock.Mock(spec=[])

        with mock.patch('neutron_lib.plugins.directory.get_plugin',
                        autospec=True, return_value=mock_plugin):
            result = self.driver._get_ovn_client
            self.assertIsNone(result)

    def test_get_ovn_client_no_ovn_driver(self):
        """Test OVN client retrieval when OVN driver not found"""
        mock_plugin = mock.Mock()
        mock_driver = mock.Mock(spec=[])
        mock_plugin.mechanism_manager.ordered_mech_drivers = [mock_driver]

        with mock.patch('neutron_lib.plugins.directory.get_plugin',
                        autospec=True, return_value=mock_plugin):
            result = self.driver._get_ovn_client
            self.assertIsNone(result)

    def test_chassis_can_forward_physnet_true(self):
        """Test chassis can forward physnet returns True"""
        mock_ovn_client = mock.Mock()

        # Mock chassis 1 with physnet1
        mock_chassis1 = mock.Mock()
        mock_chassis1.name = 'chassis-1'
        mock_chassis1.external_ids = {
            'ovn-bridge-mappings': 'physnet1:br-provider,physnet2:br-ex'
        }

        # Mock chassis 2 without our physnet
        mock_chassis2 = mock.Mock()
        mock_chassis2.name = 'chassis-2'
        mock_chassis2.external_ids = {
            'ovn-bridge-mappings': 'physnet3:br-other'
        }

        mock_ovn_client._sb_idl.tables = {
            'Chassis': mock.Mock(rows=mock.Mock(
                values=mock.Mock(return_value=[mock_chassis1, mock_chassis2])
            ))
        }

        result = self.driver._chassis_can_forward_physnet(
            mock_ovn_client, 'physnet1')
        self.assertTrue(result)

    def test_chassis_can_forward_physnet_false(self):
        """Test chassis can forward physnet returns False"""
        mock_ovn_client = mock.Mock()

        # Mock chassis without the requested physnet
        mock_chassis = mock.Mock()
        mock_chassis.name = 'test-chassis'
        mock_chassis.external_ids = {
            'ovn-bridge-mappings': 'physnet1:br-provider'
        }

        mock_ovn_client._sb_idl.tables = {
            'Chassis': mock.Mock(rows=mock.Mock(
                values=mock.Mock(return_value=[mock_chassis])
            ))
        }

        result = self.driver._chassis_can_forward_physnet(
            mock_ovn_client, 'physnet2')
        self.assertFalse(result)

    def test_chassis_can_forward_physnet_no_sb_connection(self):
        """Test chassis can forward physnet when no SB connection"""
        mock_ovn_client = mock.Mock(spec=[])  # No _sb_idl attribute

        result = self.driver._chassis_can_forward_physnet(
            mock_ovn_client, 'physnet1')
        # Should fail open and return True
        self.assertTrue(result)


class TestL2vniPortBinding(tests_base.BaseTestCase):
    """Test cases for port binding functionality"""

    def setUp(self):
        super(TestL2vniPortBinding, self).setUp()
        self.driver = baremetal_l2vni_mapping.L2vniMechanismDriver()
        self.driver.initialize()
        self.context = self._create_port_context()

    def _create_port_context(self, vnic_type=portbindings.VNIC_BAREMETAL,
                             network_type=n_const.TYPE_VXLAN,
                             physnet='physnet1',
                             vlan_id=100):
        """Create a mock port context"""
        mock_context = mock.Mock()

        # Setup current port
        network = ml2_utils.get_test_network()
        port = ml2_utils.get_test_port(
            network['id'],
            vnic_type=vnic_type,
            binding_profile={'physical_network': physnet}
        )
        mock_context.current = port

        # Setup network segments
        overlay_segment = {
            api.ID: 'overlay-segment-id',
            api.NETWORK_TYPE: network_type,
            api.SEGMENTATION_ID: 5000,
            api.PHYSICAL_NETWORK: None
        }

        vlan_segment = {
            api.ID: 'vlan-segment-id',
            api.NETWORK_TYPE: n_const.TYPE_VLAN,
            api.SEGMENTATION_ID: vlan_id,
            api.PHYSICAL_NETWORK: physnet
        }

        mock_network = mock.Mock()
        mock_network.network_segments = [overlay_segment]
        mock_network.current = network
        mock_context.network = mock_network

        mock_context.segments_to_bind = [overlay_segment]
        mock_context.top_bound_segment = None
        mock_context.bottom_bound_segment = None
        mock_context.original_bottom_bound_segment = None

        # Mock methods
        mock_context.allocate_dynamic_segment.return_value = vlan_segment
        mock_context.is_partial_segment.return_value = False
        mock_context.continue_binding = mock.Mock()

        return mock_context

    def test_bind_port_unsupported_vnic_type(self):
        """Test bind_port skips unsupported VNIC types"""
        context = self._create_port_context(
            vnic_type=portbindings.VNIC_NORMAL)
        self.driver.bind_port(context)
        context.continue_binding.assert_not_called()

    def test_bind_port_non_overlay_network(self):
        """Test bind_port skips non-overlay networks (flat, vlan, etc)"""
        context = self._create_port_context(
            network_type=n_const.TYPE_FLAT)
        self.driver.bind_port(context)
        context.continue_binding.assert_not_called()

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_ensure_localnet_port', autospec=True)
    def test_bind_port_geneve_network(self, mock_ensure_localnet):
        """Test bind_port processes Geneve networks"""
        context = self._create_port_context(
            network_type=n_const.TYPE_GENEVE)
        self.driver.bind_port(context)

        # Should allocate dynamic segment for Geneve
        context.allocate_dynamic_segment.assert_called_once()
        call_args = context.allocate_dynamic_segment.call_args[0][0]
        self.assertEqual('physnet1', call_args[api.PHYSICAL_NETWORK])
        self.assertEqual(n_const.TYPE_VLAN, call_args[api.NETWORK_TYPE])

        # Should ensure localnet port
        mock_ensure_localnet.assert_called_once()

        # Should continue binding
        context.continue_binding.assert_called_once()

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_ensure_localnet_port', autospec=True)
    def test_bind_port_allocates_new_segment(self, mock_ensure_localnet):
        """Test bind_port allocates new VLAN segment"""
        context = self._create_port_context()
        self.driver.bind_port(context)

        # Should allocate dynamic segment
        context.allocate_dynamic_segment.assert_called_once()
        call_args = context.allocate_dynamic_segment.call_args[0][0]
        self.assertEqual('physnet1', call_args[api.PHYSICAL_NETWORK])
        self.assertEqual(n_const.TYPE_VLAN, call_args[api.NETWORK_TYPE])

        # Should ensure localnet port
        mock_ensure_localnet.assert_called_once()

        # Should continue binding
        context.continue_binding.assert_called_once()

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_ensure_localnet_port', autospec=True)
    def test_bind_port_reuses_existing_segment(self, mock_ensure_localnet):
        """Test bind_port reuses existing VLAN segment"""
        context = self._create_port_context()

        # Add existing VLAN segment to network
        vlan_segment = {
            api.ID: 'existing-vlan-segment',
            api.NETWORK_TYPE: n_const.TYPE_VLAN,
            api.SEGMENTATION_ID: 200,
            api.PHYSICAL_NETWORK: 'physnet1'
        }
        context.network.network_segments.append(vlan_segment)

        self.driver.bind_port(context)

        # Should NOT allocate new segment
        context.allocate_dynamic_segment.assert_not_called()

        # Should still ensure localnet port
        mock_ensure_localnet.assert_called_once()

        # Should continue binding
        context.continue_binding.assert_called_once()

    def test_bind_port_partial_segment_error(self):
        """Test bind_port raises error for partial segment"""
        context = self._create_port_context()

        # Add partial VLAN segment (no segmentation_id)
        partial_segment = {
            api.ID: 'partial-vlan-segment',
            api.NETWORK_TYPE: n_const.TYPE_VLAN,
            api.SEGMENTATION_ID: None,
            api.PHYSICAL_NETWORK: 'physnet1'
        }
        context.network.network_segments.append(partial_segment)
        context.is_partial_segment.return_value = True

        self.assertRaises(n_exc.InvalidInput,
                          self.driver.bind_port, context)

    def test_bind_port_missing_physnet_no_default(self):
        """Test bind_port raises error when physnet missing and no default"""
        context = self._create_port_context()
        context.current[portbindings.PROFILE] = {}

        # No default configured
        cfg.CONF.set_override('default_physical_network', None,
                              group='baremetal_l2vni')

        # Should raise InvalidInput exception
        self.assertRaises(n_exc.InvalidInput,
                          self.driver.bind_port, context)

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_ensure_localnet_port', autospec=True)
    def test_bind_port_uses_default_physnet(self, mock_ensure_localnet):
        """Test bind_port uses default physnet when missing from profile"""
        context = self._create_port_context()
        context.current[portbindings.PROFILE] = {}

        # Configure default physical network
        cfg.CONF.set_override('default_physical_network', 'default-physnet',
                              group='baremetal_l2vni')

        self.driver.bind_port(context)

        # Should allocate segment with default physnet
        context.allocate_dynamic_segment.assert_called_once()
        call_args = context.allocate_dynamic_segment.call_args[0][0]
        self.assertEqual('default-physnet', call_args[api.PHYSICAL_NETWORK])
        self.assertEqual(n_const.TYPE_VLAN, call_args[api.NETWORK_TYPE])

        # Should ensure localnet port
        mock_ensure_localnet.assert_called_once()

        # Should continue binding
        context.continue_binding.assert_called_once()

    def test_bind_port_profile_physnet_overrides_default(self):
        """Test bind_port prefers profile physnet over default"""
        context = self._create_port_context(physnet='profile-physnet')

        # Configure default physical network
        cfg.CONF.set_override('default_physical_network', 'default-physnet',
                              group='baremetal_l2vni')

        with mock.patch.object(self.driver, '_ensure_localnet_port',
                               autospec=True):
            self.driver.bind_port(context)

        # Should use profile physnet, not default
        context.allocate_dynamic_segment.assert_called_once()
        call_args = context.allocate_dynamic_segment.call_args[0][0]
        self.assertEqual('profile-physnet', call_args[api.PHYSICAL_NETWORK])

    def test_bind_port_segment_allocation_fails(self):
        """Test bind_port raises error when segment allocation fails"""
        context = self._create_port_context()

        # Mock allocation returning None (failure)
        context.allocate_dynamic_segment.return_value = None

        # Should raise InvalidInput exception
        self.assertRaises(n_exc.InvalidInput,
                          self.driver.bind_port, context)

    def test_bind_port_allocated_segment_missing_vlan_id(self):
        """Test bind_port raises error when segment lacks segmentation_id"""
        context = self._create_port_context()

        # Mock allocation returning segment without segmentation_id
        invalid_segment = {
            api.ID: 'invalid-segment',
            api.NETWORK_TYPE: n_const.TYPE_VLAN,
            api.SEGMENTATION_ID: None,
            api.PHYSICAL_NETWORK: 'physnet1'
        }
        context.allocate_dynamic_segment.return_value = invalid_segment

        # Should raise InvalidInput exception
        self.assertRaises(n_exc.InvalidInput,
                          self.driver.bind_port, context)


class TestL2vniLocalnetPort(tests_base.BaseTestCase):
    """Test cases for localnet port management"""

    def setUp(self):
        super(TestL2vniLocalnetPort, self).setUp()
        self.driver = baremetal_l2vni_mapping.L2vniMechanismDriver()
        self.driver.initialize()

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_get_ovn_client', new_callable=mock.PropertyMock)
    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_chassis_can_forward_physnet', autospec=True)
    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_ensure_router_gateway_chassis', autospec=True)
    def test_ensure_localnet_port_success(self, mock_router_gw,
                                          mock_can_forward,
                                          mock_get_client):
        """Test successful localnet port creation"""
        mock_context = mock.Mock()
        mock_ovn_client = mock.Mock()
        mock_get_client.return_value = mock_ovn_client
        mock_can_forward.return_value = True

        # Mock that port doesn't exist
        mock_ovn_client._nb_idl.lsp_get.return_value.execute.return_value = (
            None)

        network_id = 'test-network-id'
        physnet = 'physnet1'
        vlan_id = 100

        self.driver._ensure_localnet_port(
            mock_context, network_id, physnet, vlan_id)

        # Should create localnet port
        mock_ovn_client._nb_idl.create_lswitch_port.assert_called_once()
        mock_ovn_client._transaction.assert_called_once()

        # Should ensure router gateway chassis
        mock_router_gw.assert_called_once_with(self.driver, mock_ovn_client,
                                               network_id)

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_get_ovn_client', new_callable=mock.PropertyMock)
    def test_ensure_localnet_port_disabled_by_config(self, mock_get_client):
        """Test localnet port creation skipped when disabled by config"""
        cfg.CONF.set_override('create_localnet_ports', False,
                              group='baremetal_l2vni')

        mock_context = mock.Mock()
        network_id = 'test-network-id'
        physnet = 'physnet1'
        vlan_id = 100

        self.driver._ensure_localnet_port(
            mock_context, network_id, physnet, vlan_id)

        # Should not get OVN client
        mock_get_client.assert_not_called()

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_get_ovn_client', new_callable=mock.PropertyMock)
    def test_ensure_localnet_port_no_ovn_client(self, mock_get_client):
        """Test localnet port creation when OVN client unavailable"""
        mock_get_client.return_value = None
        mock_context = mock.Mock()

        self.driver._ensure_localnet_port(
            mock_context, 'network-id', 'physnet1', 100)

        # Should return early without error

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_get_ovn_client', new_callable=mock.PropertyMock)
    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_chassis_can_forward_physnet', autospec=True)
    def test_ensure_localnet_port_chassis_cannot_forward(
            self,
            mock_can_forward,
            mock_get_client):
        """Test localnet port creation skipped when chassis can't forward"""
        mock_ovn_client = mock.Mock()
        mock_get_client.return_value = mock_ovn_client
        mock_can_forward.return_value = False

        mock_context = mock.Mock()

        self.driver._ensure_localnet_port(
            mock_context, 'network-id', 'physnet1', 100)

        # Should not attempt to create port
        mock_ovn_client._nb_idl.create_lswitch_port.assert_not_called()

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_get_ovn_client', new_callable=mock.PropertyMock)
    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_chassis_can_forward_physnet', autospec=True)
    def test_ensure_localnet_port_already_exists(self, mock_can_forward,
                                                 mock_get_client):
        """Test localnet port creation when port already exists"""
        mock_ovn_client = mock.Mock()
        mock_get_client.return_value = mock_ovn_client
        mock_can_forward.return_value = True

        # Mock that port already exists
        mock_ovn_client._nb_idl.lsp_get.return_value.execute.return_value = (
            mock.Mock())

        mock_context = mock.Mock()

        self.driver._ensure_localnet_port(
            mock_context, 'network-id', 'physnet1', 100)

        # Should not create new port
        mock_ovn_client._nb_idl.create_lswitch_port.assert_not_called()

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_get_ovn_client', new_callable=mock.PropertyMock)
    def test_remove_localnet_port_success(self, mock_get_client):
        """Test successful localnet port removal"""
        mock_ovn_client = mock.Mock()
        mock_get_client.return_value = mock_ovn_client

        # Mock that port exists
        mock_ovn_client._nb_idl.lsp_get.return_value.execute.return_value = (
            mock.Mock())

        mock_context = mock.Mock()
        network_id = 'test-network-id'
        physnet = 'physnet1'

        self.driver._remove_localnet_port(mock_context, network_id, physnet)

        # Should delete the port
        mock_ovn_client._nb_idl.lsp_del.return_value.execute\
            .assert_called_once()

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_get_ovn_client', new_callable=mock.PropertyMock)
    def test_remove_localnet_port_not_exists(self, mock_get_client):
        """Test localnet port removal when port doesn't exist"""
        mock_ovn_client = mock.Mock()
        mock_get_client.return_value = mock_ovn_client

        # Mock that port doesn't exist
        mock_ovn_client._nb_idl.lsp_get.return_value.execute.return_value = (
            None)

        mock_context = mock.Mock()

        self.driver._remove_localnet_port(
            mock_context, 'network-id', 'physnet1')

        # Should not attempt to delete
        mock_ovn_client._nb_idl.lsp_del.assert_not_called()


class TestL2vniPortUpdate(tests_base.BaseTestCase):
    """Test cases for port update functionality"""

    def setUp(self):
        super(TestL2vniPortUpdate, self).setUp()
        self.driver = baremetal_l2vni_mapping.L2vniMechanismDriver()
        self.driver.initialize()

    def test_update_port_postcommit_unsupported_vnic_type(self):
        """Test update_port_postcommit skips unsupported VNIC types"""
        mock_context = mock.Mock()
        mock_context.current = {
            portbindings.VNIC_TYPE: portbindings.VNIC_NORMAL,
            portbindings.VIF_TYPE: portbindings.VIF_TYPE_UNBOUND
        }

        self.driver.update_port_postcommit(mock_context)
        # Should return early without error

    @mock.patch.object(port_objects.PortBindingLevel, 'get_objects',
                       autospec=True)
    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_remove_localnet_port', autospec=True)
    def test_update_port_postcommit_release_segment(self, mock_remove_port,
                                                    mock_get_objects):
        """Test update_port_postcommit releases dynamic segment"""
        mock_context = mock.Mock()
        network = ml2_utils.get_test_network()

        mock_context.current = {
            portbindings.VNIC_TYPE: portbindings.VNIC_BAREMETAL,
            portbindings.VIF_TYPE: portbindings.VIF_TYPE_UNBOUND
        }

        segment = {
            api.ID: 'segment-id',
            api.NETWORK_TYPE: n_const.TYPE_VLAN,
            api.SEGMENTATION_ID: 100,
            api.PHYSICAL_NETWORK: 'physnet1'
        }
        mock_context.original_bottom_bound_segment = segment
        mock_network = mock.Mock()
        mock_network.current = network
        mock_context.network = mock_network

        # No other ports using this segment
        mock_get_objects.return_value = []

        self.driver.update_port_postcommit(mock_context)

        # Should remove localnet port and release segment
        mock_remove_port.assert_called_once()
        mock_context.release_dynamic_segment.assert_called_once_with(
            'segment-id')

    @mock.patch.object(provisioning_blocks, 'provisioning_complete',
                       autospec=True)
    def test_update_port_postcommit_complete_provisioning(self, mock_pc):
        """Test update_port_postcommit completes provisioning blocks"""
        mock_context = mock.Mock()
        port = ml2_utils.get_test_port(
            'network-id',
            vnic_type=portbindings.VNIC_BAREMETAL,
            vif_type=portbindings.VIF_TYPE_OTHER
        )
        mock_context.current = port

        self.driver.update_port_postcommit(mock_context)

        # Should complete provisioning
        mock_pc.assert_called_once_with(
            mock_context._plugin_context,
            port['id'],
            resources.PORT,
            'L2'
        )


class TestL2vniRouterGateway(tests_base.BaseTestCase):
    """Test cases for router gateway chassis management"""

    def setUp(self):
        super(TestL2vniRouterGateway, self).setUp()
        self.driver = baremetal_l2vni_mapping.L2vniMechanismDriver()
        self.driver.initialize()

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_get_local_chassis', autospec=True)
    def test_ensure_router_gateway_chassis_disabled(self, mock_get_chassis):
        """Test router gateway chassis when disabled by config"""
        cfg.CONF.set_override('create_localnet_ports', False,
                              group='baremetal_l2vni')

        mock_ovn_client = mock.Mock()
        self.driver._ensure_router_gateway_chassis(
            mock_ovn_client, 'network-id')

        # Should return early
        mock_get_chassis.assert_not_called()

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_get_local_chassis', autospec=True)
    def test_ensure_router_gateway_chassis_no_chassis(self,
                                                      mock_get_chassis):
        """Test router gateway chassis when chassis not found"""
        mock_get_chassis.return_value = None
        mock_ovn_client = mock.Mock()

        self.driver._ensure_router_gateway_chassis(
            mock_ovn_client, 'network-id')

        # Should log warning but not raise error

    @mock.patch.object(baremetal_l2vni_mapping.L2vniMechanismDriver,
                       '_get_local_chassis', autospec=True)
    def test_ensure_router_gateway_chassis_no_nb_idl(self, mock_get_chassis):
        """Test router gateway chassis when no northbound connection"""
        mock_chassis = mock.Mock()
        mock_chassis.name = 'test-chassis'
        mock_get_chassis.return_value = mock_chassis

        mock_ovn_client = mock.Mock(spec=[])  # No _nb_idl attribute

        self.driver._ensure_router_gateway_chassis(
            mock_ovn_client, 'network-id')

        # Should return early without error
