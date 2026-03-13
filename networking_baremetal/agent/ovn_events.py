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

"""OVN RowEvent handlers for L2VNI reconciliation."""

from neutron.plugins.ml2.drivers.ovn.mech_driver.ovsdb import ovsdb_monitor
from oslo_log import log as logging
from ovsdbapp.backend.ovs_idl import event as row_event

LOG = logging.getLogger(__name__)


class LocalnetPortEvent(ovsdb_monitor.BaseEvent):
    """Trigger L2VNI reconciliation when localnet ports are created or deleted.

    Watches for CREATE and DELETE events on Logical_Switch_Port table where
    type=localnet and the port name follows L2VNI naming convention
    (contains '-localnet-').

    Uses hash ring to filter events so only the agent responsible for the
    network processes the event.

    CREATE events trigger immediate reconciliation to add required subports.
    DELETE events trigger immediate reconciliation to remove obsolete subports,
    ensuring fast cleanup for security and resource isolation.
    """

    table = 'Logical_Switch_Port'
    events = (row_event.RowEvent.ROW_CREATE, row_event.RowEvent.ROW_DELETE)

    def __init__(self, agent):
        """Initialize LocalnetPortEvent.

        :param agent: BaremetalNeutronAgent instance
        """
        self.agent = agent
        self.hashring = agent.member_manager.hashring
        self.agent_id = agent.agent_id
        super().__init__()

    def match_fn(self, event, row, old=None):
        """Filter for L2VNI localnet ports owned by this agent.

        Returns True only if:
        1. Port type is localnet
        2. Port name follows L2VNI naming: neutron-<uuid>-localnet-<physnet>
        3. This agent owns the network (hash ring check)

        Note: Event type filtering (CREATE/DELETE only) is handled by the
        parent BaseEvent.matches() method, which ensures UPDATE events
        are filtered out before this method is called.

        :param event: Event type (ROW_CREATE or ROW_DELETE)
        :param row: OVN Logical_Switch_Port row
        :param old: Previous row state (unused for CREATE events)
        :returns: True if event should be processed, False otherwise
        """
        # Filter for localnet port type
        if not hasattr(row, 'type') or row.type != 'localnet':
            return False

        if not hasattr(row, 'name') or not row.name:
            return False

        # L2VNI localnet ports have format:
        # neutron-<network_uuid>-localnet-<physnet>
        if '-localnet-' not in row.name:
            return False

        # Extract network_id from port name
        try:
            parts = row.name.split('-localnet-')
            if len(parts) != 2:
                return False

            ls_name = parts[0]  # neutron-<network_uuid>
            if not ls_name.startswith('neutron-'):
                return False

            network_id = ls_name.replace('neutron-', '', 1)

            # Hash ring check: Do I own this network?
            hashring_members = list(self.hashring[network_id.encode('utf-8')])
            if self.agent_id not in hashring_members:
                LOG.debug(
                    "Localnet port %s on network %s not owned by this "
                    "agent (hash ring), ignoring. Agent ID: %s, "
                    "Hash ring members for network: %s",
                    row.name, network_id, self.agent_id, hashring_members)
                return False

            LOG.debug("Localnet port %s matches: L2VNI port on network %s "
                      "owned by this agent", row.name, network_id)
            return True

        except (ValueError, AttributeError, KeyError) as e:
            LOG.debug("Failed to parse localnet port name %s: %s",
                      row.name, e)
            return False

    def run(self, event, row, old):
        """Trigger targeted L2VNI reconciliation for specific VLAN.

        :param event: Event type (ROW_CREATE or ROW_DELETE)
        :param row: OVN Logical_Switch_Port row
        :param old: Previous row state (used for DELETE events)
        """
        # Debug logging at the very start before locking
        LOG.debug("LocalnetPortEvent.run called: event=%s, row.name=%s, "
                  "row.tag=%s, row.options=%s, old=%s",
                  event, row.name, getattr(row, 'tag', None),
                  getattr(row, 'options', None), old)

        try:
            # Extract info from event
            network_id = self._extract_network_id(row.name)
            physnet = row.options.get('network_name')
            vlan_id = int(row.tag[0]) if row.tag else None

            if not all([network_id, physnet, vlan_id]):
                LOG.warning(
                    "Incomplete info from localnet port event "
                    "(network_id=%s, physnet=%s, vlan_id=%s), falling "
                    "back to full reconciliation",
                    network_id, physnet, vlan_id)
                self.agent._reconcile_l2vni_trunks()
                return

            action = 'add' if event == self.ROW_CREATE else 'remove'
            LOG.info("Localnet port %s for network %s (physnet=%s, vlan=%s), "
                     "triggering targeted reconciliation",
                     action, network_id, physnet, vlan_id)

            # Call targeted reconciliation (blocks until lock available)
            self.agent._reconcile_single_vlan_blocking(
                network_id, physnet, vlan_id, action)

        except AttributeError:
            LOG.exception(
                "Malformed OVN row data in localnet port event, falling "
                "back to full reconciliation")
            self.agent._reconcile_l2vni_trunks()

    def _extract_network_id(self, port_name):
        """Extract network UUID from localnet port name.

        Localnet port names follow the pattern:
        neutron-<network_uuid>-localnet-<physnet>

        :param port_name: OVN localnet port name
        :returns: Network UUID or None
        """
        try:
            parts = port_name.split('-localnet-')
            if len(parts) != 2:
                return None
            ls_name = parts[0]
            return ls_name.replace('neutron-', '', 1)
        except (ValueError, AttributeError):
            return None
