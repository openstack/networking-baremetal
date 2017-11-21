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


import mock

from neutron.db import provisioning_blocks
from neutron.plugins.ml2 import driver_context
from neutron.tests.unit.plugins.ml2 import _test_mech_agent as base
from neutron_lib.api.definitions import portbindings
from neutron_lib import constants as n_const
from neutron_lib.plugins.ml2 import api

from networking_baremetal import constants
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
    AGENTS = [{'alive': True, 'configurations': GOOD_CONFIGS, 'host': 'host'}]
    AGENTS_DEAD = [
        {'alive': False, 'configurations': GOOD_CONFIGS, 'host': 'dead_host'}
    ]
    AGENTS_BAD = [
        {'alive': False, 'configurations': GOOD_CONFIGS, 'host': 'bad_host_1'},
        {'alive': True, 'configurations': BAD_CONFIGS, 'host': 'bad_host_2'}
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
        self.assertEqual({}, self.driver.vif_details)

    @mock.patch.object(provisioning_blocks, 'add_provisioning_component')
    def test_bind_port(self, mpb_pc):
        port_context = self._make_port_ctx(self.AGENTS)
        port_context._plugin_context = 'plugin_context'
        self.assertEqual(port_context._bound_vif_type, None)
        self.driver.bind_port(port_context)
        self.assertEqual(port_context._bound_vif_type, self.driver.vif_type)

    def test_get_allowed_network_types(self):
        agent_mock = mock.Mock()
        allowed_network_types = self.driver.get_allowed_network_types(
            agent_mock)
        self.assertEqual(allowed_network_types,
                         [n_const.TYPE_FLAT, n_const.TYPE_VLAN])

    @mock.patch.object(provisioning_blocks, 'provisioning_complete')
    def test_update_port_postcommit_not_bound(self, mpb_pc):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network()

        m_pc = mock.create_autospec(driver_context.PortContext)
        m_pc.current = ml2_utils.get_test_port(network_id=m_nc.current['id'])
        m_pc.network = m_nc

        self.driver.update_port_postcommit(m_pc)
        self.assertFalse(mpb_pc.called)

    @mock.patch.object(provisioning_blocks, 'provisioning_complete')
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

    @mock.patch.object(provisioning_blocks, 'provisioning_complete')
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

    @mock.patch.object(provisioning_blocks, 'add_provisioning_component')
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

    @mock.patch.object(provisioning_blocks, 'add_provisioning_component')
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
