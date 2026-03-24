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

"""L2VNI Trunk Reconciliation Manager.

Manages trunk ports and subports for OVN network nodes to ensure only
required VLANs are trunked to each chassis based on ha_chassis_group
membership and network requirements.

Architecture:
- One Neutron network per OVN ha_chassis_group for anchor port modeling
- One shared subport anchor network for all trunk subports
- Anchor ports (trunk parents) attach to ha_chassis_group networks
- Subports signal VLAN bindings to ML2 switch plugins
- Stateless reconciliation based on current OVN/Neutron state
"""

import random
import time

import yaml

from neutron.common.ovn import utils as ovn_utils
from neutron_lib.api.definitions import portbindings
from neutron_lib import constants as n_const
from openstack import exceptions as sdkexc
from oslo_config import cfg
from oslo_log import log as logging
from ovsdbapp import exceptions as ovs_exc

CONF = cfg.CONF
LOG = logging.getLogger(__name__)

# Device owner types for L2VNI infrastructure ports
# TODO(TheJulia): Propose these and move these to neutron-lib
# if they accept them.
#
# Terminology clarification:
# - ANCHOR ports = trunk parent ports (attach to HA group networks)
# - SUBPORT ports = trunk child ports (attach to subport anchor network)
# - Despite confusing naming, "subport anchor network" hosts SUBPORTS,
#   while ANCHOR ports attach to separate HA group networks
DEVICE_OWNER_L2VNI_ANCHOR = 'baremetal:l2vni_anchor'
DEVICE_OWNER_L2VNI_SUBPORT = 'baremetal:l2vni_subport'
DEVICE_OWNER_L2VNI_NETWORK = 'baremetal:l2vni_network'


def _get_trunk_name(system_id, physnet):
    """Generate consistent trunk name.

    :param system_id: OVN chassis system-id
    :param physnet: Physical network name
    :returns: Trunk name string
    """
    return f"l2vni-trunk-{system_id}-{physnet}"


def _get_anchor_port_name(system_id, physnet):
    """Generate consistent anchor port name.

    :param system_id: OVN chassis system-id
    :param physnet: Physical network name
    :returns: Port name string
    """
    return f"l2vni-anchor-{system_id}-{physnet}"


def _get_subport_name(system_id, physnet, vlan_id):
    """Generate consistent subport name.

    :param system_id: OVN chassis system-id
    :param physnet: Physical network name
    :param vlan_id: VLAN ID for segmentation
    :returns: Port name string
    """
    return f"l2vni-subport-{system_id}-{physnet}-vlan{vlan_id}"


class L2VNITrunkManager:
    """Manages L2VNI trunk ports and subports for network nodes."""

    def __init__(self, neutron_client, ovn_nb_idl, ovn_sb_idl,
                 ironic_client, member_manager=None, agent_id=None):
        """Initialize L2VNI trunk manager.

        :param neutron_client: Neutron client for trunk/port operations
        :param ovn_nb_idl: OVN Northbound IDL connection
        :param ovn_sb_idl: OVN Southbound IDL connection
        :param ironic_client: Ironic client for node/port data
        :param member_manager: Member manager for hash ring filtering
                              (optional, for distributed work)
        :param agent_id: This agent's ID for hash ring membership checks
        """
        self.neutron = neutron_client
        self.ovn_nb_idl = ovn_nb_idl
        self.ovn_sb_idl = ovn_sb_idl
        self.ironic = ironic_client
        self.member_manager = member_manager
        self.agent_id = agent_id
        self._config_cache = None
        # Per-record cache: system_id -> data
        # Thread safety: This cache is only accessed from reconcile() and its
        # helper methods. The reconcile() method is protected by
        # _l2vni_reconciliation_lock in the agent (see
        # ironic_neutron_agent.py:_reconcile_l2vni_trunks), which prevents
        # concurrent execution. Therefore, no additional locking is needed.
        self._ironic_cache = {}

    def _should_manage_chassis(self, system_id):
        """Check if this agent should manage this chassis based on hash ring.

        :param system_id: OVN chassis system-id
        :returns: bool - True if this agent should manage the chassis
        """
        if not self.member_manager or not self.agent_id:
            # No hash ring - manage all chassis (single agent mode)
            return True

        # Check if this agent is responsible for this chassis
        # Hashring requires bytes for md5 hashing
        return (self.agent_id in
                self.member_manager.hashring[system_id.encode('utf-8')])

    def reconcile(self):
        """Main reconciliation entry point.

        Performs stateless reconciliation of trunk infrastructure:
        1. Ensure ha_chassis_group networks exist
        2. Ensure subport anchor network exists
        3. Discover/create trunk ports for chassis
        4. Calculate required VLANs per chassis from OVN state
        5. Reconcile subports to match requirements
        6. Clean up unused infrastructure
        """
        try:
            # Skip reconciliation if OVN connections are not available
            if self.ovn_nb_idl is None or self.ovn_sb_idl is None:
                LOG.debug("OVN connections not available, skipping "
                          "reconciliation")
                return

            # Ensure infrastructure networks exist
            self._ensure_infrastructure_networks()

            # Build trunk map: {(system_id, physnet): trunk_id}
            trunk_map = self._discover_trunks()

            # Calculate required VLANs with VNI info:
            # {(system_id, physnet): {vlan_id: vni}}
            required_vlans = self._calculate_required_vlans()

            # Reconcile subports
            self._reconcile_subports(trunk_map, required_vlans)

            # Clean up unused infrastructure
            self._cleanup_unused_infrastructure()

        except (sdkexc.SDKException, AttributeError, KeyError, TypeError,
                ValueError, IndexError):
            LOG.exception("Failed to reconcile L2VNI trunks")
            # Don't re-raise - let reconciliation continue on next interval
        except Exception:
            # This broad exception handler is intentional. The reconcile()
            # method is called periodically by the agent and must be resilient
            # to any unexpected errors to prevent the reconciliation loop from
            # crashing. The specific exception handlers above catch known error
            # types; this catches anything else that slips through.
            LOG.exception("Unexpected error during L2VNI trunk "
                          "reconciliation.")
            # Don't re-raise - let reconciliation continue on next interval

    def reconcile_single_vlan(self, network_id, physnet, vlan_id,
                              action='add'):
        """Targeted reconciliation for a single VLAN.

        Called by OVN event handlers when a specific localnet port is
        created or deleted. Much faster than full reconciliation because
        we already know which VLAN to add/remove.

        :param network_id: Neutron network UUID (tenant network with VLAN)
        :param physnet: Physical network name
        :param vlan_id: VLAN ID to add or remove
        :param action: 'add' for CREATE events, 'remove' for DELETE events
        """
        try:
            LOG.debug("Starting targeted reconciliation: %s VLAN %d on "
                      "physnet %s for network %s",
                      action, vlan_id, physnet, network_id)

            # Skip reconciliation if OVN connections are not available
            if self.ovn_nb_idl is None or self.ovn_sb_idl is None:
                LOG.error("OVN connections not available, cannot perform "
                          "targeted reconciliation")
                return

            # Ensure infrastructure networks exist (creates if missing)
            self._ensure_infrastructure_networks()

            # Get subport anchor network
            anchor_network_id = self._get_subport_anchor_network_id()
            if not anchor_network_id:
                LOG.error("Cannot reconcile VLAN without anchor network")
                return

            # Get VNI and segment_id for this network if adding
            segment_id = vni = None
            if action == 'add':
                vni, segment_id = self._get_vni_and_segment_for_network(
                    network_id, physnet, vlan_id)
                if not segment_id:
                    LOG.error(
                        "Cannot create subport: segment not found for "
                        "network %s VLAN %d on physnet %s. Skipping "
                        "reconciliation.", network_id, vlan_id, physnet)
                    return
                if not vni:
                    LOG.warning(
                        "No VNI found for network %s, subport will "
                        "not have L2VNI mapping configured", network_id)

            # Find all chassis that have this physnet
            chassis_set = self._get_all_chassis_with_physnet(physnet)
            if not chassis_set:
                LOG.debug("No chassis found with physnet %s", physnet)
                return

            # For each chassis, add or remove the subport
            for system_id in chassis_set:
                if not self._should_manage_chassis(system_id):
                    continue

                # Ensure trunk exists
                trunk_id = self._find_or_create_trunk(system_id, physnet)
                if not trunk_id:
                    LOG.error("Cannot reconcile VLAN %d for chassis %s: "
                              "trunk not found/created", vlan_id, system_id)
                    continue

                # Add or remove this specific VLAN
                if action == 'add':
                    self._ensure_single_subport(
                        trunk_id, system_id, physnet, vlan_id,
                        anchor_network_id, segment_id, vni=vni)
                elif action == 'remove':
                    self._remove_single_subport(
                        trunk_id, system_id, physnet, vlan_id)

            LOG.info("Completed targeted reconciliation for %s VLAN %d "
                     "(VNI: %s) on physnet %s",
                     action, vlan_id, vni if vni else 'none', physnet)

        except (sdkexc.SDKException, ovs_exc.OvsdbAppException):
            LOG.exception(
                "Failed targeted reconciliation for VLAN %d on "
                "physnet %s, will retry on next periodic reconciliation",
                vlan_id, physnet)

    def _ensure_infrastructure_networks(self):
        """Ensure ha_chassis_group and subport anchor networks exist.

        Infrastructure networks are metadata/modeling networks that don't
        pass actual traffic but are used for trunk port management:

        1. Subport Anchor Network (singular, shared):
           - All trunk SUBPORTS attach to this network
           - Used to signal VLAN bindings to ML2 switch plugins
           - Config: l2vni_subport_anchor_network

        2. HA Chassis Group Networks (multiple, one per group):
           - Trunk ANCHOR PORTS (parents) attach to these networks
           - One network created per OVN ha_chassis_group
           - Named: l2vni-ha-group-{group_name}

        Note: Despite the name "subport anchor network", it hosts subports,
        NOT anchor ports. Anchor ports attach to HA group networks.
        """
        if not CONF.l2vni.l2vni_auto_create_networks:
            LOG.debug("Auto-creation of L2VNI networks is disabled")
            return

        # Ensure subport anchor network exists (for trunk subports)
        self._ensure_subport_anchor_network()

        # Ensure network per ha_chassis_group (for trunk anchor ports),
        # but only for groups that contain chassis we manage
        for ha_group in self._get_ha_chassis_groups():
            # Check if this group contains any chassis we manage
            if self._ha_group_has_managed_chassis(ha_group):
                self._ensure_ha_group_network(ha_group)

    def _ha_group_has_managed_chassis(self, ha_group):
        """Check if HA group contains any chassis this agent manages.

        :param ha_group: OVN HA_Chassis_Group row
        :returns: bool
        """
        for ha_chassis in ha_group.ha_chassis:
            chassis = self._get_chassis_by_name(ha_chassis.chassis_name)
            if chassis:
                # Chassis name IS the system-id
                if self._should_manage_chassis(chassis.name):
                    return True
        return False

    def _ensure_network(self, network_name, description):
        """Ensure a network exists, creating if necessary.

        :param network_name: Name of the network
        :param description: Description for the network
        :returns: Network ID
        :raises: Exception if network creation fails
        """
        # Check if network exists
        networks = self.neutron.network.networks(name=network_name)
        for network in networks:
            return network.id

        # Create network with configured type
        network_type = CONF.l2vni.l2vni_subport_anchor_network_type
        LOG.info("Creating L2VNI network '%s' with type '%s'",
                 network_name, network_type)
        try:
            network = self.neutron.network.create_network(
                name=network_name,
                description=description,
                admin_state_up=True,
                shared=False,
                is_default=False,
                provider_network_type=network_type
            )
            LOG.info("Created L2VNI network '%s' (%s) with type '%s'",
                     network_name, network.id, network_type)
            return network.id
        except sdkexc.BadRequestException as e:
            LOG.error(
                "Failed to create L2VNI network '%s' with type '%s'. This "
                "indicates a misconfiguration - the requested network type "
                "is not available in your environment. Please verify that "
                "'%s' is configured in ml2_type_drivers and enabled in your "
                "Neutron deployment. Error: %s",
                network_name, network_type, network_type, e)
            raise

    def _ensure_subport_anchor_network(self):
        """Ensure the shared subport anchor network exists.

        This network is where all trunk SUBPORTS are attached. It is used
        to signal VLAN bindings to ML2 switch plugins but does not pass
        actual traffic.

        Note: Despite the name, this network does NOT host "anchor ports"
        (trunk parents). Those attach to HA chassis group networks.

        :returns: Network ID
        """
        network_name = CONF.l2vni.l2vni_subport_anchor_network
        return self._ensure_network(
            network_name,
            'L2VNI subport anchor network for VLAN signaling'
        )

    def _ensure_ha_group_network(self, ha_group):
        """Ensure network exists for an ha_chassis_group.

        Creates a network for modeling the ha_chassis_group. This network
        is where trunk ANCHOR PORTS (trunk parents) are attached. It does
        not pass actual traffic, only used for modeling/metadata.

        Note: Trunk SUBPORTS attach to the separate "subport anchor network",
        not to these HA group networks.

        :param ha_group: OVN HA_Chassis_Group row
        :returns: Network ID
        """
        network_name = f"l2vni-ha-group-{ha_group.name}"
        description = f'L2VNI network for ha_chassis_group {ha_group.name}'
        return self._ensure_network(network_name, description)

    def _get_ha_chassis_groups(self):
        """Get all ha_chassis_groups from OVN.

        :returns: List of OVN HA_Chassis_Group rows
        """
        try:
            if not hasattr(self.ovn_nb_idl, 'tables'):
                LOG.warning("OVN NB IDL not available")
                return []

            ha_groups = []
            # Access IDL tables: tables['TableName'].rows.values()
            if 'HA_Chassis_Group' in self.ovn_nb_idl.tables:
                table = self.ovn_nb_idl.tables['HA_Chassis_Group']
                for row in table.rows.values():
                    ha_groups.append(row)

            return ha_groups

        except (AttributeError, KeyError):
            LOG.exception("Failed to get ha_chassis_groups from OVN")
            return []

    def _discover_trunks(self):
        """Discover existing trunk ports for network nodes.

        Builds a map of (chassis_system_id, physnet) -> trunk_id by:
        1. Finding all chassis in ha_chassis_groups
        2. Getting physnets from bridge-mappings
        3. Looking up or creating trunk ports

        :returns: dict {(system_id, physnet): trunk_id}
        """
        trunk_map = {}

        # Get all chassis in ha_chassis_groups
        chassis_physnets = self._get_chassis_physnets()

        for (system_id, physnet) in chassis_physnets:
            # Skip chassis this agent doesn't manage (hash ring filtering)
            if not self._should_manage_chassis(system_id):
                continue

            trunk_id = self._find_or_create_trunk(system_id, physnet)
            if trunk_id:
                trunk_map[(system_id, physnet)] = trunk_id

        return trunk_map

    def _get_chassis_physnets(self):
        """Get all (chassis_system_id, physnet) combinations.

        Finds chassis in ha_chassis_groups and extracts their
        physical networks from bridge-mappings.

        :returns: set of (system_id, physnet) tuples
        """
        chassis_physnets = set()

        try:
            # Get all chassis referenced in ha_chassis_groups
            chassis_names = set()
            for ha_group in self._get_ha_chassis_groups():
                for ha_chassis in ha_group.ha_chassis:
                    chassis_names.add(ha_chassis.chassis_name)

            # Get physnets for each chassis
            if not hasattr(self.ovn_sb_idl, 'tables'):
                LOG.warning("OVN SB IDL not available")
                return chassis_physnets

            if 'Chassis' in self.ovn_sb_idl.tables:
                for chassis in self.ovn_sb_idl.tables['Chassis'].rows.values():
                    if chassis.name not in chassis_names:
                        continue

                    # The chassis name IS the system-id (UUID)
                    system_id = chassis.name

                    bridge_mappings = chassis.other_config.get(
                        'ovn-bridge-mappings', '')
                    physnets = self._parse_bridge_mappings(bridge_mappings)

                    for physnet in physnets:
                        chassis_physnets.add((system_id, physnet))

        except (AttributeError, KeyError):
            LOG.exception("Failed to get chassis physnets")

        return chassis_physnets

    def _parse_bridge_mappings(self, bridge_mappings_str):
        """Parse OVN bridge-mappings string to extract physnets.

        :param bridge_mappings_str: String like "physnet1:br-ex,physnet2:br2"
        :returns: List of physical network names
        """
        physnets = []
        if not bridge_mappings_str:
            return physnets

        for mapping in bridge_mappings_str.split(','):
            if ':' not in mapping:
                continue
            physnet = mapping.split(':')[0].strip()
            if physnet:
                physnets.append(physnet)

        return physnets

    def _find_or_create_trunk(self, system_id, physnet):
        """Find existing trunk or create new one.

        :param system_id: OVN chassis system-id
        :param physnet: Physical network name
        :returns: Trunk ID or None
        """
        # Always reconcile anchor port first (creates or updates existing)
        anchor_port_id = self._find_or_create_anchor_port(system_id, physnet)
        if not anchor_port_id:
            LOG.warning("Cannot find or create anchor port for "
                        "chassis %s physnet %s", system_id, physnet)
            return None

        # Try to find existing trunk
        trunk_name = _get_trunk_name(system_id, physnet)
        trunks = self.neutron.network.trunks(name=trunk_name)
        for trunk in trunks:
            return trunk.id

        # Create trunk
        try:
            LOG.debug("Creating trunk %s for chassis %s physnet %s",
                      trunk_name, system_id, physnet)
            trunk = self.neutron.network.create_trunk(
                name=trunk_name,
                description=f'trunk for chassis {system_id} '
                            f'on {physnet}',
                port_id=anchor_port_id,
                admin_state_up=True
            )
            LOG.debug("Created trunk %s", trunk.id)
            return trunk.id

        except sdkexc.SDKException:
            LOG.exception("Failed to create trunk for chassis %s physnet %s",
                          system_id, physnet)
            return None

    def _find_or_create_anchor_port(self, system_id, physnet):
        """Find or create anchor port for trunk.

        The anchor port is the trunk PARENT port. It attaches to an
        HA chassis group network (NOT the "subport anchor network").

        Terminology clarification:
        - Anchor port = trunk parent (attaches to HA group network)
        - Subports = trunk children (attach to subport anchor network)

        :param system_id: OVN chassis system-id
        :param physnet: Physical network name
        :returns: Port ID or None
        """
        port_name = _get_anchor_port_name(system_id, physnet)

        # Try to find existing port
        ports = self.neutron.network.ports(name=port_name)
        for port in ports:
            binding_profile = port.binding_profile or {}
            current_local_link_info = binding_profile.get(
                'local_link_information')

            # Early return if already configured correctly
            if current_local_link_info:
                return port.id

            # Port exists but missing local_link_information
            local_link_list = self._get_local_link_information(
                system_id, physnet)
            if local_link_list:
                binding_profile['local_link_information'] = local_link_list
                self.neutron.network.update_port(
                    port.id, binding_profile=binding_profile)
                LOG.info("Updated anchor port %s with missing "
                         "local_link_information (%d link(s))",
                         port.id, len(local_link_list))
            else:
                LOG.warning(
                    "Anchor port %s missing local_link_information for "
                    "chassis %s physnet %s", port.id, system_id, physnet)

            return port.id

        # Need to create - find ha_group network for this chassis
        network_id = self._find_ha_group_network_for_chassis(system_id)
        if not network_id:
            LOG.warning("Cannot find ha_chassis_group network for "
                        "chassis %s", system_id)
            return None

        # Get local_link_information for new anchor port
        local_link_list = self._get_local_link_information(system_id, physnet)
        if not local_link_list:
            LOG.warning("Could not determine local_link_information for "
                        "chassis %s physnet %s. Port will be created but "
                        "may not bind properly when subports are added.",
                        system_id, physnet)

        # Build binding profile
        binding_profile = {
            'system_id': system_id,
            'physical_network': physnet
        }
        if local_link_list:
            binding_profile['local_link_information'] = local_link_list
            LOG.info("Creating anchor port with %d link(s) for chassis %s "
                     "physnet %s", len(local_link_list), system_id, physnet)

        # Create anchor port
        try:
            LOG.debug("Creating anchor port %s for chassis %s physnet %s",
                      port_name, system_id, physnet)
            port = self.neutron.network.create_port(
                name=port_name,
                network_id=network_id,
                device_owner=DEVICE_OWNER_L2VNI_ANCHOR,
                admin_state_up=True,
                binding_vnic_type=portbindings.VNIC_BAREMETAL,
                binding_profile=binding_profile
            )
            LOG.debug("Created anchor port %s", port.id)
            return port.id

        except sdkexc.SDKException:
            LOG.exception("Failed to create anchor port for chassis %s "
                          "physnet %s", system_id, physnet)
            return None

    def _find_ha_group_network_for_chassis(self, system_id):
        """Find ha_chassis_group network that contains this chassis.

        :param system_id: OVN chassis system-id (same as chassis name)
        :returns: Network ID or None
        """
        # Find ha_chassis_group containing this chassis
        for ha_group in self._get_ha_chassis_groups():
            for ha_chassis in ha_group.ha_chassis:
                chassis = self._get_chassis_by_name(ha_chassis.chassis_name)
                # Chassis name IS the system-id
                if chassis and chassis.name == system_id:
                    # Found the group, find its network
                    network_name = f"l2vni-ha-group-{ha_group.name}"
                    networks = self.neutron.network.networks(
                        name=network_name)
                    # networks should be a list, because were asking the api
                    # for a list of networks matching the name with a single
                    # resulting entry if found, otherwise an empty list.
                    for network in networks:
                        # If we have a match, return the first entry.
                        return network.id

        return None

    def _get_chassis_by_name(self, chassis_name):
        """Get OVN chassis by name.

        :param chassis_name: Chassis name
        :returns: Chassis row or None
        """
        try:
            if not hasattr(self.ovn_sb_idl, 'tables'):
                return None

            if 'Chassis' in self.ovn_sb_idl.tables:
                for chassis in self.ovn_sb_idl.tables['Chassis'].rows.values():
                    if chassis.name == chassis_name:
                        return chassis

        except (AttributeError, KeyError):
            LOG.exception("Failed to get chassis %s", chassis_name)

        return None

    def _calculate_required_vlans(self):
        """Calculate which VLANs each chassis needs.

        A chassis needs a VLAN if:
        - There's a localnet port for a network on that physnet
        - The chassis is in the ha_chassis_group for a router on that network

        For each required VLAN, also captures the associated VNI and
        segment_id from the network's segments to enable L2VNI mapping
        configuration on switches and segment-based cleanup.

        :returns: dict {(system_id, physnet): {
                            vlan_id: {
                                'vni': vni,
                                'segment_id': segment_id}
                            }
                       }
                  where vni is an integer for L2VNI networks or None for
                  pure VLAN networks, and segment_id is the VLAN segment UUID
        """
        chassis_vlan_vni_map = {}

        networks_with_segments = self._get_networks_with_segments()

        for network_id, segment_info in networks_with_segments.items():
            # Extract VNI from overlay segments
            vni_segments = segment_info['vni_segments']
            vni = None

            if vni_segments:
                # Use the first overlay segment
                vni = vni_segments[0].segmentation_id

                # Warn if multiple overlay segments exist (unusual config)
                if len(vni_segments) > 1:
                    LOG.warning(
                        "Network %s has %d overlay segments. Only the first "
                        "(VNI %s, type %s) will be used for L2VNI mapping. "
                        "Multiple overlay segments per network is not "
                        "supported.",
                        network_id, len(vni_segments), vni,
                        vni_segments[0].network_type)

            for segment in segment_info['vlan_segments']:
                physnet = segment.physical_network
                vlan_id = segment.segmentation_id
                segment_id = segment.id

                chassis_set = self._find_chassis_for_network(
                    network_id, physnet)

                for system_id in chassis_set:
                    if not self._should_manage_chassis(system_id):
                        continue

                    key = (system_id, physnet)
                    if key not in chassis_vlan_vni_map:
                        chassis_vlan_vni_map[key] = {}
                    chassis_vlan_vni_map[key][vlan_id] = {
                        'vni': vni,
                        'segment_id': segment_id
                    }

        return chassis_vlan_vni_map

    def _get_networks_with_segments(self):
        """Get networks with their VLAN and overlay segments.

        :returns: dict {network_id: {
            'vlan_segments': [segment objects],
            'vni_segments': [segment objects]
        }}
        """
        networks = {}

        try:
            segments = self.neutron.network.segments()
            for segment in segments:
                network_id = segment.network_id
                if network_id not in networks:
                    networks[network_id] = {
                        'vlan_segments': [],
                        'vni_segments': []
                    }

                if segment.network_type == n_const.TYPE_VLAN:
                    networks[network_id]['vlan_segments'].append(segment)
                elif segment.network_type in [n_const.TYPE_VXLAN,
                                              n_const.TYPE_GENEVE]:
                    networks[network_id]['vni_segments'].append(segment)

        except sdkexc.SDKException:
            LOG.exception("Failed to get networks with VLAN and overlay "
                          "segments")

        return networks

    def _get_vni_and_segment_for_network(self, network_id, physnet, vlan_id):
        """Get VNI and segment_id for a network.

        :param network_id: Neutron network UUID
        :param physnet: Physical network name
        :param vlan_id: VLAN ID to match for segment_id lookup
        :returns: tuple (vni, segment_id) where vni is int or None,
                  and segment_id is the VLAN segment UUID or None
        """
        vni = None
        segment_id = None

        try:
            segments = self.neutron.network.segments(network_id=network_id)
            for segment in segments:

                # Extract VNI from overlay segment
                if segment.network_type in [n_const.TYPE_VXLAN,
                                            n_const.TYPE_GENEVE]:
                    vni = segment.segmentation_id

                # Extract segment_id from matching VLAN segment
                if (segment.network_type == n_const.TYPE_VLAN
                        and segment.physical_network == physnet
                        and segment.segmentation_id == vlan_id):
                    segment_id = segment.id

        except sdkexc.SDKException:
            LOG.exception("Failed to get segments for network %s", network_id)

        return vni, segment_id

    def _find_chassis_for_network(self, network_id, physnet):
        """Find chassis that need a specific network's VLAN.

        :param network_id: Neutron network UUID
        :param physnet: Physical network name
        :returns: set of system_ids
        """
        chassis_set = set()

        # Check for localnet ports - if they exist, all chassis with
        # this physnet need the VLAN
        if self._has_localnet_port(network_id, physnet):
            chassis_set.update(self._get_all_chassis_with_physnet(physnet))

        # Check for router ports with ha_chassis_group
        chassis_set.update(
            self._get_chassis_for_router_ports(network_id, physnet))

        return chassis_set

    def _has_localnet_port(self, network_id, physnet):
        """Check if network has a localnet port on physnet.

        :param network_id: Neutron network UUID
        :param physnet: Physical network name
        :returns: bool
        """
        try:
            ls_name = ovn_utils.ovn_name(network_id)
            if not hasattr(self.ovn_nb_idl, 'tables'):
                return False

            if 'Logical_Switch_Port' not in self.ovn_nb_idl.tables:
                return False

            if 'Logical_Switch' not in self.ovn_nb_idl.tables:
                return False

            lsp_table = self.ovn_nb_idl.tables['Logical_Switch_Port']
            ls_table = self.ovn_nb_idl.tables['Logical_Switch']
            for lsp in lsp_table.rows.values():
                if not (lsp.type == 'localnet'
                        and lsp.options.get('network_name') == physnet):
                    continue

                for ls in ls_table.rows.values():
                    if ls.name == ls_name and lsp in ls.ports:
                        return True

        except (AttributeError, KeyError):
            LOG.exception("Failed to check for localnet port on network %s.",
                          network_id)

        return False

    def _get_all_chassis_with_physnet(self, physnet):
        """Get all chassis that have a specific physnet.

        :param physnet: Physical network name
        :returns: set of system_ids (chassis names)
        """
        chassis_set = set()

        try:
            if not hasattr(self.ovn_sb_idl, 'tables'):
                return chassis_set

            if 'Chassis' not in self.ovn_sb_idl.tables:
                return chassis_set

            for chassis in self.ovn_sb_idl.tables['Chassis'].rows.values():
                bridge_mappings = chassis.other_config.get(
                    'ovn-bridge-mappings', '')
                physnets = self._parse_bridge_mappings(bridge_mappings)

                if physnet in physnets:
                    # Chassis name IS the system-id
                    chassis_set.add(chassis.name)

        except (AttributeError, KeyError):
            LOG.exception("Failed to get chassis with physnet %s",
                          physnet)

        return chassis_set

    def _get_chassis_for_router_ports(self, network_id, physnet):
        """Get chassis hosting router ports on this network.

        :param network_id: Neutron network UUID
        :param physnet: Physical network name
        :returns: set of system_ids
        """
        chassis_set = set()

        try:
            ls_name = ovn_utils.ovn_name(network_id)
            if not hasattr(self.ovn_nb_idl, 'tables'):
                return chassis_set

            if 'Logical_Router_Port' not in self.ovn_nb_idl.tables:
                return chassis_set

            if 'Logical_Switch_Port' not in self.ovn_nb_idl.tables:
                return chassis_set

            if 'Logical_Switch' not in self.ovn_nb_idl.tables:
                return chassis_set

            lrp_table = self.ovn_nb_idl.tables['Logical_Router_Port']
            lsp_table = self.ovn_nb_idl.tables['Logical_Switch_Port']
            ls_table = self.ovn_nb_idl.tables['Logical_Switch']

            for lrp in lrp_table.rows.values():
                # Check if this LRP is connected to our logical switch
                # via its peer LSP
                for lsp in lsp_table.rows.values():
                    if not (lsp.type == 'router'
                            and lsp.options.get('router-port') == lrp.name):
                        continue

                    # Find the logical switch
                    for ls in ls_table.rows.values():
                        if ls.name == ls_name and lsp in ls.ports:
                            # Found a router port on network
                            chassis_set.update(self._get_chassis_for_lrp(lrp))
                            # Found the switch for this LSP, check next LRP
                            break

        except (AttributeError, KeyError):
            LOG.exception("Failed to get chassis for router ports on "
                          "network %s", network_id)

        return chassis_set

    def _get_chassis_for_lrp(self, lrp):
        """Get chassis assigned to a logical router port.

        :param lrp: Logical_Router_Port row
        :returns: set of system_ids (chassis names)
        """
        chassis_set = set()

        try:
            # Check ha_chassis_group (preferred)
            if hasattr(lrp, 'ha_chassis_group') and lrp.ha_chassis_group:
                for ha_chassis in lrp.ha_chassis_group.ha_chassis:
                    chassis = self._get_chassis_by_name(
                        ha_chassis.chassis_name)
                    if chassis:
                        # Chassis name IS the system-id
                        chassis_set.add(chassis.name)

            # Check legacy gateway_chassis
            elif hasattr(lrp, 'gateway_chassis') and lrp.gateway_chassis:
                for gw_chassis in lrp.gateway_chassis:
                    chassis = self._get_chassis_by_name(
                        gw_chassis.chassis_name)
                    if chassis:
                        # Chassis name IS the system-id
                        chassis_set.add(chassis.name)

        except (AttributeError, KeyError):
            LOG.exception("Failed to get chassis for LRP %s",
                          lrp.name)

        return chassis_set

    def _reconcile_subports(self, trunk_map, required_vlans):
        """Reconcile subports to match required VLAN state.

        :param trunk_map: dict {(system_id, physnet): trunk_id}
        :param required_vlans: dict {(system_id, physnet): {vlan_id: vni}}
                               where vni may be None for non-L2VNI networks
        """
        subport_anchor_net = self._get_subport_anchor_network_id()
        if not subport_anchor_net:
            LOG.error("Cannot reconcile subports without anchor network")
            return

        for (system_id, physnet), trunk_id in trunk_map.items():
            vlan_vni_map = required_vlans.get((system_id, physnet), {})
            self._reconcile_trunk_subports(
                trunk_id, system_id, physnet, vlan_vni_map, subport_anchor_net)

    def _get_subport_anchor_network_id(self):
        """Get the subport anchor network ID.

        :returns: Network ID or None
        """
        network_name = CONF.l2vni.l2vni_subport_anchor_network
        networks = self.neutron.network.networks(name=network_name)
        for network in networks:
            return network.id
        return None

    def _reconcile_trunk_subports(self, trunk_id, system_id, physnet,
                                  vlan_vni_map, anchor_network_id):
        """Reconcile subports for a single trunk.

        :param trunk_id: Trunk UUID
        :param system_id: Chassis system-id
        :param physnet: Physical network name
        :param vlan_vni_map: dict {vlan_id: {'vni': vni,
                                              'segment_id': segment_id}}
        :param anchor_network_id: Subport anchor network UUID
        """
        # Get current subports
        try:
            trunk = self.neutron.network.get_trunk(trunk_id)
            current_subports = {sp['segmentation_id']: sp['port_id']
                                for sp in trunk.sub_ports}
        except sdkexc.SDKException:
            LOG.exception("Failed to get trunk %s", trunk_id)
            return

        # Add missing subports with VNI and segment_id
        for vlan_id in vlan_vni_map.keys() - set(current_subports.keys()):
            vlan_info = vlan_vni_map.get(vlan_id)
            if isinstance(vlan_info, dict):
                vni = vlan_info.get('vni')
                segment_id = vlan_info.get('segment_id')
            else:
                vni = vlan_info
                segment_id = None
            if not segment_id:
                LOG.error(
                    "Cannot add subport for VLAN %d: segment_id missing. "
                    "This indicates a bug in _calculate_required_vlans().",
                    vlan_id)
                continue
            self._add_subport(trunk_id, system_id, physnet, vlan_id,
                              anchor_network_id, segment_id, vni=vni)

        # Remove extra subports
        for vlan_id in set(current_subports.keys()) - vlan_vni_map.keys():
            self._remove_subport(trunk_id, current_subports[vlan_id],
                                 system_id, physnet, vlan_id)

    def _add_subport(self, trunk_id, system_id, physnet, vlan_id,
                     anchor_network_id, segment_id, vni=None):
        """Add a subport to a trunk.

        Subports are the trunk CHILDREN that attach to the shared
        "subport anchor network". They signal VLAN bindings to ML2
        switch plugins.

        :param trunk_id: Trunk UUID
        :param system_id: Chassis system-id
        :param physnet: Physical network name
        :param vlan_id: VLAN ID for segmentation
        :param anchor_network_id: Subport anchor network UUID (the shared
                                  network all subports attach to)
        :param segment_id: Neutron VLAN segment UUID
        :param vni: VNI for L2VNI mapping (optional, None for pure VLAN
                    networks)
        """
        port_name = _get_subport_name(system_id, physnet, vlan_id)

        # Get chassis hostname for binding
        hostname = self._get_chassis_hostname(system_id)
        if not hostname:
            LOG.warning("Could not determine hostname for chassis %s. "
                        "Subport will not be bound.", system_id)

        try:
            # Create port
            LOG.debug("Creating subport %s for trunk %s (segment: %s, "
                      "VNI: %s)", port_name, trunk_id, segment_id,
                      vni if vni else 'none')

            # Build binding profile with segment_id and VNI
            binding_profile = {
                'physical_network': physnet,
                'segment_id': segment_id
            }
            if vni:
                binding_profile['vni'] = vni

            port = self.neutron.network.create_port(
                name=port_name,
                network_id=anchor_network_id,
                device_owner=DEVICE_OWNER_L2VNI_SUBPORT,
                admin_state_up=True,
                binding_vnic_type='baremetal',
                binding_profile=binding_profile
            )

            # Set binding:host_id on subport if we have a hostname
            if hostname:
                self.neutron.network.update_port(
                    port.id,
                    **{'binding:host_id': hostname}
                )
                LOG.debug("Set binding:host_id=%s for subport %s",
                          hostname, port.id)

            # Add as subport
            self.neutron.network.add_trunk_subports(
                trunk_id,
                [{'port_id': port.id,
                  'segmentation_type': 'vlan',
                  'segmentation_id': vlan_id}]
            )
            LOG.debug("Added subport %s (VLAN %d, VNI: %s) to trunk %s",
                      port.id, vlan_id, vni if vni else 'none', trunk_id)

        except sdkexc.SDKException:
            LOG.exception("Failed to add subport for trunk %s VLAN %d",
                          trunk_id, vlan_id)

    def _remove_subport(self, trunk_id, port_id, system_id, physnet, vlan_id):
        """Remove a subport from a trunk.

        :param trunk_id: Trunk UUID
        :param port_id: Subport UUID
        :param system_id: Chassis system-id
        :param physnet: Physical network name
        :param vlan_id: VLAN ID
        """
        try:
            LOG.debug("Removing subport %s (VLAN %d) from trunk %s",
                      port_id, vlan_id, trunk_id)

            # Remove from trunk
            self.neutron.network.delete_trunk_subports(
                trunk_id, [{'port_id': port_id}])

            # Delete port
            self.neutron.network.delete_port(port_id)

            LOG.debug("Removed subport %s from trunk %s", port_id, trunk_id)

        except sdkexc.SDKException:
            LOG.exception("Failed to remove subport %s from trunk %s",
                          port_id, trunk_id)

    def _ensure_single_subport(self, trunk_id, system_id, physnet, vlan_id,
                               anchor_network_id, segment_id, vni=None):
        """Ensure a single subport exists on a trunk.

        Idempotent - checks if subport already exists before creating.

        :param trunk_id: Trunk UUID
        :param system_id: Chassis system-id
        :param physnet: Physical network name
        :param vlan_id: VLAN ID for segmentation
        :param anchor_network_id: Subport anchor network UUID
        :param segment_id: Neutron VLAN segment UUID
        :param vni: VNI for L2VNI mapping (optional, None for pure VLAN
                    networks)
        """
        try:
            trunk = self.neutron.network.get_trunk(trunk_id)
            existing_vlans = {sp['segmentation_id'] for sp in trunk.sub_ports}

            if vlan_id in existing_vlans:
                LOG.debug("Subport for VLAN %d already exists on trunk %s",
                          vlan_id, trunk_id)
                return

            # Add the subport
            self._add_subport(trunk_id, system_id, physnet, vlan_id,
                              anchor_network_id, segment_id, vni=vni)

        except sdkexc.SDKException:
            LOG.exception("Failed to ensure subport for VLAN %d on trunk %s",
                          vlan_id, trunk_id)

    def _remove_single_subport(self, trunk_id, system_id, physnet, vlan_id):
        """Remove a single subport from a trunk if it exists.

        Idempotent - checks if subport exists before removing.

        :param trunk_id: Trunk UUID
        :param system_id: Chassis system-id
        :param physnet: Physical network name
        :param vlan_id: VLAN ID to remove
        """
        try:
            trunk = self.neutron.network.get_trunk(trunk_id)
            subport_to_remove = None

            for sp in trunk.sub_ports:
                if sp['segmentation_id'] == vlan_id:
                    subport_to_remove = sp['port_id']
                    break

            if not subport_to_remove:
                LOG.debug("Subport for VLAN %d does not exist on trunk %s",
                          vlan_id, trunk_id)
                return

            # Remove the subport
            self._remove_subport(trunk_id, subport_to_remove, system_id,
                                 physnet, vlan_id)

        except sdkexc.SDKException:
            LOG.exception("Failed to remove subport for VLAN %d from "
                          "trunk %s", vlan_id, trunk_id)

    def _get_local_link_information(self, system_id, physnet):
        """Get local_link_information data using tiered approach.

        Tries in order:
        1. OVN LLDP data
        2. Ironic port data
        3. YAML configuration file

        Aggregates multiple links for LAG/bonding configurations where
        multiple physical ports connect to the same physical network.

        :param system_id: Chassis system-id
        :param physnet: Physical network name
        :returns: list of dicts with local_link_information, or None if no
                  connection data found. List may contain multiple entries
                  for LAG/bonding scenarios.
        """
        # Try OVN LLDP data first
        lldp_data = self._get_lldp_from_ovn(system_id, physnet)
        if lldp_data:
            return lldp_data

        # Try Ironic discovery
        ironic_data = self._get_local_link_from_ironic(system_id, physnet)
        if ironic_data:
            return ironic_data

        # Fall back to YAML config
        return self._get_local_link_from_config(system_id, physnet)

    def _get_lldp_from_ovn(self, system_id, physnet):
        """Get local_link_information from OVN LLDP data.

        Extracts LLDP information from OVN Southbound Port table for the
        chassis and physical network. Aggregates all ports on the bridge
        to support LAG/bonding configurations.

        :param system_id: Chassis system-id (same as chassis name)
        :param physnet: Physical network name
        :returns: list of dicts with local_link_information, or None if no
                  LLDP data found
        """
        try:
            if not hasattr(self.ovn_sb_idl, 'tables'):
                LOG.debug("OVN SB IDL not available for LLDP lookup")
                return None

            # Find the chassis - chassis name IS the system-id
            chassis = None
            if 'Chassis' in self.ovn_sb_idl.tables:
                for c in self.ovn_sb_idl.tables['Chassis'].rows.values():
                    if c.name == system_id:
                        chassis = c
                        break

            if not chassis:
                return None

            # Find port on this chassis that maps to the physnet
            bridge_mappings = chassis.other_config.get(
                'ovn-bridge-mappings', '')
            physnet_to_bridge = {}
            for mapping in bridge_mappings.split(','):
                if ':' not in mapping:
                    continue
                pnet, bridge = mapping.split(':', 1)
                physnet_to_bridge[pnet.strip()] = bridge.strip()

            bridge_name = physnet_to_bridge.get(physnet)
            if not bridge_name:
                return None

            # Aggregate all ports on this chassis with LLDP for this bridge
            # Supports LAG/bonding with multiple ports to same bridge
            local_links = []
            if 'Port' in self.ovn_sb_idl.tables:
                for port in self.ovn_sb_idl.tables['Port'].rows.values():
                    # Check if port belongs to this chassis
                    if port.chassis != chassis:
                        continue

                    # Check if this port is on the correct bridge
                    # Port.interfaces is a list of Interface objects
                    if not hasattr(port, 'interfaces') or not port.interfaces:
                        continue

                    port_on_bridge = False
                    for iface in port.interfaces:
                        # Interface.name typically matches the OVS interface
                        # which should contain the bridge name for physical
                        # interfaces (e.g., "br-physnet1", "eth0", etc.)
                        if not hasattr(iface, 'name'):
                            continue
                        iface_name = iface.name
                        # Check if interface name matches or is on bridge
                        if (iface_name == bridge_name
                                or iface_name.startswith(bridge_name)):
                            port_on_bridge = True
                            break

                    if not port_on_bridge:
                        continue

                    # Get LLDP data from external_ids
                    lldp = port.external_ids
                    chassis_id = lldp.get('lldp_chassis_id')
                    port_id = lldp.get('lldp_port_id')
                    system_name = lldp.get('lldp_system_name')

                    if chassis_id and port_id:
                        LOG.debug("Found LLDP data for chassis %s physnet %s "
                                  "bridge %s: switch_id=%s, port_id=%s, "
                                  "switch_info=%s",
                                  system_id, physnet, bridge_name, chassis_id,
                                  port_id, system_name)
                        local_links.append({
                            'switch_id': chassis_id,
                            'port_id': port_id,
                            'switch_info': system_name or ''
                        })

            if local_links:
                LOG.info("Found %d link(s) from LLDP for chassis %s "
                         "physnet %s", len(local_links), system_id, physnet)
                return local_links

            return None

        except (AttributeError, KeyError):
            LOG.exception("Failed to get LLDP data from OVN for chassis %s "
                          "physnet %s.", system_id, physnet)
            return None

    def _fetch_ironic_data_for_system_id(self, system_id):
        """Fetch node and ports for a specific system_id from Ironic.

        Queries Ironic efficiently by:
        1. Filtering nodes by conductor_group/shard if configured
        2. Requesting only minimal fields needed
        3. Only fetching ports for the matched node

        :param system_id: Chassis system-id
        :returns: dict with cached_at, node_uuid, and ports list, or None
        """
        try:
            # Build query filters
            query_params = {}
            if CONF.l2vni.ironic_conductor_group:
                query_params['conductor_group'] = (
                    CONF.l2vni.ironic_conductor_group)
            if CONF.l2vni.ironic_shard:
                query_params['shard'] = CONF.l2vni.ironic_shard

            # Query nodes with minimal fields for performance
            nodes = self.ironic.nodes(
                fields=['uuid', 'properties'],
                **query_params
            )

            # Find the node with matching system_id
            for node in nodes:
                if node.properties.get('system_id') == system_id:
                    # Found the node - fetch its ports with minimal fields
                    LOG.debug("Found Ironic node %s for system_id %s, "
                              "fetching ports", node.uuid, system_id)

                    ports = self.ironic.ports(
                        node_uuid=node.uuid,
                        fields=['physical_network', 'local_link_connection']
                    )

                    # Build cache entry
                    cache_entry = {
                        'cached_at': time.time(),
                        'node_uuid': node.uuid,
                        'ports': []
                    }

                    for port in ports:
                        if port.local_link_connection:
                            cache_entry['ports'].append({
                                'physnet': port.physical_network,
                                'local_link': port.local_link_connection
                            })

                    LOG.debug("Cached Ironic data for system_id %s: "
                              "node %s with %d ports",
                              system_id, node.uuid, len(cache_entry['ports']))

                    return cache_entry

            LOG.debug("No Ironic node found with system_id %s", system_id)
            return None

        except (sdkexc.SDKException, AttributeError, KeyError):
            LOG.exception("Failed to fetch Ironic data for system_id %s",
                          system_id)
            return None

    def _aggregate_ironic_ports_for_physnet(self, cache_entry, physnet,
                                            system_id, source_label):
        """Aggregate Ironic ports matching physnet from cache entry.

        Helper method to extract local_link_information data for all ports
        matching a physical network from a cached Ironic data entry.

        :param cache_entry: Cached Ironic data dict with 'ports' list
        :param physnet: Physical network name to filter by
        :param system_id: Chassis system-id (for logging)
        :param source_label: Label for log messages (e.g., "Ironic cache",
                            "Ironic")
        :returns: list of local_link_information dicts, or None if no matches
        """
        local_links = []
        for port in cache_entry['ports']:
            if port['physnet'] == physnet:
                local_links.append(port['local_link'])

        if local_links:
            LOG.debug("Found %d link(s) from %s for chassis %s physnet %s",
                      len(local_links), source_label, system_id, physnet)
            return local_links

        return None

    def _get_local_link_from_ironic(self, system_id, physnet):
        """Get local_link_information from Ironic, using per-record cache.

        Uses a per-record cache with TTL and jitter to avoid thundering herd
        issues when multiple agents are running. Each system_id is cached
        independently and only refreshed when its TTL expires.

        Aggregates all Ironic ports matching the physnet to support LAG/bonding
        configurations where multiple ports share the same physical_network.

        :param system_id: Chassis system-id
        :param physnet: Physical network name
        :returns: list of dicts with local_link_information, or None if no
                  ports found
        """
        try:
            # Check if we have a valid cached entry for this system_id
            if system_id in self._ironic_cache:
                cached_entry = self._ironic_cache[system_id]
                age = time.time() - cached_entry['cached_at']

                # Add jitter (10-20%) to TTL to spread refresh times
                # across multiple agents and avoid thundering herd
                jitter = 0.9 + random.random() * 0.2  # noqa: S311
                ttl_with_jitter = CONF.l2vni.ironic_cache_ttl * jitter

                if age < ttl_with_jitter:
                    # Cache hit - use cached data
                    LOG.debug("Using cached Ironic data for system_id %s "
                              "(age: %.1fs, TTL: %.1fs)",
                              system_id, age, ttl_with_jitter)

                    return self._aggregate_ironic_ports_for_physnet(
                        cached_entry, physnet, system_id, "Ironic cache")
                else:
                    LOG.debug("Ironic cache expired for system_id %s "
                              "(age: %.1fs, TTL: %.1fs)",
                              system_id, age, ttl_with_jitter)

            # Cache miss or expired - fetch data for this system_id
            LOG.debug("Fetching Ironic data for system_id %s (cache miss)",
                      system_id)
            cache_entry = self._fetch_ironic_data_for_system_id(system_id)

            if cache_entry:
                # Update cache with new entry
                self._ironic_cache[system_id] = cache_entry

                return self._aggregate_ironic_ports_for_physnet(
                    cache_entry, physnet, system_id, "Ironic")

            return None

        except (KeyError, AttributeError):
            LOG.exception("Failed to get local_link_information from Ironic "
                          "for chassis %s physnet %s.", system_id,
                          physnet)
            return None

    def _get_local_link_from_node_config(self, node, physnet):
        """Get local_link_information from a network node config entry.

        Supports both single-link and multi-link (LAG/bonding) configurations:
        - Single dict: local_link_information: {switch_id: ..., port_id: ...}
        - List of dicts: local_link_information: [{...}, {...}]

        For backward compatibility, also accepts 'local_link_connection' as an
        alias for 'local_link_information'.

        :param node: Network node config dict from YAML
        :param physnet: Physical network name
        :returns: list of dicts with local_link_information, or None
        """
        for trunk_config in node.get('trunks', []):
            if trunk_config.get('physical_network') == physnet:
                # Try new name first, fallback to old name for backward compat
                local_link = trunk_config.get('local_link_information')
                if not local_link:
                    # Check for deprecated name
                    local_link = trunk_config.get('local_link_connection')
                    if local_link:
                        LOG.warning(
                            "Configuration uses deprecated "
                            "'local_link_connection' field for physnet %s. "
                            "Please update to 'local_link_information' (as a "
                            "list) to match Neutron API naming.",
                            physnet)

                if not local_link:
                    return None

                # Support both single dict and list of dicts
                if isinstance(local_link, list):
                    LOG.debug("Found %d link(s) in config for physnet %s",
                              len(local_link), physnet)
                    return local_link
                elif isinstance(local_link, dict):
                    # Single dict - wrap in list for consistency
                    return [local_link]
                else:
                    LOG.warning("Invalid local_link_information format in "
                                "config for physnet %s: expected dict or list",
                                physnet)
                    return None
        return None

    def _get_local_link_from_config(self, system_id, physnet):
        """Get local_link_information from YAML config file.

        Matches network nodes by system_id (chassis UUID) or hostname.
        This allows the YAML config to use either the predictable hostname
        or the exact chassis UUID.

        :param system_id: Chassis system-id (UUID)
        :param physnet: Physical network name
        :returns: list of dicts with local_link_information or None
        """
        if self._config_cache is None:
            self._load_config()

        if not self._config_cache:
            return None

        # Try to match by system_id first (exact UUID match)
        for node in self._config_cache.get('network_nodes', []):
            if node.get('system_id') == system_id:
                return self._get_local_link_from_node_config(node, physnet)

        # No system_id match found, try hostname fallback
        chassis_hostname = self._get_chassis_hostname(system_id)
        if chassis_hostname:
            for node in self._config_cache.get('network_nodes', []):
                if node.get('hostname') == chassis_hostname:
                    LOG.debug("Matched chassis %s by hostname %s in config",
                              system_id, chassis_hostname)
                    return self._get_local_link_from_node_config(node, physnet)

        return None

    def _get_chassis_hostname(self, system_id):
        """Get hostname for a chassis by system-id.

        :param system_id: Chassis system-id (UUID)
        :returns: Hostname string or None
        """
        try:
            if not hasattr(self.ovn_sb_idl, 'tables'):
                return None

            if 'Chassis' not in self.ovn_sb_idl.tables:
                return None

            for chassis in self.ovn_sb_idl.tables['Chassis'].rows.values():
                if chassis.name == system_id and hasattr(chassis, 'hostname'):
                    return chassis.hostname
        except (AttributeError, KeyError):
            LOG.debug("Failed to get hostname for chassis %s", system_id)

        return None

    def _load_config(self):
        """Load configuration from YAML file."""
        try:
            config_file = CONF.l2vni.l2vni_network_nodes_config
            with open(config_file, 'r') as f:
                self._config_cache = yaml.safe_load(f)
                LOG.debug("Loaded L2VNI configuration from %s", config_file)
        except FileNotFoundError:
            self._config_cache = {}
        except (IOError, OSError, yaml.YAMLError):
            LOG.exception("Failed to load L2VNI config file")
            self._config_cache = {}

    def _cleanup_unused_infrastructure(self):
        """Clean up unused L2VNI infrastructure.

        Removes:
        - Trunks with no subports for deleted chassis
        - Anchor ports for deleted trunks
        - Networks for deleted ha_chassis_groups
        """
        try:
            # Get current chassis/physnet combinations that should exist
            valid_chassis_physnets = self._get_chassis_physnets()

            # Clean up orphaned trunks and anchor ports
            self._cleanup_orphaned_trunks(valid_chassis_physnets)

            # Clean up orphaned ha_chassis_group networks
            self._cleanup_orphaned_networks()

        except (sdkexc.SDKException, AttributeError, KeyError):
            LOG.exception("Failed to clean up unused L2VNI infrastructure.")

    def _cleanup_orphaned_trunks(self, valid_chassis_physnets):
        """Clean up trunks and anchor ports for deleted chassis.

        :param valid_chassis_physnets: set of (system_id, physnet) that
                                      should have trunks
        """
        try:
            # Find all L2VNI trunks
            trunks = self.neutron.network.trunks()
            for trunk in trunks:
                if not trunk.name or not trunk.name.startswith(
                        'l2vni-trunk-'):
                    continue

                # Parse trunk name: l2vni-trunk-{system_id}-{physnet}
                # System_id is a UUID with dashes, so split from right
                name_without_prefix = trunk.name[len('l2vni-trunk-'):]
                parts = name_without_prefix.rsplit('-', 1)
                if len(parts) != 2:
                    continue

                system_id = parts[0]
                physnet = parts[1]

                # Check if this trunk should still exist
                if (system_id, physnet) not in valid_chassis_physnets:
                    LOG.info("Cleaning up orphaned trunk %s for chassis %s "
                             "physnet %s", trunk.id, system_id, physnet)

                    # Get anchor port before deleting trunk
                    anchor_port_id = trunk.port_id

                    # Delete all subports first
                    if trunk.sub_ports:
                        for subport in trunk.sub_ports:
                            try:
                                port_id = subport['port_id']
                                subport_spec = [{'port_id': port_id}]
                                self.neutron.network.delete_trunk_subports(
                                    trunk.id, subport_spec)
                                self.neutron.network.delete_port(
                                    subport['port_id'])
                            except sdkexc.SDKException:
                                LOG.warning("Failed to delete subport %s",
                                            subport['port_id'])

                    # Delete trunk
                    try:
                        self.neutron.network.delete_trunk(trunk.id)
                        LOG.info("Deleted orphaned trunk %s", trunk.id)
                    except sdkexc.SDKException:
                        LOG.exception("Failed to delete trunk %s", trunk.id)
                        continue

                    # Delete anchor port
                    if anchor_port_id:
                        try:
                            self.neutron.network.delete_port(anchor_port_id)
                            LOG.info("Deleted orphaned anchor port %s",
                                     anchor_port_id)
                        except sdkexc.SDKException:
                            LOG.warning("Failed to delete anchor port %s",
                                        anchor_port_id)

        except (sdkexc.SDKException, AttributeError):
            LOG.exception("Failed to cleanup orphaned trunks")

    def _cleanup_orphaned_networks(self):
        """Clean up ha_chassis_group networks that no longer have groups."""
        try:
            # Get all current ha_chassis_groups
            ha_groups = self._get_ha_chassis_groups()
            valid_group_names = {group.name for group in ha_groups}

            # Find all L2VNI ha_chassis_group networks
            networks = self.neutron.network.networks()
            for network in networks:
                if not network.name or not network.name.startswith(
                        'l2vni-ha-group-'):
                    continue

                # Parse network name: l2vni-ha-group-{group_name}
                parts = network.name.split('-', 3)
                if len(parts) < 4:
                    continue

                group_name = parts[3]

                # Check if this ha_chassis_group still exists
                if group_name not in valid_group_names:
                    # Check if network has any ports (besides DHCP/router)
                    ports = list(self.neutron.network.ports(
                        network_id=network.id))
                    l2vni_ports = [
                        p for p in ports
                        if p.device_owner == DEVICE_OWNER_L2VNI_ANCHOR]

                    if not l2vni_ports:
                        LOG.info("Cleaning up orphaned ha_chassis_group "
                                 "network %s for group %s",
                                 network.id, group_name)
                        try:
                            self.neutron.network.delete_network(network.id)
                            LOG.info("Deleted orphaned network %s", network.id)
                        except sdkexc.SDKException:
                            LOG.exception("Failed to delete network %s",
                                          network.id)

        except (sdkexc.SDKException, AttributeError):
            LOG.exception("Failed to cleanup orphaned networks")
