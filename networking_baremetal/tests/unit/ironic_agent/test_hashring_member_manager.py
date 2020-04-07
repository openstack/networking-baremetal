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

import copy
from unittest import mock

from oslo_utils import timeutils
from oslotest import base

from networking_baremetal.agent import ironic_neutron_agent


def fake_notification():
    return (mock.Mock(),
            'publisher_id',
            'event_type',
            {'id': 'agent_id',
             'host': 'agent_host',
             'timestamp': timeutils.utcnow_ts()},
            'metadata')


class TestHashRingMemberManagerNotificationEndpoint(base.BaseTestCase):
    def setUp(self):
        super(TestHashRingMemberManagerNotificationEndpoint, self).setUp()
        self.member_manager = (
            ironic_neutron_agent.HashRingMemberManagerNotificationEndpoint())
        self.member_manager.members = []
        self.old_timestamp = 1517874977

    @mock.patch.object(ironic_neutron_agent.LOG, 'info', autospec=True)
    def test_notification_info_add_new_agent(self, mock_log):
        self.member_manager.hashring = mock.Mock()
        ctxt, publisher_id, event_type, payload, metadata = fake_notification()
        self.member_manager.info(ctxt, publisher_id, event_type, payload,
                                 metadata)
        self.member_manager.hashring.add_node.assert_called_with(payload['id'])
        self.assertEqual(payload, self.member_manager.members[0])
        self.assertEqual(1, mock_log.call_count)

    def test_notification_info_update_timestamp(self):
        self.member_manager.hashring = mock.Mock()
        ctxt, publisher_id, event_type, payload, metadata = fake_notification()
        # Set an old timestamp, and insert into members
        payload['timestamp'] = self.old_timestamp
        self.member_manager.members.append(copy.deepcopy(payload))
        # Reset timestamp, and simulate notification, add_node not called
        # Timestamp in member manager is updated.
        payload['timestamp'] = timeutils.utcnow_ts()
        self.assertNotEqual(payload['timestamp'],
                            self.member_manager.members[0]['timestamp'])
        self.member_manager.info(ctxt, publisher_id, event_type, payload,
                                 metadata)
        self.member_manager.hashring.add_node.assert_not_called()
        self.assertEqual(payload['timestamp'],
                         self.member_manager.members[0]['timestamp'])

    @mock.patch.object(ironic_neutron_agent.LOG, 'info', autospec=True)
    def test_remove_old_members(self, mock_log):
        self.member_manager.hashring = mock.Mock()
        # Add a member with an old timestamp, it is removed.
        ctxt, publisher_id, event_type, payload, metadata = fake_notification()
        payload['timestamp'] = self.old_timestamp
        self.member_manager.info(ctxt, publisher_id, event_type, payload,
                                 metadata)
        self.member_manager.hashring.remove_node.assert_called_with(
            payload['id'])
        self.assertEqual(0, len(self.member_manager.members))
        self.assertEqual(2, mock_log.call_count)
