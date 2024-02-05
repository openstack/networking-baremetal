# Copyright 2017 Mirantis, Inc.
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

from neutron.db import provisioning_blocks
from neutron.plugins.ml2 import driver_context
from neutron.tests.unit.plugins.ml2 import _test_mech_agent as base
from neutron_lib.api.definitions import portbindings
from neutron_lib import constants as n_const
from neutron_lib.plugins.ml2 import api
from oslo_config import fixture as config_fixture

from networking_baremetal import common
from networking_baremetal import config
from networking_baremetal import constants
from networking_baremetal import exceptions
from networking_baremetal.plugins.ml2 import baremetal_mech
from networking_baremetal.tests.unit.plugins.ml2 import utils as ml2_utils


class TestBaremetalMechDriver(base.AgentMechanismBaseTestCase):
    VIF_TYPE = portbindings.VIF_TYPE_OTHER
    VIF_DETAILS = None
    AGENT_TYPE = constants.BAREMETAL_AGENT_TYPE
    GOOD_CONFIGS = {
        'bridge_mappings': {'fake_physical_network': 'fake_physnet'}
    }
    BAD_CONFIGS = {
        'bridge_mappings': {'wrong_physical_network': 'wrong_physnet'}
    }
    AGENTS = [{'agent_type': AGENT_TYPE, 'alive': True,
               'configurations': GOOD_CONFIGS, 'host': 'host'}]
    AGENTS_DEAD = [
        {'agent_type': AGENT_TYPE, 'alive': False,
         'configurations': GOOD_CONFIGS, 'host': 'dead_host'}
    ]
    AGENTS_BAD = [
        {'agent_type': AGENT_TYPE, 'alive': False,
         'configurations': GOOD_CONFIGS, 'host': 'bad_host_1'},
        {'agent_type': AGENT_TYPE, 'alive': True,
         'configurations': BAD_CONFIGS, 'host': 'bad_host_2'}
    ]
    VNIC_TYPE = portbindings.VNIC_BAREMETAL

    def setUp(self):
        super(TestBaremetalMechDriver, self).setUp()
        self.driver = baremetal_mech.BaremetalMechanismDriver()
        self.driver.initialize()

    def _make_port_ctx(self, agents):
        segments = [{api.ID: 'local_segment_id',
                     api.PHYSICAL_NETWORK: 'fake_physical_network',
                     api.NETWORK_TYPE: n_const.TYPE_FLAT}]
        return base.FakePortContext(self.AGENT_TYPE, agents, segments,
                                    vnic_type=self.VNIC_TYPE)

    def test_initialize(self):
        self.assertEqual([portbindings.VNIC_BAREMETAL],
                         self.driver.supported_vnic_types)
        self.assertEqual(portbindings.VIF_TYPE_OTHER, self.driver.vif_type)

    def test_get_allowed_network_types(self):
        agent_mock = mock.Mock()
        allowed_network_types = self.driver.get_allowed_network_types(
            agent_mock)
        self.assertEqual(allowed_network_types,
                         [n_const.TYPE_FLAT, n_const.TYPE_VLAN])

    @mock.patch.object(provisioning_blocks, 'provisioning_complete',
                       autospec=True)
    def test_update_port_postcommit_not_bound(self, mpb_pc):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network()

        m_pc = mock.create_autospec(driver_context.PortContext)
        m_pc.current = ml2_utils.get_test_port(network_id=m_nc.current['id'])
        m_pc.network = m_nc

        self.driver.update_port_postcommit(m_pc)
        self.assertFalse(mpb_pc.called)

    @mock.patch.object(provisioning_blocks, 'provisioning_complete',
                       autospec=True)
    def test_update_port_postcommit_unsupported_vnic_type_not_bound(
            self, mpb_pc):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network()

        m_pc = mock.create_autospec(driver_context.PortContext)
        m_pc.current = ml2_utils.get_test_port(
            network_id=m_nc.current['id'], vnic_type=portbindings.VNIC_MACVTAP,
            vif_type=portbindings.VIF_TYPE_OTHER)
        m_pc.network = m_nc

        self.driver.update_port_postcommit(m_pc)
        self.assertFalse(mpb_pc.called)

    @mock.patch.object(provisioning_blocks, 'provisioning_complete',
                       autospec=True)
    def test_update_port_postcommit_supported_vnic_type_bound(
            self, mpb_pc):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network()

        m_pc = mock.create_autospec(driver_context.PortContext)
        m_pc.current = ml2_utils.get_test_port(
            network_id=m_nc.current['id'],
            vnic_type=portbindings.VNIC_BAREMETAL,
            vif_type=portbindings.VIF_TYPE_OTHER)
        m_pc._plugin_context = 'plugin_context'
        m_pc.network = m_nc

        self.driver.update_port_postcommit(m_pc)
        mpb_pc.assert_called_once_with('plugin_context', m_pc.current['id'],
                                       'port', 'BAREMETAL_DRV_ENTITIY')

    @mock.patch.object(provisioning_blocks, 'add_provisioning_component',
                       autospec=True)
    def test_bind_port_unsupported_network_type(self, mpb_pc):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VXLAN)

        m_pc = mock.create_autospec(driver_context.PortContext)
        m_pc.current = ml2_utils.get_test_port(
            network_id=m_nc.current['id'],
            vnic_type=portbindings.VNIC_BAREMETAL,
            vif_type=portbindings.VIF_TYPE_OTHER)
        m_pc.network = m_nc
        m_pc.segments_to_bind = [
            ml2_utils.get_test_segment(network_type=n_const.TYPE_VXLAN)]

        self.driver.bind_port(m_pc)
        self.assertFalse(mpb_pc.called)

    @mock.patch.object(provisioning_blocks, 'add_provisioning_component',
                       autospec=True)
    def test_bind_port_unsupported_vnic_type(self, mpb_pc):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_FLAT)

        m_pc = mock.create_autospec(driver_context.PortContext)
        m_pc.current = ml2_utils.get_test_port(
            network_id=m_nc.current['id'], vnic_type='unsupported')
        m_pc.network = m_nc
        m_pc.segments_to_bind = [
            ml2_utils.get_test_segment(network_type=n_const.TYPE_FLAT)]

        self.driver.bind_port(m_pc)
        self.assertFalse(mpb_pc.called)

    def test_empty_methods(self):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network()

        m_pc = mock.create_autospec(driver_context.PortContext)
        m_pc.current = ml2_utils.get_test_port(network_id=m_nc.current['id'])
        m_pc.network = m_nc.current

        m_sc = mock.create_autospec(driver_context.SubnetContext)
        m_sc.current = ml2_utils.get_test_subnet(
            network_id=m_nc.current['id'])
        m_sc.network = m_nc

        self.driver.create_network_precommit(m_nc)
        self.driver.create_network_postcommit(m_nc)
        self.driver.update_network_precommit(m_nc)
        self.driver.update_network_postcommit(m_nc)
        self.driver.delete_network_precommit(m_nc)
        self.driver.delete_network_postcommit(m_nc)
        self.driver.create_subnet_precommit(m_sc)
        self.driver.create_subnet_postcommit(m_sc)
        self.driver.update_subnet_precommit(m_sc)
        self.driver.update_subnet_postcommit(m_sc)
        self.driver.delete_subnet_precommit(m_sc)
        self.driver.delete_subnet_postcommit(m_sc)
        self.driver.create_port_precommit(m_pc)
        self.driver.create_port_postcommit(m_pc)
        self.driver.update_port_precommit(m_pc)
        self.driver.update_port_postcommit(m_pc)
        self.driver.delete_port_precommit(m_pc)
        self.driver.delete_port_postcommit(m_pc)


class TestBaremetalMechDriverFakeDriver(base.AgentMechanismBaseTestCase):
    VIF_TYPE = portbindings.VIF_TYPE_OTHER
    VIF_DETAILS = None
    AGENT_TYPE = constants.BAREMETAL_AGENT_TYPE
    AGENT_CONF = {'bridge_mappings': {'fake_physical_network': 'fake_physnet'}}
    AGENTS = [{'agent_type': AGENT_TYPE, 'alive': True,
               'configurations': AGENT_CONF, 'host': 'host'}]
    VNIC_TYPE = portbindings.VNIC_BAREMETAL

    def setUp(self):
        super(TestBaremetalMechDriverFakeDriver, self).setUp()
        mock_manager = mock.patch.object(common, 'driver_mgr', autospec=True)
        self.mock_manager = mock_manager.start()
        self.addCleanup(mock_manager.stop)

        self.mock_driver = mock.MagicMock()

        self.mock_manager.return_value = self.mock_driver

        self.conf = self.useFixture(config_fixture.Config())
        self.conf.config(enabled_devices=['foo'],
                         group='networking_baremetal')
        self.conf.register_opts(config._opts + config._device_opts,
                                group='foo')
        self.conf.config(driver='test-driver',
                         switch_id='aa:bb:cc:dd:ee:ff',
                         switch_info='foo',
                         physical_networks=['fake_physical_network'],
                         group='foo')

        self.driver = baremetal_mech.BaremetalMechanismDriver()
        self.driver.initialize()

        self.mock_manager.assert_called_once_with('foo')
        self.mock_driver.load_config.assert_called_once()
        self.mock_driver.validate.assert_called_once()
        self.mock_manager.reset_mock()
        self.mock_driver.reset_mock()

    def _make_port_ctx(self, agents, profile):
        segments = [{api.ID: 'local_segment_id',
                     api.PHYSICAL_NETWORK: 'fake_physical_network',
                     api.NETWORK_TYPE: n_const.TYPE_FLAT}]
        return base.FakePortContext(self.AGENT_TYPE, agents, segments,
                                    vnic_type=self.VNIC_TYPE, profile=profile)

    def test__is_bound(self):
        m_pc = mock.create_autospec(driver_context.PortContext)
        m_pc.current = ml2_utils.get_test_port(
            network_id='network-id',
            vnic_type=portbindings.VNIC_BAREMETAL,
            vif_type=None)
        self.assertFalse(self.driver._is_bound(m_pc.current))
        m_pc.current[portbindings.VIF_TYPE] = portbindings.VIF_TYPE_OTHER
        self.assertTrue(self.driver._is_bound(m_pc.current))

    def test_create_network_postcommit_flat(self):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_FLAT)
        self.driver.create_network_postcommit(m_nc)
        self.mock_manager.assert_not_called()
        self.mock_driver.assert_not_called()

    def test_update_network_postcommit_flat(self):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_FLAT)
        self.driver.update_network_postcommit(m_nc)
        self.mock_manager.assert_not_called()
        self.mock_driver.assert_not_called()

    def test_delete_network_postcommit_flat(self):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_FLAT)
        self.driver.delete_network_postcommit(m_nc)
        self.mock_manager.assert_not_called()
        self.mock_driver.assert_not_called()

    def test_create_network_postcommit_vlan(self):
        # VLAN but no segmentation ID
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN)
        self.driver.create_network_postcommit(m_nc)
        self.mock_manager.assert_not_called()
        self.mock_manager.assert_not_called()

        # VLAN with segmentation ID, but not on physical network
        self.mock_manager.reset_mock()
        self.mock_driver.reset_mock()
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN,
            segmentation_id=10)
        self.driver.create_network_postcommit(m_nc)
        self.mock_manager.assert_not_called()
        self.mock_driver.assert_not_called()

        # VLAN with segmentation ID, on physical network
        self.mock_manager.reset_mock()
        self.mock_driver.reset_mock()
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN,
            segmentation_id=10,
            physical_network='fake_physical_network')
        self.driver.create_network_postcommit(m_nc)
        self.mock_manager.assert_called_once_with('foo')
        self.mock_driver.create_network.assert_called_once_with(m_nc)

        # Device VLAN management disabled in config
        self.conf.config(manage_vlans=False, group='foo')
        self.mock_manager.reset_mock()
        self.mock_driver.reset_mock()
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN,
            segmentation_id=10,
            physical_network='fake_physical_network')
        self.driver.create_network_postcommit(m_nc)
        self.mock_manager.assert_not_called()
        self.mock_driver.assert_not_called()

    def test_update_network_postcommit_vlan(self):
        # VLAN but no segmentation ID
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN)
        m_nc.original = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN)
        self.driver.update_network_postcommit(m_nc)
        self.mock_manager.assert_not_called()
        self.mock_driver.assert_not_called()

        # With physical network
        self.mock_manager.reset_mock()
        self.mock_driver.reset_mock()
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN,
            segmentation_id=10,
            physical_network='fake_physical_network')
        self.driver.update_network_postcommit(m_nc)
        self.mock_manager.assert_called_once_with('foo')
        self.mock_driver.update_network.assert_called_once_with(m_nc)

        # VLAN management disabled
        self.mock_manager.reset_mock()
        self.mock_driver.reset_mock()
        self.conf.config(manage_vlans=False,
                         group='foo')
        self.driver.update_network_postcommit(m_nc)
        self.mock_manager.assert_not_called()
        self.mock_driver.assert_not_called()

        # Device not on physical network
        self.mock_manager.reset_mock()
        self.mock_driver.reset_mock()
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN,
            segmentation_id=10,
            physical_network='fake_physical_network')
        self.conf.config(physical_networks=['not-connected-physnet'],
                         manage_vlans=True,
                         group='foo')
        self.driver.update_network_postcommit(m_nc)
        self.mock_manager.assert_not_called()
        self.mock_driver.assert_not_called()

    def test_delete_network_postcommit_vlan(self):
        # VLAN but no segmentation ID
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN)
        m_nc.original = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN)
        self.driver.delete_network_postcommit(m_nc)
        self.mock_manager.assert_not_called()
        self.mock_driver.assert_not_called()

        # VLAN ID and matching physnet
        self.mock_manager.reset_mock()
        self.mock_driver.reset_mock()
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN,
            segmentation_id=10,
            physical_network='fake_physical_network')
        self.driver.delete_network_postcommit(m_nc)
        self.mock_manager.assert_called_once_with('foo')
        self.mock_driver.delete_network.assert_called_once_with(m_nc)

        # Not on physnet
        self.mock_manager.reset_mock()
        self.mock_driver.reset_mock()
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN,
            segmentation_id=10,
            physical_network='not-on-physnet')
        self.driver.delete_network_postcommit(m_nc)
        self.mock_manager.assert_not_called()
        self.mock_driver.assert_not_called()

        # VLAN management disabled
        self.mock_manager.reset_mock()
        self.mock_driver.reset_mock()
        self.conf.config(manage_vlans=False,
                         group='foo')
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN,
            segmentation_id=10,
            physical_network='fake_physical_network')
        self.driver.delete_network_postcommit(m_nc)
        self.mock_manager.assert_not_called()
        self.mock_driver.assert_not_called()

    @mock.patch.object(provisioning_blocks, 'add_provisioning_component',
                       autospec=True)
    def test_bind_port(self, mock_p_blocks):
        binding_profile = {}
        lli = binding_profile['local_link_information'] = []
        lli.append({'port_id': 'test1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                    'switch_info': 'foo'})
        context = self._make_port_ctx(self.AGENTS, binding_profile)
        context._plugin_context = 'plugin_context'
        self.assertIsNone(context._bound_vif_type)
        self.driver.bind_port(context)
        self.mock_manager.assert_called_once_with('foo')
        self.mock_driver.create_port.assert_called_once_with(
            context, context.segments_to_bind[0], lli)
        self.assertEqual(context._bound_vif_type, self.driver.vif_type)

    @mock.patch.object(provisioning_blocks, 'add_provisioning_component',
                       autospec=True)
    def test_no_device_does_bind_port(self, mock_p_blocks):
        binding_profile = {}
        lli = binding_profile['local_link_information'] = []
        lli.append({'port_id': 'test1/1', 'switch_id': '11:11:11:11:11:11',
                    'switch_info': 'not-such-device'})
        context = self._make_port_ctx(self.AGENTS, binding_profile)
        context._plugin_context = 'plugin_context'
        self.assertIsNone(context._bound_vif_type)
        self.driver.bind_port(context)
        self.mock_manager.assert_not_called()
        self.mock_driver.create_port.assert_not_called()
        self.assertIsNone(context._bound_vif_type)
        mock_p_blocks.assert_not_called()

    @mock.patch.object(provisioning_blocks, 'add_provisioning_component',
                       autospec=True)
    def test_driver_load_error_does_not_bind_port(self, mock_p_blocks):
        binding_profile = {}
        lli = binding_profile['local_link_information'] = []
        lli.append({'port_id': 'test1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                    'switch_info': 'foo'})
        context = self._make_port_ctx(self.AGENTS, binding_profile)
        context._plugin_context = 'plugin_context'
        self.mock_manager.side_effect = exceptions.DriverEntrypointLoadError(
            entry_point='entry_point', err='ERROR_MSG'
        )
        self.assertIsNone(context._bound_vif_type)
        self.driver.bind_port(context)
        self.mock_manager.assert_called_once_with('foo')
        self.mock_driver.create_port.assert_not_called()
        # The port will not bind
        self.assertIsNone(context._bound_vif_type)

    @mock.patch.object(provisioning_blocks, 'add_provisioning_component',
                       autospec=True)
    def test_not_on_physnet_does_not_bind_port(self, mock_p_blocks):
        binding_profile = {}
        lli = binding_profile['local_link_information'] = []
        lli.append({'port_id': 'test1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                    'switch_info': 'foo'})
        context = self._make_port_ctx(self.AGENTS, binding_profile)
        context._plugin_context = 'plugin_context'
        self.conf.config(physical_networks='other_physnet',
                         group='foo')
        self.assertIsNone(context._bound_vif_type)
        self.driver.bind_port(context)
        self.mock_manager.assert_not_called()
        self.mock_driver.create_port.assert_not_called()
        # The port will not bind
        self.assertIsNone(context._bound_vif_type)

    @mock.patch.object(provisioning_blocks, 'add_provisioning_component',
                       autospec=True)
    def test_bond_mode_supported_bind_port(self, mock_p_blocks):
        binding_profile = {}
        lli = binding_profile['local_link_information'] = []
        llg = binding_profile['local_group_information'] = {}
        llg['bond_mode'] = '802.3ad'
        lli.append({'port_id': 'test1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                    'switch_info': 'foo'})
        context = self._make_port_ctx(self.AGENTS, binding_profile)
        context._plugin_context = 'plugin_context'
        self.mock_driver.SUPPORTED_BOND_MODES = {'802.3ad'}
        self.assertIsNone(context._bound_vif_type)
        self.driver.bind_port(context)
        self.mock_manager.assert_called_once_with('foo')
        self.mock_driver.create_port.assert_called_once_with(
            context, context.segments_to_bind[0], lli)
        self.assertEqual(context._bound_vif_type, self.driver.vif_type)

    @mock.patch.object(provisioning_blocks, 'add_provisioning_component',
                       autospec=True)
    def test_bond_mode_unsupported_does_not_bind_port(self, mock_p_blocks):
        binding_profile = {}
        lli = binding_profile['local_link_information'] = []
        llg = binding_profile['local_group_information'] = {}
        llg['bond_mode'] = 'unsupported'
        lli.append({'port_id': 'test1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                    'switch_info': 'foo'})
        context = self._make_port_ctx(self.AGENTS, binding_profile)
        context._plugin_context = 'plugin_context'
        self.mock_driver.SUPPORTED_BOND_MODES = {'802.3ad'}
        self.assertIsNone(context._bound_vif_type)
        self.driver.bind_port(context)
        self.mock_manager.assert_called_once_with('foo')
        self.mock_driver.create_port.assert_not_called()
        self.assertIsNone(context._bound_vif_type)

    @mock.patch.object(provisioning_blocks, 'add_provisioning_component',
                       autospec=True)
    def test_no_port_id_does_not_bind_port(self, mock_p_blocks):
        binding_profile = {}
        lli = binding_profile['local_link_information'] = []
        lli.append({'switch_id': 'aa:bb:cc:dd:ee:ff',
                    'switch_info': 'foo'})
        context = self._make_port_ctx(self.AGENTS, binding_profile)
        context._plugin_context = 'plugin_context'
        self.assertIsNone(context._bound_vif_type)
        self.driver.bind_port(context)
        self.mock_manager.assert_not_called()
        self.mock_driver.create_port.assert_not_called()
        self.assertIsNone(context._bound_vif_type)

    @mock.patch.object(provisioning_blocks, 'provisioning_complete',
                       autospec=True)
    def test_port_bound_update_port(self, mock_p_blocks):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network()
        m_nc.original = ml2_utils.get_test_network()
        m_pc = mock.create_autospec(driver_context.PortContext)
        m_pc.current = ml2_utils.get_test_port(
            network_id=m_nc.current['id'],
            vnic_type=portbindings.VNIC_BAREMETAL,
            vif_type=portbindings.VIF_TYPE_OTHER)
        m_pc.original = ml2_utils.get_test_port(
            network_id=m_nc.current['id'],
            vnic_type=portbindings.VNIC_BAREMETAL,
            vif_type=portbindings.VIF_TYPE_OTHER)
        m_pc.network = m_nc
        m_pc._plugin_context = 'plugin_context'
        m_pc._bound_vif_type = self.driver.vif_type
        self.driver.update_port_postcommit(m_pc)
        self.mock_manager.assert_called_once_with('foo')
        self.mock_driver.update_port.assert_called_once_with(
            m_pc, m_pc.current['binding:profile']['local_link_information'])

    def test_port_unbound_unplug_port(self):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network()
        m_nc.original = ml2_utils.get_test_network()
        m_pc = mock.create_autospec(driver_context.PortContext)
        m_pc.current = ml2_utils.get_test_port(
            network_id=m_nc.current['id'],
            vnic_type=None,
            vif_type=None)
        m_pc.original = ml2_utils.get_test_port(
            network_id=m_nc.current['id'],
            vnic_type=portbindings.VNIC_BAREMETAL,
            vif_type=portbindings.VIF_TYPE_OTHER)
        m_pc.network = m_nc
        self.driver.update_port_postcommit(m_pc)
        self.mock_manager.assert_called_once_with('foo')
        self.mock_driver.delete_port.assert_called_once_with(
            m_pc, m_pc.current['binding:profile']['local_link_information'],
            current=False)

    def test_delete_port(self):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network()
        m_pc = mock.create_autospec(driver_context.PortContext)
        m_pc.current = ml2_utils.get_test_port(
            network_id=m_nc.current['id'],
            vnic_type=portbindings.VNIC_BAREMETAL,
            vif_type=portbindings.VIF_TYPE_OTHER)
        m_pc.network = m_nc
        self.driver.delete_port_postcommit(m_pc)
        self.mock_manager.assert_called_once_with('foo')
        self.mock_driver.delete_port.assert_called_once_with(
            m_pc, m_pc.current['binding:profile']['local_link_information'],
            current=True)
