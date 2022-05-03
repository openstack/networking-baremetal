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
from xml.etree import ElementTree

from ncclient import manager
from neutron.plugins.ml2 import driver_context
from neutron_lib import constants as n_const
from neutron_lib.plugins.ml2 import api
from oslo_config import fixture as config_fixture
from oslo_utils import uuidutils

from networking_baremetal import config
from networking_baremetal import constants
from networking_baremetal.constants import NetconfEditConfigOperation as nc_op
from networking_baremetal.drivers.netconf import openconfig
from networking_baremetal.openconfig.interfaces import interfaces
from networking_baremetal.openconfig.lacp import lacp
from networking_baremetal.tests import base
from networking_baremetal.tests.unit.plugins.ml2 import utils as ml2_utils


OC_IF_NS = 'http://openconfig.net/yang/interfaces'
OC_IF_ETH_NS = 'http://openconfig.net/yang/interfaces/ethernet'
OC_IF_AGG_NS = 'http://openconfig.net/yang/interfaces/aggregate'
XML_IFACES_AGGREDATE_ID = f'''
<data>
  <interfaces xmlns="{OC_IF_NS}">
    <interface>
      <name>foo1/1</name>
      <ethernet xmlns="{OC_IF_ETH_NS}">
        <config>
          <aggregate-id xmlns="{OC_IF_AGG_NS}">Po10</aggregate-id>
        </config>
      </ethernet>
    </interface>
    <interface>
      <name>foo1/2</name>
      <ethernet xmlns="{OC_IF_ETH_NS}">
        <config>
          <aggregate-id xmlns="{OC_IF_AGG_NS}">Po10</aggregate-id>
        </config>
      </ethernet>
    </interface>
  </interfaces>
</data>
'''

XML_AGGREGATE_IFACES = f'''
<data>
  <interfaces xmlns="{OC_IF_NS}">
    <interface>
      <name>Po5</name>
    </interface>
    <interface>
      <name>Po7</name>
    </interface>
    <interface>
      <name>Po9</name>
    </interface>
    <interface>
      <name>foo1/1</name>
    </interface>
  </interfaces>
</data>
'''


class TestNetconfOpenConfigClient(base.TestCase):

    def setUp(self):
        super(TestNetconfOpenConfigClient, self).setUp()
        self.device = 'foo'
        self.conf = self.useFixture(config_fixture.Config())
        self.conf.register_opts(config._opts + config._device_opts,
                                group='foo')
        self.conf.register_opts((openconfig._DEVICE_OPTS
                                 + openconfig._NCCLIENT_OPTS), group='foo')
        self.conf.config(enabled_devices=['foo'],
                         group='networking_baremetal')
        self.conf.config(driver='test-driver',
                         switch_id='aa:bb:cc:dd:ee:ff',
                         switch_info='foo',
                         physical_networks=['fake_physical_network'],
                         device_params={'name': 'default'},
                         host='foo.example.com',
                         key_filename='/test/test_key_file',
                         username='foo_user',
                         group='foo')

        self.client = openconfig.NetconfOpenConfigClient(self.device)

    def test_get_lock_session_id(self):
        err_info = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<error-info xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">'
            '<session-id>{}</session-id>'
            '</error-info>')
        self.assertEqual('0', self.client._get_lock_session_id(
            err_info.format(0)))
        self.assertEqual('abc-123', self.client._get_lock_session_id(
            err_info.format('abc-123')))

    def test_get_client_args(self):
        self.assertEqual(
            {'device_params': {'name': 'default'},
             'host': 'foo.example.com',
             'hostkey_verify': True,
             'keepalive': True,
             'key_filename': '/test/test_key_file',
             'port': 830,
             'username': 'foo_user',
             'allow_agent': True,
             'look_for_keys': True}, self.client.get_client_args())

    @mock.patch.object(manager, 'connect', autospec=True)
    def test_get_capabilities(self, mock_manager):
        fake_caps = set(constants.IANA_NETCONF_CAPABILITIES.values())
        fake_caps.add('http://openconfig.net/yang/'
                      'network-instance?'
                      'module=openconfig-network-instance&'
                      'revision=2021-07-22')
        fake_caps.add('http://openconfig.net/yang/'
                      'interfaces?'
                      'module=openconfig-interfaces&'
                      'revision=2021-04-06')
        mock_ncclient = mock.Mock()
        mock_ncclient.server_capabilities = fake_caps
        mock_manager.return_value.__enter__.return_value = mock_ncclient
        self.assertEqual({
            ':base:1.0', ':base:1.1', ':candidate', ':confirmed-commit',
            ':confirmed-commit:1.1', ':rollback-on-error', ':startup',
            ':validate', ':validate:1.1', ':writable-running',
            'openconfig-network-instance', 'openconfig-interfaces'},
            self.client.get_capabilities())

    @mock.patch.object(manager, 'connect', autospec=True)
    @mock.patch.object(openconfig.NetconfOpenConfigClient,
                       'get_lock_and_configure', autospec=True)
    def test_edit_config_writable_running(self, mock_lock_config,
                                          mock_manager):
        fake_config = mock.Mock()
        fake_config.to_xml_element.return_value = ElementTree.Element('fake')
        mock_ncclient = mock.Mock()
        fake_caps = {constants.IANA_NETCONF_CAPABILITIES[':writable-running']}
        mock_ncclient.server_capabilities = fake_caps
        mock_manager.return_value.__enter__.return_value = mock_ncclient
        self.client.edit_config(fake_config)
        mock_lock_config.assert_called_once_with(self.client, mock_ncclient,
                                                 openconfig.RUNNING,
                                                 [fake_config], False)

    @mock.patch.object(manager, 'connect', autospec=True)
    @mock.patch.object(openconfig.NetconfOpenConfigClient,
                       'get_lock_and_configure', autospec=True)
    def test_edit_config_candidate(self, mock_lock_config, mock_manager):
        fake_config = mock.Mock()
        fake_config.to_xml_element.return_value = ElementTree.Element('fake')
        mock_ncclient = mock.Mock()
        fake_caps = {constants.IANA_NETCONF_CAPABILITIES[':candidate']}
        mock_ncclient.server_capabilities = fake_caps
        mock_manager.return_value.__enter__.return_value = mock_ncclient
        self.client.edit_config(fake_config)
        mock_lock_config.assert_called_once_with(self.client, mock_ncclient,
                                                 openconfig.CANDIDATE,
                                                 [fake_config], False)

    def test_get_lock_and_configure_confirmed_commit(self):
        self.client.capabilities = {':candidate', ':writable-running',
                                    ':confirmed-commit'}
        fake_config = mock.Mock()
        fake_config.to_xml_element.return_value = ElementTree.Element('fake')
        mock_client = mock.MagicMock()
        self.client.get_lock_and_configure(mock_client, openconfig.CANDIDATE,
                                           [fake_config], False)
        mock_client.locked.assert_called_with(openconfig.CANDIDATE)
        mock_client.discard_changes.assert_called_once()
        mock_client.edit_config.assert_called_with(
            target=openconfig.CANDIDATE,
            config='<config><fake /></config>')
        mock_client.validate.assert_not_called()
        mock_client.commit.assert_has_calls([
            mock.call(confirmed=True, timeout=str(30)), mock.call()])

    def test_get_lock_and_configure_validate(self):
        self.client.capabilities = {':candidate', ':writable-running',
                                    ':validate'}
        fake_config = mock.Mock()
        fake_config.to_xml_element.return_value = ElementTree.Element('fake')
        mock_client = mock.MagicMock()
        self.client.get_lock_and_configure(mock_client, openconfig.CANDIDATE,
                                           [fake_config], False)
        mock_client.locked.assert_called_with(openconfig.CANDIDATE)
        mock_client.discard_changes.assert_called_once()
        mock_client.edit_config.assert_called_with(
            target=openconfig.CANDIDATE,
            config='<config><fake /></config>')
        mock_client.validate.assert_called_once_with(
            source=openconfig.CANDIDATE)
        mock_client.commit.assert_called_once_with()

    def test_get_lock_and_configure_writeable_running(self):
        self.client.capabilities = {':writable-running'}
        fake_config = mock.Mock()
        fake_config.to_xml_element.return_value = ElementTree.Element('fake')
        mock_client = mock.MagicMock()
        self.client.get_lock_and_configure(mock_client, openconfig.RUNNING,
                                           [fake_config], False)
        mock_client.locked.assert_called_with(openconfig.RUNNING)
        mock_client.discard_changes.assert_not_called()
        mock_client.validate.assert_not_called()
        mock_client.commit.assert_not_called()
        mock_client.edit_config.assert_called_with(
            target=openconfig.RUNNING,
            config='<config><fake /></config>')

    @mock.patch.object(manager, 'connect', autospec=True)
    def test_get(self, mock_manager):
        fake_query = interfaces.Interfaces()
        fake_query.add('foo1/1')
        mock_ncclient = mock.Mock()
        mock_manager.return_value.__enter__.return_value = mock_ncclient
        self.client.get(query=fake_query)
        mock_ncclient.get.assert_called_with(filter=('subtree', mock.ANY))

    def test_get_aggregation_ids(self):
        self.conf.config(link_aggregate_prefix='foo',
                         link_aggregate_range='5..10',
                         group=self.device)
        self.assertEqual({'foo5', 'foo6', 'foo7', 'foo8', 'foo9', 'foo10'},
                         self.client.get_aggregation_ids())

    def test_allocate_deferred(self):
        aggregate_id = 'foo5'
        _config = []
        ifaces = interfaces.Interfaces()
        iface_a = ifaces.add('foo1/1',
                             interface_type=constants.IFACE_TYPE_ETHERNET)
        iface_a.ethernet.config.aggregate_id = openconfig.DEFERRED
        iface_b = ifaces.add('foo1/2',
                             interface_type=constants.IFACE_TYPE_ETHERNET)
        iface_b.ethernet.config.aggregate_id = openconfig.DEFERRED
        iface_agg = ifaces.add(openconfig.DEFERRED,
                               interface_type=constants.IFACE_TYPE_AGGREGATE)
        iface_agg.config.name = openconfig.DEFERRED
        _config.append(ifaces)
        _lacp = lacp.LACP()
        lacp_ifaces = lacp.LACPInterfaces()
        lacp_ifaces.add(openconfig.DEFERRED)
        _config.append(_lacp)
        self.client.allocate_deferred(aggregate_id, _config)
        for conf in _config:
            if isinstance(conf, interfaces.Interfaces):
                for iface in ifaces:
                    if isinstance(iface, interfaces.InterfaceAggregate):
                        self.assertEqual(iface.name, aggregate_id)
                        self.assertEqual(iface.config.name, aggregate_id)
                    elif isinstance(iface, interfaces.InterfaceEthernet):
                        self.assertEqual(iface.ethernet.config.aggregate_id,
                                         aggregate_id)
            if isinstance(conf, lacp.LACP):
                for lacp_iface in _lacp.interfaces.interfaces:
                    self.assertEqual(lacp_iface.name, aggregate_id)
                    self.assertEqual(lacp_iface.config.name, aggregate_id)

    def test_get_free_aggregate_id(self):
        self.conf.config(link_aggregate_prefix='Po',
                         link_aggregate_range='5..10',
                         group=self.device)
        mock_get_result = mock.Mock()
        mock_get_result.data_xml = XML_AGGREGATE_IFACES
        mock_client_locked = mock.Mock()
        mock_client_locked.get.return_value = mock_get_result
        used_aggregate_ids = {'Po5', 'Po7', 'Po9'}
        all_aggregate_ids = self.client.get_aggregation_ids()
        result = self.client.get_free_aggregate_id(mock_client_locked)
        self.assertNotIn(result, used_aggregate_ids)
        self.assertIn(result, all_aggregate_ids)


class TestNetconfOpenConfigDriver(base.TestCase):

    def setUp(self):
        super(TestNetconfOpenConfigDriver, self).setUp()
        self.device = 'foo'
        self.conf = self.useFixture(config_fixture.Config())
        self.conf.register_opts(config._opts + config._device_opts,
                                group='foo')
        self.conf.register_opts((openconfig._DEVICE_OPTS
                                 + openconfig._NCCLIENT_OPTS), group='foo')
        self.conf.config(enabled_devices=['foo'],
                         group='networking_baremetal')
        self.conf.config(driver='test-driver',
                         switch_id='aa:bb:cc:dd:ee:ff',
                         switch_info='foo',
                         physical_networks=['fake_physical_network'],
                         device_params={'name': 'default'},
                         host='foo.example.com',
                         key_filename='/test/test_key_file',
                         username='foo_user',
                         group='foo')
        mock_client = mock.patch.object(openconfig, 'NetconfOpenConfigClient',
                                        autospec=True)
        self.mock_client = mock_client.start()
        self.addCleanup(mock_client.stop)

        self.driver = openconfig.NetconfOpenConfigDriver(self.device)
        self.mock_client.assert_called_once_with('foo')
        self.mock_client.reset_mock()

    def test_validate(self):
        self.driver.validate()
        self.driver.client.get_capabilities.assert_called_once_with()

    @mock.patch.object(openconfig, 'CONF', autospec=True)
    def test_load_config(self, mock_conf):
        self.driver.load_config()
        mock_conf.register_opts.assert_has_calls(
            [mock.call(openconfig._DEVICE_OPTS, group=self.driver.device),
             mock.call(openconfig._NCCLIENT_OPTS, group=self.driver.device)])

    def test_create_network(self):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network()
        self.driver.create_network(m_nc)
        net_instances = self.driver.client.edit_config.call_args[0][0]
        for net_instance in net_instances:
            self.assertEqual(net_instance.name, 'default')
            vlans = net_instance.vlans
            for vlan in vlans:
                self.assertEqual(vlan.config.operation, nc_op.MERGE.value)
                self.assertEqual(vlan.config.name,
                                 self.driver._uuid_as_hex(m_nc.current['id']))
                self.assertEqual(vlan.config.status, constants.VLAN_ACTIVE)

    def test_update_network_no_changes(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN)
        m_nc.original = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN)
        self.assertEqual(m_nc.current, m_nc.original)
        self.driver.update_network(m_nc)
        self.driver.client.edit_config.assert_not_called()

    def test_update_network_change_vlan_id(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=10)
        m_nc.original = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=20)
        self.driver.update_network(m_nc)
        call_args_list = self.driver.client.edit_config.call_args_list
        del_net_instances = call_args_list[0][0][0]
        add_net_instances = call_args_list[1][0][0]
        self.driver.client.edit_config.assert_has_calls(
            [mock.call(del_net_instances), mock.call(add_net_instances)])
        for net_instance in del_net_instances:
            self.assertEqual(net_instance.name, 'default')
            for vlan in net_instance.vlans:
                self.assertEqual(vlan.operation, nc_op.REMOVE.value)
                self.assertEqual(vlan.vlan_id, 20)
                self.assertEqual(vlan.config.status, constants.VLAN_SUSPENDED)
                self.assertEqual(vlan.config.name, 'neutron-DELETED-20')
        for net_instance in add_net_instances:
            self.assertEqual(net_instance.name, 'default')
            for vlan in net_instance.vlans:
                self.assertEqual(vlan.operation, nc_op.MERGE.value)
                self.assertEqual(vlan.config.name,
                                 self.driver._uuid_as_hex(network_id))
                self.assertEqual(vlan.vlan_id, 10)

    def test_update_network_change_admin_state(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=10,
            admin_state_up=False)
        m_nc.original = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=10,
            admin_state_up=True)
        self.driver.update_network(m_nc)
        call_args_list = self.driver.client.edit_config.call_args_list
        add_net_instances = call_args_list[0][0][0]
        self.driver.client.edit_config.assert_called_once_with(
            add_net_instances)
        for net_instance in add_net_instances:
            self.assertEqual(net_instance.name, 'default')
            for vlan in net_instance.vlans:
                self.assertEqual(vlan.operation, nc_op.MERGE.value)
                self.assertEqual(vlan.config.status, constants.VLAN_SUSPENDED)
                self.assertEqual(vlan.config.name,
                                 self.driver._uuid_as_hex(network_id))
                self.assertEqual(vlan.vlan_id, 10)

    def test_delete_network(self):
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_nc.current = ml2_utils.get_test_network(
            network_type=n_const.TYPE_VLAN, segmentation_id=15)
        self.driver.delete_network(m_nc)
        self.driver.client.edit_config.assert_called_once()
        call_args_list = self.driver.client.edit_config.call_args_list
        net_instances = call_args_list[0][0][0]
        for net_instance in net_instances:
            self.assertEqual(net_instance.name, 'default')
            for vlan in net_instance.vlans:
                self.assertEqual(vlan.operation, nc_op.REMOVE.value)
                self.assertEqual(vlan.vlan_id, 15)
                self.assertEqual(vlan.config.status, constants.VLAN_SUSPENDED)
                self.assertEqual(vlan.config.name, 'neutron-DELETED-15')

    def test_create_port_vlan(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=15)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id)
        m_pc.network = m_nc
        segment = {
            api.ID: uuidutils.generate_uuid(),
            api.PHYSICAL_NETWORK:
                m_nc.current['provider:physical_network'],
            api.NETWORK_TYPE: m_nc.current['provider:network_type'],
            api.SEGMENTATION_ID: m_nc.current['provider:segmentation_id']}
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.create_port(m_pc, segment, links)
        self.driver.client.edit_config.assert_called_once()
        call_args_list = self.driver.client.edit_config.call_args_list
        ifaces = call_args_list[0][0][0]
        for iface in ifaces:
            self.assertEqual(iface.name, links[0]['port_id'])
            self.assertEqual(iface.config.enabled,
                             m_pc.current['admin_state_up'])
            self.assertEqual(iface.config.mtu, m_nc.current[api.MTU])
            self.assertEqual(iface.config.description,
                             f'neutron-{m_pc.current[api.ID]}')
            self.assertEqual(iface.ethernet.switched_vlan.config.operation,
                             nc_op.REPLACE.value)
            self.assertEqual(
                iface.ethernet.switched_vlan.config.interface_mode,
                constants.VLAN_MODE_ACCESS)
            self.assertEqual(
                iface.ethernet.switched_vlan.config.access_vlan,
                segment[api.SEGMENTATION_ID])

    def test_create_port_flat(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_FLAT)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id)
        m_pc.network = m_nc
        segment = {
            api.ID: uuidutils.generate_uuid(),
            api.PHYSICAL_NETWORK:
                m_nc.current['provider:physical_network'],
            api.NETWORK_TYPE: m_nc.current['provider:network_type']}
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.create_port(m_pc, segment, links)
        self.driver.client.edit_config.assert_called_once()
        call_args_list = self.driver.client.edit_config.call_args_list
        ifaces = call_args_list[0][0][0]
        for iface in ifaces:
            self.assertEqual(iface.name, links[0]['port_id'])
            self.assertEqual(iface.config.enabled,
                             m_pc.current['admin_state_up'])
            self.assertEqual(iface.config.mtu, m_nc.current[api.MTU])
            self.assertEqual(iface.config.description,
                             f'neutron-{m_pc.current[api.ID]}')
            self.assertIsNone(iface.ethernet)

    def test_update_port(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=15,
            mtu=9000)
        m_nc.original = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=15,
            mtu=1500)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            admin_state_up=False)
        m_pc.original = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            admin_state_up=True)
        m_pc.network = m_nc
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.update_port(m_pc, links)
        self.driver.client.edit_config.assert_called_once()
        call_args_list = self.driver.client.edit_config.call_args_list
        ifaces = call_args_list[0][0][0]
        for iface in ifaces:
            self.assertEqual(iface.name, links[0]['port_id'])
            self.assertEqual(iface.config.enabled,
                             m_pc.current['admin_state_up'])
            self.assertEqual(iface.config.mtu, m_nc.current[api.MTU])
            self.assertIsNone(iface.ethernet)

    def test_update_port_no_supported_attrib_changed(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=15)
        m_nc.original = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=15)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            name='current')
        m_pc.original = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            name='original')
        m_pc.network = m_nc
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.update_port(m_pc, links)
        self.driver.client.edit_config.assert_not_called()

    def test_delete_port_vlan(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=15)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id)
        m_pc.network = m_nc
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.delete_port(m_pc, links)
        self.driver.client.edit_config.assert_called_once()
        call_args_list = self.driver.client.edit_config.call_args_list
        ifaces = call_args_list[0][0][0]
        for iface in ifaces:
            self.assertEqual(iface.name, links[0]['port_id'])
            self.assertEqual(iface.config.operation, nc_op.REMOVE.value)
            self.assertEqual(iface.config.description, '')
            self.assertFalse(iface.config.enabled)
            self.assertEqual(iface.config.mtu, 0)
            self.assertEqual(iface.ethernet.switched_vlan.config.operation,
                             nc_op.REMOVE.value)

    def test_delete_port_flat(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_FLAT)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id)
        m_pc.network = m_nc
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.delete_port(m_pc, links)
        self.driver.client.edit_config.assert_called_once()
        call_args_list = self.driver.client.edit_config.call_args_list
        ifaces = call_args_list[0][0][0]
        for iface in ifaces:
            self.assertEqual(iface.name, links[0]['port_id'])
            self.assertEqual(iface.config.operation, nc_op.REMOVE.value)
            self.assertEqual(iface.config.description, '')
            self.assertFalse(iface.config.enabled)
            self.assertEqual(iface.config.mtu, 0)
            self.assertIsNone(iface.ethernet)

    def test_create_lacp_port_flat(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        binding_profile = {
            constants.LOCAL_LINK_INFO: [
                {'port_id': 'foo1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'},
                {'port_id': 'foo1/2', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'}],
            constants.LOCAL_GROUP_INFO: {
                'id': uuidutils.generate_uuid(),
                'name': 'PortGroup1',
                'bond_mode': '802.3ad',
                'bond_properties': {
                    constants.LACP_INTERVAL: 'fast',
                    constants.LACP_MIN_LINKS: 2}
            }
        }
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_FLAT)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            binding_profile=binding_profile)
        m_pc.network = m_nc
        segment = {
            api.ID: uuidutils.generate_uuid(),
            api.PHYSICAL_NETWORK:
                m_nc.current['provider:physical_network'],
            api.NETWORK_TYPE: m_nc.current['provider:network_type']}
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.create_port(m_pc, segment, links)
        self.driver.client.edit_config.assert_called_once_with(
            [mock.ANY, mock.ANY], deferred_allocations=True)
        call_args_list = self.driver.client.edit_config.call_args_list
        ifaces = list(call_args_list[0][0][0][0])
        _lacp = call_args_list[0][0][0][1]
        lacp_iface = list(_lacp.interfaces)[0]
        if_link_a = ifaces[0]
        if_link_b = ifaces[1]
        if_agg = ifaces[2]
        assert isinstance(if_link_a, interfaces.InterfaceEthernet)
        assert isinstance(if_link_b, interfaces.InterfaceEthernet)
        assert isinstance(if_agg, interfaces.InterfaceAggregate)
        assert isinstance(lacp_iface, lacp.LACPInterface)
        self.assertEqual(if_link_a.name, 'foo1/1')
        self.assertEqual(if_link_b.name, 'foo1/2')
        self.assertEqual(if_agg.name, openconfig.DEFERRED)
        for iface in (if_link_a, if_link_b):
            self.assertEqual(iface.config.operation, nc_op.MERGE.value)
            self.assertEqual(iface.config.enabled,
                             m_pc.current['admin_state_up'])
            self.assertEqual(iface.config.mtu, m_nc.current['mtu'])
            self.assertEqual(iface.config.description,
                             f'neutron-{m_pc.current[api.ID]}')
            self.assertEqual(iface.ethernet.config.aggregate_id,
                             openconfig.DEFERRED)
        self.assertEqual(if_agg.name, openconfig.DEFERRED)
        self.assertEqual(if_agg.config.name, openconfig.DEFERRED)
        self.assertEqual(if_agg.config.operation, nc_op.MERGE.value)
        self.assertEqual(if_agg.config.description,
                         f'neutron-{m_pc.current[api.ID]}')
        self.assertEqual(if_agg.aggregation.config.lag_type,
                         constants.LAG_TYPE_LACP)
        self.assertEqual(if_agg.aggregation.config.min_links,
                         binding_profile[
                             constants.LOCAL_GROUP_INFO]['bond_properties'][
                             constants.LACP_MIN_LINKS])
        self.assertIsNone(if_agg.aggregation.switched_vlan)
        self.assertEqual(lacp_iface.name, openconfig.DEFERRED)
        self.assertEqual(lacp_iface.operation, nc_op.REPLACE.value)
        self.assertEqual(lacp_iface.config.name, openconfig.DEFERRED)
        self.assertEqual(lacp_iface.config.interval,
                         constants.LACP_PERIOD_FAST)

    def test_create_lacp_port_vlan(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        binding_profile = {
            constants.LOCAL_LINK_INFO: [
                {'port_id': 'foo1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'},
                {'port_id': 'foo1/2', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'}],
            constants.LOCAL_GROUP_INFO: {
                'id': uuidutils.generate_uuid(),
                'name': 'PortGroup1',
                'bond_mode': '802.3ad',
                'bond_properties': {
                    constants.LACP_INTERVAL: 'fast',
                    constants.LACP_MIN_LINKS: 2}
            }
        }
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=40)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            binding_profile=binding_profile)
        m_pc.network = m_nc
        segment = {
            api.ID: uuidutils.generate_uuid(),
            api.PHYSICAL_NETWORK:
                m_nc.current['provider:physical_network'],
            api.NETWORK_TYPE: m_nc.current['provider:network_type'],
            api.SEGMENTATION_ID: m_nc.current['provider:segmentation_id']}
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.create_port(m_pc, segment, links)
        self.driver.client.edit_config.assert_called_once_with(
            [mock.ANY, mock.ANY], deferred_allocations=True)
        call_args_list = self.driver.client.edit_config.call_args_list
        ifaces = list(call_args_list[0][0][0][0])
        _lacp = call_args_list[0][0][0][1]
        lacp_iface = list(_lacp.interfaces)[0]
        if_link_a = ifaces[0]
        if_link_b = ifaces[1]
        if_agg = ifaces[2]
        assert isinstance(if_link_a, interfaces.InterfaceEthernet)
        assert isinstance(if_link_b, interfaces.InterfaceEthernet)
        assert isinstance(if_agg, interfaces.InterfaceAggregate)
        assert isinstance(lacp_iface, lacp.LACPInterface)
        self.assertEqual(if_link_a.name, 'foo1/1')
        self.assertEqual(if_link_b.name, 'foo1/2')
        self.assertEqual(if_agg.name, openconfig.DEFERRED)
        for iface in (if_link_a, if_link_b):
            self.assertEqual(iface.config.operation, nc_op.MERGE.value)
            self.assertEqual(iface.config.enabled,
                             m_pc.current['admin_state_up'])
            self.assertEqual(iface.config.mtu, m_nc.current['mtu'])
            self.assertEqual(iface.config.description,
                             f'neutron-{m_pc.current[api.ID]}')
            self.assertEqual(iface.ethernet.config.aggregate_id,
                             openconfig.DEFERRED)
        self.assertEqual(if_agg.name, openconfig.DEFERRED)
        self.assertEqual(if_agg.config.name, openconfig.DEFERRED)
        self.assertEqual(if_agg.config.operation, nc_op.MERGE.value)
        self.assertEqual(if_agg.config.description,
                         f'neutron-{m_pc.current[api.ID]}')
        self.assertEqual(if_agg.aggregation.config.lag_type,
                         constants.LAG_TYPE_LACP)
        self.assertEqual(if_agg.aggregation.config.min_links,
                         binding_profile[
                             constants.LOCAL_GROUP_INFO]['bond_properties'][
                             constants.LACP_MIN_LINKS])
        self.assertEqual(if_agg.aggregation.switched_vlan.config.operation,
                         nc_op.REPLACE.value)
        self.assertEqual(
            if_agg.aggregation.switched_vlan.config.interface_mode,
            constants.VLAN_MODE_ACCESS)
        self.assertEqual(if_agg.aggregation.switched_vlan.config.access_vlan,
                         segment[api.SEGMENTATION_ID])
        self.assertEqual(lacp_iface.name, openconfig.DEFERRED)
        self.assertEqual(lacp_iface.operation, nc_op.REPLACE.value)
        self.assertEqual(lacp_iface.config.name, openconfig.DEFERRED)
        self.assertEqual(lacp_iface.config.interval,
                         constants.LACP_PERIOD_FAST)

    def test_update_lacp_port(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        binding_profile = {
            constants.LOCAL_LINK_INFO: [
                {'port_id': 'foo1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'},
                {'port_id': 'foo1/2', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'}],
            constants.LOCAL_GROUP_INFO: {
                'id': uuidutils.generate_uuid(),
                'name': 'PortGroup1',
                'bond_mode': '802.3ad',
                'bond_properties': {
                    constants.LACP_INTERVAL: 'fast',
                    constants.LACP_MIN_LINKS: 2}
            }
        }
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=15,
            mtu=9000)
        m_nc.original = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=15,
            mtu=1500)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            admin_state_up=False, binding_profile=binding_profile)
        m_pc.original = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            admin_state_up=True, binding_profile=binding_profile)
        m_pc.network = m_nc
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.client.get.return_value = XML_IFACES_AGGREDATE_ID
        self.driver.update_port(m_pc, links)
        self.driver.client.get.assert_called_once()
        self.driver.client.edit_config.assert_called_once()
        # Validate the query to get aggregate id
        query_call_args = self.driver.client.get.call_args
        ifaces = query_call_args[1]['query']
        iface_a = list(ifaces)[0]
        iface_b = list(ifaces)[1]
        self.assertEqual(iface_a.name, 'foo1/1')
        self.assertEqual(iface_b.name, 'foo1/2')
        self.assertIsNone(iface_a.config)
        self.assertIsNone(iface_a.ethernet)
        self.assertIsNone(iface_b.config)
        self.assertIsNone(iface_b.ethernet)
        # Validate the edit config call
        edit_call_args_list = self.driver.client.edit_config.call_args_list
        ifaces = list(edit_call_args_list[0][0][0])
        if_link_a = ifaces[0]
        if_link_b = ifaces[1]
        if_agg = ifaces[2]
        assert isinstance(if_link_a, interfaces.InterfaceEthernet)
        assert isinstance(if_link_b, interfaces.InterfaceEthernet)
        assert isinstance(if_agg, interfaces.InterfaceAggregate)
        self.assertEqual(if_link_a.name, 'foo1/1')
        self.assertEqual(if_link_b.name, 'foo1/2')
        self.assertEqual(if_agg.name, 'Po10')
        for iface in (if_link_a, if_link_b):
            self.assertEqual(iface.config.operation, nc_op.MERGE.value)
            self.assertEqual(False, iface.config.enabled)
            self.assertEqual(9000, iface.config.mtu)
        self.assertEqual(if_agg.name, 'Po10')
        self.assertEqual(if_agg.operation, nc_op.MERGE.value)
        self.assertEqual(False, if_agg.config.enabled)

    def test_delete_lacp_port_flat(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        binding_profile = {
            constants.LOCAL_LINK_INFO: [
                {'port_id': 'foo1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'},
                {'port_id': 'foo1/2', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'}],
            constants.LOCAL_GROUP_INFO: {
                'id': uuidutils.generate_uuid(),
                'name': 'PortGroup1',
                'bond_mode': '802.3ad',
                'bond_properties': {
                    constants.LACP_INTERVAL: 'fast',
                    constants.LACP_MIN_LINKS: 2}
            }
        }
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_FLAT)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            binding_profile=binding_profile)
        m_pc.network = m_nc
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.client.get.return_value = XML_IFACES_AGGREDATE_ID
        self.driver.delete_port(m_pc, links)
        self.driver.client.get.assert_called_once_with(query=mock.ANY)
        self.driver.client.edit_config.assert_called_once_with([mock.ANY,
                                                                mock.ANY])
        # Validate the query to get aggregate id
        query_call_args = self.driver.client.get.call_args
        ifaces = query_call_args[1]['query']
        iface_a = list(ifaces)[0]
        iface_b = list(ifaces)[1]
        self.assertEqual(iface_a.name, 'foo1/1')
        self.assertEqual(iface_b.name, 'foo1/2')
        self.assertIsNone(iface_a.config)
        self.assertIsNone(iface_a.ethernet)
        self.assertIsNone(iface_b.config)
        self.assertIsNone(iface_b.ethernet)
        # Validate the edit config call
        edit_call_args_list = self.driver.client.edit_config.call_args_list
        _lacp = edit_call_args_list[0][0][0][0]
        ifaces = list(edit_call_args_list[0][0][0][1])
        lacp_iface = list(_lacp.interfaces)[0]
        if_link_a = ifaces[0]
        if_link_b = ifaces[1]
        if_agg = ifaces[2]
        assert isinstance(if_link_a, interfaces.InterfaceEthernet)
        assert isinstance(if_link_b, interfaces.InterfaceEthernet)
        assert isinstance(if_agg, interfaces.InterfaceAggregate)
        assert isinstance(lacp_iface, lacp.LACPInterface)
        self.assertEqual(if_link_a.name, 'foo1/1')
        self.assertEqual(if_link_b.name, 'foo1/2')
        self.assertEqual(if_agg.name, 'Po10')
        for iface in (if_link_a, if_link_b):
            self.assertEqual(iface.config.operation, nc_op.REMOVE.value)
            self.assertEqual(iface.config.enabled, False)
            self.assertEqual(iface.config.mtu, 0)
            self.assertEqual(iface.config.description, '')
            self.assertIsNone(iface.ethernet.switched_vlan)
            self.assertIsNone(iface.ethernet.config.aggregate_id)
            self.assertEqual(iface.ethernet.config.operation,
                             nc_op.REMOVE.value)
        self.assertEqual(if_agg.name, 'Po10')
        self.assertEqual(if_agg.operation, nc_op.REMOVE.value)
        self.assertIsNone(if_agg.config)
        self.assertIsNone(if_agg.aggregation)
        self.assertEqual(lacp_iface.name, 'Po10')
        self.assertEqual(lacp_iface.operation, nc_op.REMOVE.value)
        self.assertIsNone(lacp_iface.config)

    def test_delete_lacp_port_vlan(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        binding_profile = {
            constants.LOCAL_LINK_INFO: [
                {'port_id': 'foo1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'},
                {'port_id': 'foo1/2', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'}],
            constants.LOCAL_GROUP_INFO: {
                'id': uuidutils.generate_uuid(),
                'name': 'PortGroup1',
                'bond_mode': '802.3ad',
                'bond_properties': {
                    constants.LACP_INTERVAL: 'fast',
                    constants.LACP_MIN_LINKS: 2}
            }
        }
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=40)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            binding_profile=binding_profile)
        m_pc.network = m_nc
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.client.get.return_value = XML_IFACES_AGGREDATE_ID
        self.driver.delete_port(m_pc, links)
        self.driver.client.get.assert_called_once_with(query=mock.ANY)
        self.driver.client.edit_config.assert_called_once_with([mock.ANY,
                                                                mock.ANY])
        # Validate the query to get aggregate id
        query_call_args = self.driver.client.get.call_args
        ifaces = query_call_args[1]['query']
        iface_a = list(ifaces)[0]
        iface_b = list(ifaces)[1]
        self.assertEqual(iface_a.name, 'foo1/1')
        self.assertEqual(iface_b.name, 'foo1/2')
        self.assertIsNone(iface_a.config)
        self.assertIsNone(iface_a.ethernet)
        self.assertIsNone(iface_b.config)
        self.assertIsNone(iface_b.ethernet)
        # Validate the edit config call
        edit_call_args_list = self.driver.client.edit_config.call_args_list
        _lacp = edit_call_args_list[0][0][0][0]
        ifaces = list(edit_call_args_list[0][0][0][1])
        lacp_iface = list(_lacp.interfaces)[0]
        if_link_a = ifaces[0]
        if_link_b = ifaces[1]
        if_agg = ifaces[2]
        assert isinstance(if_link_a, interfaces.InterfaceEthernet)
        assert isinstance(if_link_b, interfaces.InterfaceEthernet)
        assert isinstance(if_agg, interfaces.InterfaceAggregate)
        assert isinstance(lacp_iface, lacp.LACPInterface)
        self.assertEqual(if_link_a.name, 'foo1/1')
        self.assertEqual(if_link_b.name, 'foo1/2')
        self.assertEqual(if_agg.name, 'Po10')
        for iface in (if_link_a, if_link_b):
            self.assertEqual(iface.config.operation, nc_op.REMOVE.value)
            self.assertEqual(iface.config.enabled, False)
            self.assertEqual(iface.config.mtu, 0)
            self.assertEqual(iface.config.description, '')
            self.assertEqual(iface.ethernet.switched_vlan.config.operation,
                             nc_op.REMOVE.value)
            self.assertIsNone(iface.ethernet.config.aggregate_id)
        self.assertEqual(if_agg.name, 'Po10')
        self.assertEqual(if_agg.operation, nc_op.REMOVE.value)
        self.assertIsNone(if_agg.config)
        self.assertIsNone(if_agg.aggregation)
        self.assertEqual(lacp_iface.name, 'Po10')
        self.assertEqual(lacp_iface.operation, nc_op.REMOVE.value)
        self.assertIsNone(lacp_iface.config)

    def test_create_pre_configured_aggregate_port_vlan(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        binding_profile = {
            constants.LOCAL_LINK_INFO: [
                {'port_id': 'foo1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'},
                {'port_id': 'foo1/2', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'}],
            constants.LOCAL_GROUP_INFO: {
                'id': uuidutils.generate_uuid(),
                'name': 'PortGroup1',
                'bond_mode': 'balance-rr',
            }
        }
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=40)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            binding_profile=binding_profile)
        m_pc.network = m_nc
        segment = {
            api.ID: uuidutils.generate_uuid(),
            api.PHYSICAL_NETWORK:
                m_nc.current['provider:physical_network'],
            api.NETWORK_TYPE: m_nc.current['provider:network_type'],
            api.SEGMENTATION_ID: m_nc.current['provider:segmentation_id']}
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.client.get.return_value = XML_IFACES_AGGREDATE_ID
        self.driver.create_port(m_pc, segment, links)
        self.driver.client.get.assert_called_once()
        self.driver.client.edit_config.assert_called_once()
        # Validate the query to get aggregate id
        query_call_args = self.driver.client.get.call_args
        ifaces = query_call_args[1]['query']
        iface_a = list(ifaces)[0]
        iface_b = list(ifaces)[1]
        self.assertEqual(iface_a.name, 'foo1/1')
        self.assertEqual(iface_b.name, 'foo1/2')
        self.assertIsNone(iface_a.config)
        self.assertIsNone(iface_a.ethernet)
        self.assertIsNone(iface_b.config)
        self.assertIsNone(iface_b.ethernet)
        # Validate the edit config call
        edit_call_args_list = self.driver.client.edit_config.call_args_list
        ifaces = list(edit_call_args_list[0][0][0])
        if_agg = ifaces[0]
        assert isinstance(if_agg, interfaces.InterfaceAggregate)
        self.assertEqual(if_agg.name, 'Po10')
        self.assertEqual(if_agg.operation, nc_op.MERGE.value)
        self.assertEqual(True, if_agg.config.enabled)
        self.assertEqual(if_agg.aggregation.switched_vlan.config.operation,
                         nc_op.REPLACE.value)
        self.assertEqual(
            if_agg.aggregation.switched_vlan.config.interface_mode,
            constants.VLAN_MODE_ACCESS)
        self.assertEqual(if_agg.aggregation.switched_vlan.config.access_vlan,
                         segment[api.SEGMENTATION_ID])

    def test_update_pre_configured_aggregate_port_vlan(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        binding_profile = {
            constants.LOCAL_LINK_INFO: [
                {'port_id': 'foo1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'},
                {'port_id': 'foo1/2', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'}],
            constants.LOCAL_GROUP_INFO: {
                'id': uuidutils.generate_uuid(),
                'name': 'PortGroup1',
                'bond_mode': 'balance-rr',
            }
        }
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=15)
        m_nc.original = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=15)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            admin_state_up=False, binding_profile=binding_profile)
        m_pc.original = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            admin_state_up=True, binding_profile=binding_profile)
        m_pc.network = m_nc
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.client.get.return_value = XML_IFACES_AGGREDATE_ID
        self.driver.update_port(m_pc, links)
        self.driver.client.get.assert_called_once()
        self.driver.client.edit_config.assert_called_once()
        # Validate the edit config call
        edit_call_args_list = self.driver.client.edit_config.call_args_list
        ifaces = list(edit_call_args_list[0][0][0])
        if_agg = ifaces[0]
        assert isinstance(if_agg, interfaces.InterfaceAggregate)
        self.assertEqual(if_agg.name, 'Po10')
        self.assertEqual(if_agg.operation, nc_op.MERGE.value)
        self.assertEqual(False, if_agg.config.enabled)

    def test_delete_pre_configured_aggregate_port_vlan(self):
        tenant_id = uuidutils.generate_uuid()
        network_id = uuidutils.generate_uuid()
        project_id = uuidutils.generate_uuid()
        m_nc = mock.create_autospec(driver_context.NetworkContext)
        m_pc = mock.create_autospec(driver_context.PortContext)
        binding_profile = {
            constants.LOCAL_LINK_INFO: [
                {'port_id': 'foo1/1', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'},
                {'port_id': 'foo1/2', 'switch_id': 'aa:bb:cc:dd:ee:ff',
                 'switch_info': 'foo'}],
            constants.LOCAL_GROUP_INFO: {
                'id': uuidutils.generate_uuid(),
                'name': 'PortGroup1',
                'bond_mode': 'balance-rr',
            }
        }
        m_nc.current = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=15)
        m_nc.original = ml2_utils.get_test_network(
            id=network_id, tenant_id=tenant_id, project_id=project_id,
            network_type=n_const.TYPE_VLAN, segmentation_id=15)
        m_pc.current = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            admin_state_up=False, binding_profile=binding_profile)
        m_pc.original = ml2_utils.get_test_port(
            network_id=network_id, tenant_id=tenant_id, project_id=project_id,
            admin_state_up=True, binding_profile=binding_profile)
        m_pc.network = m_nc
        links = m_pc.current['binding:profile'][constants.LOCAL_LINK_INFO]
        self.driver.client.get.return_value = XML_IFACES_AGGREDATE_ID
        self.driver.delete_port(m_pc, links)
        self.driver.client.get.assert_called_once()
        self.driver.client.edit_config.assert_called_once()
        # Validate the edit config call
        edit_call_args_list = self.driver.client.edit_config.call_args_list
        ifaces = list(edit_call_args_list[0][0][0])
        if_agg = ifaces[0]
        assert isinstance(if_agg, interfaces.InterfaceAggregate)
        self.assertEqual(if_agg.name, 'Po10')
        self.assertEqual(if_agg.operation, nc_op.MERGE.value)
        self.assertEqual(False, if_agg.config.enabled)
        self.assertEqual(if_agg.aggregation.switched_vlan.config.operation,
                         nc_op.REMOVE.value)
