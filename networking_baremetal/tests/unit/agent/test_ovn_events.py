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

"""Unit tests for OVN event handlers."""

from unittest import mock

from neutron.plugins.ml2.drivers.ovn.mech_driver.ovsdb import ovsdb_monitor
from neutron.tests import base as tests_base
from ovsdbapp.backend.ovs_idl import event as row_event
from tooz import hashring

from networking_baremetal.agent import ovn_events


class TestLocalnetPortEvent(tests_base.BaseTestCase):
    """Test cases for LocalnetPortEvent."""

    def setUp(self):
        super(TestLocalnetPortEvent, self).setUp()

        # Create mock agent with required attributes
        self.mock_agent = mock.MagicMock()
        self.mock_agent.agent_id = 'test-agent-id'

        # Create mock member manager with hash ring
        self.mock_member_manager = mock.MagicMock()
        self.mock_hashring = hashring.HashRing(['test-agent-id'])
        self.mock_member_manager.hashring = self.mock_hashring
        self.mock_agent.member_manager = self.mock_member_manager

        # Create event instance
        self.event = ovn_events.LocalnetPortEvent(self.mock_agent)

    def _create_mock_row(self, **kwargs):
        """Helper to create a mock row with required OVN attributes.

        BaseEvent.matches() checks row._table.name, so we need to ensure
        all mock rows have this attribute set correctly.
        """
        row = mock.MagicMock()
        row._table.name = 'Logical_Switch_Port'
        for key, value in kwargs.items():
            if key == 'tag' and value is not None:
                # tag should be a list
                row.tag = [value] if not isinstance(value, list) else value
            else:
                setattr(row, key, value)
        return row

    def test_event_initialization(self):
        """Test LocalnetPortEvent initialization."""
        self.assertEqual(self.event.agent, self.mock_agent)
        self.assertEqual(self.event.agent_id, 'test-agent-id')
        self.assertEqual(self.event.hashring, self.mock_hashring)
        self.assertEqual(self.event.event_name, 'LocalnetPortEvent')

        # Verify event is watching CREATE and DELETE on Logical_Switch_Port
        self.assertIn(row_event.RowEvent.ROW_CREATE, self.event.events)
        self.assertIn(row_event.RowEvent.ROW_DELETE, self.event.events)
        self.assertEqual(self.event.table, 'Logical_Switch_Port')

    def test_event_inherits_from_base_event(self):
        """Test LocalnetPortEvent inherits from BaseEvent."""
        self.assertIsInstance(self.event, ovsdb_monitor.BaseEvent)
        self.assertIsInstance(self.event, row_event.RowEvent)

    def test_matches_l2vni_localnet_port_owned_by_agent(self):
        """Test event matches L2VNI localnet port owned by this agent."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            type='localnet',
            name=f'neutron-{network_id}-localnet-physnet1'
        )

        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertTrue(result)

    def test_matches_rejects_update_events(self):
        """Test event rejects UPDATE events (only CREATE/DELETE allowed)."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            type='localnet',
            name=f'neutron-{network_id}-localnet-physnet1'
        )

        # BaseEvent.matches() filters out UPDATE events
        result = self.event.matches(row_event.RowEvent.ROW_UPDATE, row)

        self.assertFalse(result)

    def test_matches_rejects_wrong_table(self):
        """Test event rejects rows from wrong table."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            type='localnet',
            name=f'neutron-{network_id}-localnet-physnet1'
        )
        row._table.name = 'Logical_Router_Port'  # Wrong table

        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertFalse(result)

    def test_matches_rejects_non_localnet_port(self):
        """Test event rejects ports that are not type=localnet."""
        row = self._create_mock_row(
            type='patch',
            name='neutron-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee-localnet-'
                 'physnet1'
        )

        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertFalse(result)

    def test_matches_rejects_port_without_name(self):
        """Test event rejects ports without a name attribute."""
        row = self._create_mock_row(type='localnet')
        del row.name

        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertFalse(result)

    def test_matches_rejects_non_l2vni_localnet_port(self):
        """Test event rejects localnet ports without L2VNI naming."""
        row = self._create_mock_row(
            type='localnet',
            name='provnet-physnet1'  # No '-localnet-' in name
        )

        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertFalse(result)

    def test_matches_rejects_port_not_owned_by_agent(self):
        """Test event rejects ports not owned by agent (hash ring)."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            type='localnet',
            name=f'neutron-{network_id}-localnet-physnet1'
        )

        # Create a new hashring with a different agent
        other_hashring = hashring.HashRing(['other-agent-id'])
        self.event.hashring = other_hashring

        result = self.event.matches(row_event.RowEvent.ROW_CREATE, row)

        self.assertFalse(result)

    def test_run_triggers_targeted_reconciliation_on_create(self):
        """Test run() triggers targeted reconciliation on CREATE."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            type='localnet',
            name=f'neutron-{network_id}-localnet-physnet1',
            options={'network_name': 'physnet1'},
            tag=105
        )

        self.event.run(row_event.RowEvent.ROW_CREATE, row, None)

        # Verify targeted reconciliation was triggered
        mock_reconcile = self.mock_agent._reconcile_single_vlan_blocking
        mock_reconcile.assert_called_once_with(
            network_id, 'physnet1', 105, 'add'
        )

    def test_run_triggers_targeted_reconciliation_on_delete(self):
        """Test run() triggers targeted reconciliation on DELETE."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            type='localnet',
            name=f'neutron-{network_id}-localnet-physnet1',
            options={'network_name': 'physnet1'},
            tag=105
        )

        self.event.run(row_event.RowEvent.ROW_DELETE, row, None)

        # Verify targeted reconciliation was triggered with 'remove' action
        mock_reconcile = self.mock_agent._reconcile_single_vlan_blocking
        mock_reconcile.assert_called_once_with(
            network_id, 'physnet1', 105, 'remove'
        )

    def test_run_falls_back_to_full_reconciliation_on_missing_vlan(self):
        """Test run() falls back to full reconciliation if VLAN missing."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            type='localnet',
            name=f'neutron-{network_id}-localnet-physnet1',
            options={'network_name': 'physnet1'},
            tag=None  # No VLAN tag
        )

        self.event.run(row_event.RowEvent.ROW_CREATE, row, None)

        # Should fall back to full reconciliation
        self.mock_agent._reconcile_l2vni_trunks.assert_called_once()
        self.mock_agent._reconcile_single_vlan_blocking.assert_not_called()

    def test_run_falls_back_to_full_reconciliation_on_missing_physnet(self):
        """Test run() falls back to full reconciliation if physnet missing."""
        network_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        row = self._create_mock_row(
            type='localnet',
            name=f'neutron-{network_id}-localnet-physnet1',
            options={},  # No network_name
            tag=105
        )

        self.event.run(row_event.RowEvent.ROW_CREATE, row, None)

        # Should fall back to full reconciliation
        self.mock_agent._reconcile_l2vni_trunks.assert_called_once()
        self.mock_agent._reconcile_single_vlan_blocking.assert_not_called()

    def test_run_handles_attribute_error_gracefully(self):
        """Test run() handles AttributeError.

        Falls back to full reconciliation.
        """
        row = self._create_mock_row(
            type='localnet',
            name='neutron-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee-localnet-'
                 'physnet1'
        )
        # Missing options attribute will cause AttributeError
        del row.options

        # Should not raise exception
        self.event.run(row_event.RowEvent.ROW_CREATE, row, None)

        # Should fall back to full reconciliation
        self.mock_agent._reconcile_l2vni_trunks.assert_called_once()

    def test_extract_network_id(self):
        """Test _extract_network_id() helper method."""
        test_cases = [
            ('neutron-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee-localnet-physnet1',
             'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'),
            ('neutron-11111111-2222-3333-4444-555555555555-localnet-provider',
             '11111111-2222-3333-4444-555555555555'),
        ]

        for port_name, expected_network_id in test_cases:
            result = self.event._extract_network_id(port_name)
            self.assertEqual(result, expected_network_id)

    def test_extract_network_id_handles_malformed_name(self):
        """Test _extract_network_id() handles malformed port names."""
        # Name without '-localnet-' separator
        result = self.event._extract_network_id('invalid-name')
        self.assertIsNone(result)

        # Name with '-localnet-' but missing network UUID part
        # Returns 'neutron' because replace doesn't match 'neutron-'
        result = self.event._extract_network_id('neutron-localnet-physnet1')
        self.assertEqual(result, 'neutron')

    def test_extract_network_id_handles_empty_network_id(self):
        """Test _extract_network_id() returns empty string.

        For empty network ID.
        """
        # This edge case has empty network ID but valid format
        port_name = 'neutron--localnet-physnet1'
        result = self.event._extract_network_id(port_name)
        # Returns empty string (after replacing 'neutron-' from 'neutron-')
        self.assertEqual(result, '')
