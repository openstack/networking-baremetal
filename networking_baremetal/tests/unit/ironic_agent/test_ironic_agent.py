# Copyright 2017 Red Hat Inc.
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
from urllib import parse as urlparse

from neutron.agent import rpc as agent_rpc
from neutron.tests import base
from neutron_lib import constants as n_const
from openstack import connection
from openstack import exceptions as sdk_exc
from oslo_config import fixture as config_fixture
from tooz import hashring

from networking_baremetal.agent import ironic_neutron_agent
from networking_baremetal import constants
from networking_baremetal import ironic_client


class FakePort1(object):
    def __init__(self, physnet='physnet1'):
        self.uuid = '11111111-2222-3333-4444-555555555555'
        self.node_id = '55555555-4444-3333-2222-111111111111'
        self.physical_network = physnet


class FakePort2(object):
    def __init__(self, physnet='physnet2'):
        self.uuid = '11111111-aaaa-3333-4444-555555555555'
        self.node_id = '55555555-4444-3333-aaaa-111111111111'
        self.physical_network = physnet


@mock.patch.object(ironic_client, '_get_ironic_session', autospec=True)
@mock.patch.object(connection.Connection, 'baremetal', autospec=True)
class TestBaremetalNeutronAgent(base.BaseTestCase):
    def setUp(self):
        super(TestBaremetalNeutronAgent, self).setUp()
        self.context = object()
        self.conf = self.useFixture(config_fixture.Config())
        self.conf.config(transport_url='rabbit://user:password@host/')

    def test_get_template_node_state(self, mock_conn, mock_ir_client):
        self.agent = ironic_neutron_agent.BaremetalNeutronAgent()
        # Verify agent binary
        expected = constants.BAREMETAL_BINARY
        self.assertEqual(expected,
                         self.agent.get_template_node_state(
                             'uuid')['binary'])

        # Verify agent_type is Baremetal Node
        expected = constants.BAREMETAL_AGENT_TYPE
        self.assertEqual(expected,
                         self.agent.get_template_node_state(
                             'uuid')['agent_type'])
        # Verify topic
        expected = n_const.L2_AGENT_TOPIC
        self.assertEqual(expected,
                         self.agent.get_template_node_state(
                             'uuid')['topic'])
        # Verify host
        expected = 'the_node_uuid'
        self.assertEqual(expected,
                         self.agent.get_template_node_state(
                             'the_node_uuid')['host'])

    def test_report_state_one_node_one_port(self, mock_conn, mock_ir_client):
        self.agent = ironic_neutron_agent.BaremetalNeutronAgent()
        with mock.patch.object(self.agent.state_rpc, 'report_state',
                               autospec=True) as mock_report_state:
            self.agent.ironic_client = mock_conn
            mock_conn.ports.return_value = iter([FakePort1()])
            self.agent.agent_id = 'agent_id'
            self.agent.member_manager.hashring = hashring.HashRing(
                [self.agent.agent_id])

            expected = {
                'topic': n_const.L2_AGENT_TOPIC,
                'start_flag': True,
                'binary': constants.BAREMETAL_BINARY,
                'host': '55555555-4444-3333-2222-111111111111',
                'configurations': {
                    'bridge_mappings': {
                        'physnet1': 'yes'
                    },
                    'log_agent_heartbeats': False,
                },
                'agent_type': constants.BAREMETAL_AGENT_TYPE
            }
            self.agent._report_state()
            mock_report_state.assert_called_with(self.agent.context, expected)

    def test_report_state_with_log_agent_heartbeats(self, mock_conn,
                                                    mock_ir_client):
        self.agent = ironic_neutron_agent.BaremetalNeutronAgent()
        with mock.patch.object(self.agent.state_rpc, 'report_state',
                               autospec=True) as mock_report_state:
            self.conf.config(log_agent_heartbeats=True, group='AGENT')
            self.agent.ironic_client = mock_conn
            mock_conn.ports.return_value = iter([FakePort1()])
            self.agent.agent_id = 'agent_id'
            self.agent.member_manager.hashring = hashring.HashRing(
                [self.agent.agent_id])

            expected = {
                'topic': n_const.L2_AGENT_TOPIC,
                'start_flag': True,
                'binary': constants.BAREMETAL_BINARY,
                'host': '55555555-4444-3333-2222-111111111111',
                'configurations': {
                    'bridge_mappings': {
                        'physnet1': 'yes'
                    },
                    'log_agent_heartbeats': True,
                },
                'agent_type': constants.BAREMETAL_AGENT_TYPE
            }

            self.agent._report_state()
            mock_report_state.assert_called_with(self.agent.context, expected)

    def test_start_flag_false_on_update_no_config_change(self, mock_conn,
                                                         mock_ir_client):
        self.agent = ironic_neutron_agent.BaremetalNeutronAgent()
        with mock.patch.object(self.agent.state_rpc, 'report_state',
                               autospec=True) as mock_report_state:
            self.agent.ironic_client = mock_conn
            mock_conn.ports.return_value = iter([FakePort1()])
            self.agent.agent_id = 'agent_id'
            self.agent.member_manager.hashring = hashring.HashRing(
                [self.agent.agent_id])

            expected = {
                'topic': n_const.L2_AGENT_TOPIC,
                'start_flag': 'PLACEHOLDER',
                'binary': constants.BAREMETAL_BINARY,
                'host': '55555555-4444-3333-2222-111111111111',
                'configurations': {
                    'bridge_mappings': {
                        'physnet1': 'yes'
                    },
                    'log_agent_heartbeats': False,
                },
                'agent_type': constants.BAREMETAL_AGENT_TYPE
            }

            # First time report start_flag is True
            expected.update({'start_flag': True})
            self.agent._report_state()
            mock_report_state.assert_called_with(self.agent.context, expected)
            # Subsequent times report start_flag is False
            mock_conn.ports.return_value = iter([FakePort1()])
            expected.update({'start_flag': False})
            self.agent._report_state()
            mock_report_state.assert_called_with(self.agent.context, expected)

    def test_start_flag_true_on_update_after_config_change(self, mock_conn,
                                                           mock_ir_client):
        self.agent = ironic_neutron_agent.BaremetalNeutronAgent()
        with mock.patch.object(self.agent.state_rpc, 'report_state',
                               autospec=True) as mock_report_state:
            self.agent.ironic_client = mock_conn
            mock_conn.ports.return_value = iter([FakePort1()])
            self.agent.agent_id = 'agent_id'
            self.agent.member_manager.hashring = hashring.HashRing(
                [self.agent.agent_id])

            expected = {
                'topic': n_const.L2_AGENT_TOPIC,
                'start_flag': 'PLACEHOLDER',
                'binary': constants.BAREMETAL_BINARY,
                'host': '55555555-4444-3333-2222-111111111111',
                'configurations': {
                    'bridge_mappings': {
                        'physnet1': 'yes'
                    },
                    'log_agent_heartbeats': False,
                },
                'agent_type': constants.BAREMETAL_AGENT_TYPE
            }

            # First time report start_flag is True
            expected.update({'start_flag': True})
            self.agent._report_state()
            mock_report_state.assert_called_with(self.agent.context, expected)
            # Subsequent times report start_flag is False
            mock_conn.ports.return_value = iter([FakePort1()])
            expected.update({'start_flag': False})
            self.agent._report_state()
            mock_report_state.assert_called_with(self.agent.context, expected)
            # After bridge_mapping config change start_flag is True once
            mock_conn.ports.return_value = iter(
                [FakePort1(physnet='new_physnet')])
            expected.update({'configurations': {
                'bridge_mappings': {'new_physnet': 'yes'},
                'log_agent_heartbeats': False}})
            expected.update({'start_flag': True})
            self.agent._report_state()
            mock_report_state.assert_called_with(self.agent.context, expected)
            # Subsequent times report start_flag is False
            mock_conn.ports.return_value = iter(
                [FakePort1(physnet='new_physnet')])
            expected.update({'start_flag': False})
            self.agent._report_state()
            mock_report_state.assert_called_with(self.agent.context, expected)

    def test_report_state_two_nodes_two_ports(self, mock_conn, mock_ir_client):
        self.agent = ironic_neutron_agent.BaremetalNeutronAgent()
        with mock.patch.object(self.agent.state_rpc, 'report_state',
                               autospec=True) as mock_report_state:
            self.agent.ironic_client = mock_conn
            mock_conn.ports.return_value = iter([FakePort1(), FakePort2()])
            self.agent.agent_id = 'agent_id'
            self.agent.member_manager.hashring = hashring.HashRing(
                [self.agent.agent_id])

            expected1 = {
                'topic': n_const.L2_AGENT_TOPIC,
                'start_flag': True,
                'binary': constants.BAREMETAL_BINARY,
                'host': '55555555-4444-3333-2222-111111111111',
                'configurations': {
                    'bridge_mappings': {
                        'physnet1': 'yes'
                    },
                    'log_agent_heartbeats': False,
                },
                'agent_type': constants.BAREMETAL_AGENT_TYPE
            }
            expected2 = {
                'topic': n_const.L2_AGENT_TOPIC,
                'start_flag': True,
                'binary': constants.BAREMETAL_BINARY,
                'host': '55555555-4444-3333-aaaa-111111111111',
                'configurations': {
                    'bridge_mappings': {
                        'physnet2': 'yes'
                    },
                    'log_agent_heartbeats': False,
                },
                'agent_type': constants.BAREMETAL_AGENT_TYPE
            }

            self.agent._report_state()
            mock_report_state.assert_has_calls(
                [mock.call(self.agent.context, expected1),
                 mock.call(self.agent.context, expected2)], any_order=True)

    @mock.patch.object(ironic_client, 'get_client', autospec=True)
    @mock.patch.object(ironic_neutron_agent.LOG, 'exception', autospec=True)
    def test_ironic_port_list_fail(self, mock_log, mock_get_client,
                                   mock_conn, mock_ir_client):
        self.agent = ironic_neutron_agent.BaremetalNeutronAgent()
        self.agent.ironic_client = mock_conn

        def mock_generator(details=None):
            raise sdk_exc.OpenStackCloudException()
            yield

        mock_conn.ports.side_effect = mock_generator
        self.agent._report_state()
        self.assertEqual(1, mock_log.call_count)
        # Test initalization triggers the client call once
        # before _report_state is triggered, hence call
        # count below of 2.
        self.assertEqual(2, mock_get_client.call_count)

    @mock.patch.object(ironic_neutron_agent.BaremetalNeutronAgent, 'stop',
                       autospec=True)
    @mock.patch.object(ironic_client, 'get_client', autospec=True)
    @mock.patch.object(ironic_neutron_agent.LOG, 'exception', autospec=True)
    def test_ironic_port_list_fail_breakage(self, mock_log, mock_get_client,
                                            mock_stop, mock_conn,
                                            mock_ir_client):
        self.agent = ironic_neutron_agent.BaremetalNeutronAgent()
        self.agent.ironic_client = mock_conn
        mock_get_client.side_effect = Exception

        def mock_generator(details=None):
            raise sdk_exc.OpenStackCloudException()
            yield

        mock_conn.ports.side_effect = mock_generator
        self.agent._report_state()
        self.assertEqual(1, mock_log.call_count)
        # Checking the count on stop to see if it is called, as
        # opposed to the get_client method as it is the exception
        # root cause.
        mock_stop.assert_called_once_with(mock.ANY, failure=True)

    @mock.patch.object(ironic_neutron_agent.LOG, 'exception', autospec=True)
    @mock.patch.object(agent_rpc, 'PluginReportStateAPI', autospec=True)
    def test_state_rpc_report_state_fail(self, mock_report_state, mock_log,
                                         mock_conn, mock_ir_client):
        self.agent = ironic_neutron_agent.BaremetalNeutronAgent()
        self.agent.agent_id = 'agent_id'
        self.agent.member_manager.hashring = hashring.HashRing(
            [self.agent.agent_id])

        self.agent.ironic_client = mock_conn
        self.agent.state_rpc = mock_report_state
        mock_conn.ports.return_value = iter([FakePort1(), FakePort2()])
        mock_report_state.report_state.side_effect = Exception()
        self.agent._report_state()
        self.assertEqual(1, mock_log.call_count)

    @mock.patch.object(ironic_neutron_agent.BaremetalNeutronAgent, 'stop',
                       autospec=True)
    @mock.patch.object(ironic_neutron_agent.LOG, 'exception', autospec=True)
    @mock.patch.object(agent_rpc, 'PluginReportStateAPI', autospec=True)
    def test_state_rpc_report_state_fail_attribute(self, mock_report_state,
                                                   mock_log, mock_stop,
                                                   mock_conn,
                                                   mock_ir_client):
        self.agent = ironic_neutron_agent.BaremetalNeutronAgent()
        self.agent.agent_id = 'agent_id'
        self.agent.member_manager.hashring = hashring.HashRing(
            [self.agent.agent_id])

        self.agent.ironic_client = mock_conn
        self.agent.state_rpc = mock_report_state
        mock_conn.ports.return_value = iter([FakePort1(), FakePort2()])
        mock_report_state.report_state.side_effect = AttributeError()
        self.agent._report_state()
        self.assertEqual(1, mock_log.call_count)
        mock_stop.assert_called_once_with(mock.ANY, failure=True)

    def test__get_notification_transport_url(self, mock_conn, mock_ir_client):
        self.assertEqual(
            'rabbit://user:password@host/?amqp_auto_delete=true',
            ironic_neutron_agent._get_notification_transport_url())

        self.conf.config(transport_url='rabbit://user:password@host:5672/')
        self.assertEqual(
            'rabbit://user:password@host:5672/?amqp_auto_delete=true',
            ironic_neutron_agent._get_notification_transport_url())

        self.conf.config(transport_url='rabbit://host:5672/')
        self.assertEqual(
            'rabbit://host:5672/?amqp_auto_delete=true',
            ironic_neutron_agent._get_notification_transport_url())

        self.conf.config(transport_url='rabbit://user:password@host/vhost')
        self.assertEqual(
            'rabbit://user:password@host/vhost?amqp_auto_delete=true',
            ironic_neutron_agent._get_notification_transport_url())

        self.conf.config(
            transport_url='rabbit://user:password@host/vhost?foo=bar')
        self.assertEqual(
            # NOTE(hjensas): Parse the url's when comparing, different versions
            # may sort the query different.
            urlparse.urlparse('rabbit://user:password@host/'
                              'vhost?foo=bar&amqp_auto_delete=true'),
            urlparse.urlparse(
                ironic_neutron_agent._get_notification_transport_url()))

        self.conf.config(
            transport_url=('rabbit://user:password@host/vhost?foo=bar&'
                           'amqp_auto_delete=false'))
        self.assertEqual(
            # NOTE(hjensas): Parse the url's when comparing, different versions
            # may sort the query different.
            urlparse.urlparse('rabbit://user:password@host'
                              '/vhost?foo=bar&amqp_auto_delete=true'),
            urlparse.urlparse(
                ironic_neutron_agent._get_notification_transport_url()))

    def test__get_notification_transport_url_auto_delete_enabled(
            self, mock_conn, mock_ir_client):
        self.conf.config(amqp_auto_delete=True, group='oslo_messaging_rabbit')
        self.assertEqual(
            'rabbit://user:password@host/',
            ironic_neutron_agent._get_notification_transport_url())
