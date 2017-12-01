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

import mock

from ironicclient import client
import ironicclient.common.apiclient.exceptions as ironic_exc
from neutron.agent import rpc as agent_rpc
from neutron.tests import base
from neutron_lib import constants as n_const
from oslo_config import fixture as config_fixture
from tooz import hashring

from networking_baremetal.agent import ironic_neutron_agent
from networking_baremetal import constants


class FakePort1(object):
    def __init__(self, physnet='physnet1'):
        self.uuid = '11111111-2222-3333-4444-555555555555'
        self.node_uuid = '55555555-4444-3333-2222-111111111111'
        self.physical_network = physnet


class FakePort2(object):
    def __init__(self, physnet='physnet2'):
        self.uuid = '11111111-aaaa-3333-4444-555555555555'
        self.node_uuid = '55555555-4444-3333-aaaa-111111111111'
        self.physical_network = physnet


class TestBaremetalNeutronAgent(base.BaseTestCase):
    def setUp(self):
        super(TestBaremetalNeutronAgent, self).setUp()
        self.context = object()
        self.agent = ironic_neutron_agent.BaremetalNeutronAgent()
        self.conf = self.useFixture(config_fixture.Config())

    def test_get_template_node_state(self):
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

    @mock.patch.object(client, 'Client', autospec=False)
    def test_report_state_one_node_one_port(self, mock_client):
        with mock.patch.object(self.agent.state_rpc, 'report_state',
                               autospec=True) as mock_report_state:
            self.agent.ironic_client = mock_client
            mock_client.port.list.return_value = [FakePort1()]
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

    @mock.patch.object(client, 'Client', autospec=False)
    def test_report_state_with_log_agent_heartbeats(self, mock_client):
        with mock.patch.object(self.agent.state_rpc, 'report_state',
                               autospec=True) as mock_report_state:
            self.conf.config(log_agent_heartbeats=True, group='AGENT')
            self.agent.ironic_client = mock_client
            mock_client.port.list.return_value = [FakePort1()]
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

    @mock.patch.object(client, 'Client', autospec=False)
    def test_start_flag_false_on_update_no_config_change(self, mock_client):
        with mock.patch.object(self.agent.state_rpc, 'report_state',
                               autospec=True) as mock_report_state:
            self.agent.ironic_client = mock_client
            mock_client.port.list.return_value = [FakePort1()]
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
            expected.update({'start_flag': False})
            self.agent._report_state()
            mock_report_state.assert_called_with(self.agent.context, expected)

    @mock.patch.object(client, 'Client', autospec=False)
    def test_start_flag_true_on_update_after_config_change(self, mock_client):
        with mock.patch.object(self.agent.state_rpc, 'report_state',
                               autospec=True) as mock_report_state:
            self.agent.ironic_client = mock_client
            mock_client.port.list.return_value = [FakePort1()]
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
            expected.update({'start_flag': False})
            self.agent._report_state()
            mock_report_state.assert_called_with(self.agent.context, expected)
            # After bridge_mapping config change start_flag is True once
            mock_client.port.list.return_value = [FakePort1(
                physnet='new_physnet')]
            expected.update({'configurations': {
                'bridge_mappings': {'new_physnet': 'yes'},
                'log_agent_heartbeats': False}})
            expected.update({'start_flag': True})
            self.agent._report_state()
            mock_report_state.assert_called_with(self.agent.context, expected)
            # Subsequent times report start_flag is False
            expected.update({'start_flag': False})
            self.agent._report_state()
            mock_report_state.assert_called_with(self.agent.context, expected)

    @mock.patch.object(client, 'Client', autospec=False)
    def test_report_state_two_nodes_two_ports(self, mock_client):
        with mock.patch.object(self.agent.state_rpc, 'report_state',
                               autospec=True) as mock_report_state:
            self.agent.ironic_client = mock_client
            mock_client.port.list.return_value = [FakePort1(), FakePort2()]
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

    @mock.patch.object(ironic_neutron_agent.LOG, 'exception', autospec=True)
    @mock.patch.object(client, 'Client', autospec=False)
    def test_ironic_port_list_fail(self, mock_client, mock_log):
            self.agent.ironic_client = mock_client
            mock_client.port.list.side_effect = Exception()
            self.agent._report_state()
            self.assertEqual(1, mock_log.call_count)

    @mock.patch.object(ironic_neutron_agent.LOG, 'exception', autospec=True)
    @mock.patch.object(client, 'Client', autospec=False)
    @mock.patch.object(agent_rpc, 'PluginReportStateAPI', autospec=True)
    def test_state_rpc_report_state_fail(self, mock_report_state, mock_client,
                                         mock_log):
        self.agent.agent_id = 'agent_id'
        self.agent.member_manager.hashring = hashring.HashRing(
            [self.agent.agent_id])

        self.agent.ironic_client = mock_client
        self.agent.state_rpc = mock_report_state
        mock_client.port.list.return_value = [FakePort1(), FakePort2()]
        mock_report_state.report_state.side_effect = Exception()
        self.agent._report_state()
        self.assertEqual(1, mock_log.call_count)

    @mock.patch.object(ironic_neutron_agent.LOG, 'exception', autospec=True)
    @mock.patch.object(client, 'Client', autospec=False)
    def test_ironic_exceptions_stop_loopingcall(self, mock_client, mock_log):
        self.agent.agent_id = 'agent_id'
        self.agent.member_manager.hashring = hashring.HashRing(
            [self.agent.agent_id])
        self.agent.heartbeat = mock.Mock()
        self.agent.ironic_client = mock_client
        for exc in (ironic_exc.AuthSystemNotFound('auth_system'),
                    ironic_exc.AuthPluginOptionsMissing('opt_names'),
                    ironic_exc.UnsupportedVersion()):

            mock_client.port.list.side_effect = exc
            self.agent._report_state()
            self.assertEqual(1, mock_log.call_count)
            self.agent.heartbeat.stop.assert_called()

            mock_log.reset_mock()
            mock_client.reset_mock()
            self.agent.heartbeat.reset_mock()

    @mock.patch.object(ironic_neutron_agent.LOG, 'exception', autospec=True)
    @mock.patch.object(client, 'Client', autospec=False)
    @mock.patch.object(agent_rpc, 'PluginReportStateAPI', autospec=True)
    def test_report_state_attribute_error_stop_looping_call(
            self, mock_state_rpc, mock_client, mock_log):
        self.agent.agent_id = 'agent_id'
        self.agent.member_manager.hashring = hashring.HashRing(
            [self.agent.agent_id])
        self.agent.heartbeat = mock.Mock()
        self.agent.ironic_client = mock_client
        self.agent.state_rpc = mock_state_rpc
        mock_client.port.list.return_value = [FakePort1(), FakePort2()]
        del mock_state_rpc.report_state
        self.agent._report_state()
        self.assertEqual(1, mock_log.call_count)
        self.agent.heartbeat.stop.assert_called()
