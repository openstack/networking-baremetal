# Copyright (c) 2026 Red Hat, Inc.
# All Rights Reserved
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

"""Router HA Binding Manager.

Manages HA chassis group binding for router interface ports on VLAN networks
with baremetal nodes. Ensures router interface ports are bound to the same
HA chassis group as the network's external ports, enabling router-to-baremetal
communication on physical networks.

This is related to LP#1995078 where baremetal nodes on VLAN networks cannot
communicate with their router gateway because the router's internal interface
port (LRP) is not bound to any chassis.

This fixes LP#2144458 by providing event-driven HA chassis group binding,
eliminating the multi-minute connectivity delays caused by periodic-only
reconciliation.
"""

from neutron.common.ovn import constants as ovn_const
from neutron.common.ovn import utils as ovn_utils
from neutron_lib import constants as n_const
from openstack import exceptions as sdk_exc
from oslo_log import log as logging
from ovsdbapp.backend.ovs_idl import idlutils
from ovsdbapp import exceptions as ovs_exc

LOG = logging.getLogger(__name__)


class RouterHABindingManager:
    """Manages HA chassis group binding for router interface ports.

    Ensures router interface ports on VLAN networks are bound to the same
    HA chassis group as the network's external ports, enabling router-to-
    baremetal communication on physical networks.

    This manager is event-driven, responding to HA_Chassis_Group creation
    events to immediately bind router interface ports when network-level
    HA chassis groups are created.
    """

    def __init__(self, neutron_client, ovn_nb_idl, member_manager, agent_id):
        """Initialize router HA binding manager.

        :param neutron_client: Neutron client (OpenStack SDK Connection)
                               for port queries
        :param ovn_nb_idl: OVN Northbound IDL connection
        :param member_manager: Hash ring member manager for agent coordination
        :param agent_id: Agent ID for hash ring filtering
        """
        self.neutron_client = neutron_client
        self.ovn_nb_idl = ovn_nb_idl
        self.member_manager = member_manager
        self.agent_id = agent_id

    def bind_router_interfaces_for_network(self, network_id, ha_chassis_group):
        """Bind router interface ports to network's HA chassis group.

        This is the main entry point called by event handlers when a network
        HA chassis group is created or updated. It finds all router interface
        ports on the network and binds them to the specified HA chassis group.

        :param network_id: Neutron network UUID
        :param ha_chassis_group: OVN HA_Chassis_Group UUID or name
        """
        if not self._should_manage_network(network_id):
            return

        try:
            router_ports = self._get_router_interface_ports(network_id)

            if not router_ports:
                return

            for port in router_ports:
                try:
                    self._bind_lrp_to_ha_group(
                        port.id, ha_chassis_group, network_id)
                except (ovs_exc.OvsdbAppException, RuntimeError,
                        AttributeError):
                    LOG.exception("Failed to bind router port %s to HA "
                                  "chassis group %s", port.id,
                                  ha_chassis_group)

            LOG.info("Completed router HA binding for network %s: processed "
                     "%d router ports to HA chassis group %s",
                     network_id, len(router_ports), ha_chassis_group)

        except sdk_exc.OpenStackCloudException:
            LOG.exception("Failed to query router ports for network %s",
                          network_id)

    def _get_router_interface_ports(self, network_id):
        """Query Neutron for router interface ports on a network.

        :param network_id: Neutron network UUID
        :returns: List of router interface port objects
        """
        try:
            router_ports = list(self.neutron_client.network.ports(
                network_id=network_id,
                device_owner=n_const.DEVICE_OWNER_ROUTER_INTF))
            return router_ports
        except sdk_exc.OpenStackCloudException:
            LOG.exception("Failed to get router interface ports for "
                          "network %s", network_id)
            raise

    def _get_current_ha_chassis_group(self, lrp):
        """Extract current HA chassis group from LRP.

        :param lrp: OVN Logical_Router_Port row
        :returns: Current HA chassis group UUID/name or None
        """
        current_ha_group = None
        if hasattr(lrp, 'ha_chassis_group'):
            ha_group = lrp.ha_chassis_group
            if ha_group:
                current_ha_group = ha_group[0] if isinstance(
                    ha_group, list) else ha_group
        return current_ha_group

    def _update_lrp_ha_chassis_group(self, port_id, ha_chassis_group,
                                     network_id):
        """Update a single router port's HA chassis group if needed.

        Checks if the router port already has the correct HA chassis group
        (idempotent operation) and only updates if needed.

        :param port_id: Neutron port UUID
        :param ha_chassis_group: OVN HA_Chassis_Group UUID or name
        :param network_id: Neutron network UUID (for logging)
        :returns: True if port was updated, False if already correct or skipped
        :raises: OvsdbAppException, RuntimeError, AttributeError on errors
        """
        lrp_name = ovn_utils.ovn_lrouter_port_name(port_id)

        try:
            lrp = self.ovn_nb_idl.lrp_get(lrp_name).execute(check_error=True)
        except idlutils.RowNotFound:
            return False

        current_ha_group = self._get_current_ha_chassis_group(lrp)

        if current_ha_group == ha_chassis_group:
            return False

        self.ovn_nb_idl.lrp_set_ha_chassis_group(
            lrp_name, ha_chassis_group).execute(check_error=True)

        LOG.info("Updated router port %s HA chassis group from %s to %s "
                 "(network %s)", port_id, current_ha_group,
                 ha_chassis_group, network_id)
        return True

    def _bind_lrp_to_ha_group(self, port_id, ha_chassis_group, network_id):
        """Set LRP ha_chassis_group in OVN.

        Checks if the router port already has the correct HA chassis group
        (idempotent operation) and only updates if needed.

        :param port_id: Neutron port UUID
        :param ha_chassis_group: OVN HA_Chassis_Group UUID or name
        :param network_id: Neutron network UUID (for logging)
        """
        try:
            self._update_lrp_ha_chassis_group(
                port_id, ha_chassis_group, network_id)
        except (ovs_exc.OvsdbAppException, RuntimeError, AttributeError):
            LOG.exception("Failed to update HA chassis group for router "
                          "port %s", port_id)
            raise

    def _should_manage_network(self, network_id):
        """Check if this agent should manage the network via hash ring.

        Uses consistent hashing to determine if this agent is responsible
        for managing the network. This ensures only one agent processes
        events for each network in a multi-agent deployment.

        :param network_id: Neutron network UUID
        :returns: True if this agent owns the network, False otherwise
        """
        try:
            network_key = network_id.encode('utf-8')
            hashring_members = list(
                self.member_manager.hashring[network_key])

            return self.agent_id in hashring_members

        except (KeyError, AttributeError, TypeError):
            LOG.exception("Hash ring lookup failed for network %s, skipping "
                          "network management", network_id)
            return False

    def _get_router_ports_for_networks(self, network_ids):
        """Query router interface ports for multiple networks in chunks.

        Queries Neutron for router interface ports on the specified networks,
        using chunked queries to avoid URL length limits when filtering by
        many network IDs.

        :param network_ids: List of Neutron network UUIDs
        :returns: Dict mapping network_id -> list of port objects
        """
        ports_by_network = {}

        # Query in chunks of 100 to avoid URL length limits
        # (100 UUIDs = ~3.6KB in URL, well under 8KB web server limits;
        # 3000 networks would create ~108KB URL, causing failures)
        chunk_size = 100
        num_chunks = (len(network_ids) + chunk_size - 1) // chunk_size

        LOG.debug("Querying router ports for %d networks in %d chunk(s)",
                  len(network_ids), num_chunks)

        for i in range(0, len(network_ids), chunk_size):
            chunk = network_ids[i:i + chunk_size]

            try:
                chunk_ports = list(self.neutron_client.network.ports(
                    network_id=chunk,
                    device_owner=n_const.DEVICE_OWNER_ROUTER_INTF))

                # Group ports by network_id
                for port in chunk_ports:
                    network_id = port.network_id
                    if network_id not in ports_by_network:
                        ports_by_network[network_id] = []
                    ports_by_network[network_id].append(port)

            except sdk_exc.OpenStackCloudException:
                LOG.exception("Failed to query router interface ports for "
                              "network chunk during reconciliation")
                # Continue with remaining chunks
                continue

        return ports_by_network

    def _get_networks_with_ha_chassis_groups(self):
        """Find all networks that have HA chassis groups.

        Queries OVN's HA_Chassis_Group table for network-level groups
        (identified by having neutron:network_id in external_ids).
        Filters out router-level groups (which have neutron:router_id).

        :returns: Dict mapping network_id -> ha_chassis_group_uuid
        """
        network_ha_groups = {}

        try:
            if not hasattr(self.ovn_nb_idl, 'tables'):
                LOG.error("OVN NB IDL not available, router HA binding "
                          "cannot function. Baremetal nodes may not have "
                          "connectivity to router gateways.")
                return network_ha_groups

            if 'HA_Chassis_Group' not in self.ovn_nb_idl.tables:
                LOG.debug("HA_Chassis_Group table not found in OVN")
                return network_ha_groups

            table = self.ovn_nb_idl.tables['HA_Chassis_Group']

            for row in table.rows.values():
                if not hasattr(row, 'external_ids'):
                    continue

                external_ids = row.external_ids

                network_id = external_ids.get(
                    ovn_const.OVN_NETWORK_ID_EXT_ID_KEY)

                if network_id:
                    # Use any HA chassis group that has a network_id,
                    # regardless of whether it also has a router_id. In
                    # unified HA chassis group scenarios, the same group is
                    # used for both the network and the router.
                    network_ha_groups[network_id] = row.uuid

            LOG.debug("Found %d network-level HA chassis groups",
                      len(network_ha_groups))
            return network_ha_groups

        except (AttributeError, KeyError):
            LOG.exception("Failed to get networks with HA chassis groups "
                          "from OVN")
            return network_ha_groups

    def reconcile(self):
        """Periodic reconciliation of router HA binding.

        Discovers all networks with HA chassis groups and ensures their
        router interface ports are bound to those groups. This catches:

        1. Routers added to networks after HA chassis group exists
        2. Missed events (agent down/restarting during event)
        3. Race conditions or out-of-order event processing
        4. Manual changes to LRP ha_chassis_group settings

        This method is called periodically (default: 600s / 10 minutes)
        to ensure eventual consistency even if event-driven binding fails.
        """
        LOG.info("Starting router HA binding reconciliation")

        try:
            network_ha_groups = self._get_networks_with_ha_chassis_groups()

            if not network_ha_groups:
                return

            # Filter to only networks this agent should manage
            managed_network_ids = [
                nid for nid in network_ha_groups.keys()
                if self._should_manage_network(nid)
            ]

            if not managed_network_ids:
                LOG.debug("No managed networks found during reconciliation")
                return

            # Query router ports for managed networks (chunked for safety)
            ports_by_network = self._get_router_ports_for_networks(
                managed_network_ids)

            networks_processed = 0
            ports_updated = 0

            for network_id in managed_network_ids:
                ha_chassis_group = network_ha_groups[network_id]
                networks_processed += 1

                router_ports = ports_by_network.get(network_id, [])

                if not router_ports:
                    continue

                for port in router_ports:
                    try:
                        updated = self._update_lrp_ha_chassis_group(
                            port.id, ha_chassis_group, network_id)
                        if updated:
                            ports_updated += 1

                    except (ovs_exc.OvsdbAppException, RuntimeError,
                            AttributeError):
                        LOG.exception("Failed to reconcile router port %s",
                                      port.id)

            LOG.info("Router HA binding reconciliation complete: processed %d "
                     "networks, updated %d router ports",
                     networks_processed, ports_updated)

        except Exception:
            LOG.exception("Router HA binding reconciliation failed")
