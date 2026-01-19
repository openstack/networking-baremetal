# Copyright (c) 2025 Rackspace Technology, Inc.
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

import functools
import socket
from typing import cast

from neutron.common.ovn import constants as ovn_const
from neutron.common.ovn import utils as ovn_utils
from neutron.db import provisioning_blocks
from neutron.objects import ports
from neutron_lib.api.definitions import portbindings
from neutron_lib.callbacks import resources
from neutron_lib import constants as p_const
from neutron_lib import exceptions as exc
from neutron_lib.plugins import directory
from neutron_lib.plugins.ml2 import api
from oslo_config import cfg
from oslo_log import log as logging

LOG = logging.getLogger(__name__)

baremetal_l2vni_opts = [
    cfg.BoolOpt('create_localnet_ports',
                default=True,
                help='Automatically create OVN localnet ports to bridge '
                     'VXLAN overlay networks to physical networks for '
                     'baremetal. Disable if localnet ports are managed '
                     'externally or not needed.'),
    cfg.StrOpt('default_physical_network',
               default=None,
               help='Default physical network name to use for baremetal '
                    'L2VNI bindings when the port binding profile does not '
                    'specify a physical_network. If not set and the port '
                    'lacks physical_network in its binding profile, port '
                    'binding will fail.'),
]

cfg.CONF.register_opts(baremetal_l2vni_opts, group='baremetal_l2vni')

SUPPORTED_VNIC_TYPES = [portbindings.VNIC_BAREMETAL]

# NOTE(cardoe) This is where we want to use the TYPE from
# https://review.opendev.org/c/openstack/neutron-specs/+/952166
# Supports both VXLAN and Geneve overlay protocols for EVPN L2VNI
EVPN_TYPES = [p_const.TYPE_VXLAN, p_const.TYPE_GENEVE]


def _get_port_name(ls_name, physnet):
    """Helper to ensure consistent naming of ports."""
    return f"{ls_name}-localnet-{physnet}"


class L2vniMechanismDriver(api.MechanismDriver):
    """ML2 mechanism driver for L2VNI binding

    This mechanism driver is called on port binding to facilitate the
    VTEP to VLAN binding necessary for EVPN networks to attach to
    baremetal ports, which may then connect to the environment through
    an EVPN connection, or through direct port attachments
    """

    @property
    def connectivity(self):
        return portbindings.CONNECTIVITY_L2

    def initialize(self):
        pass

    @functools.cached_property
    def _get_ovn_client(self):
        """Get OVN client from the OVN mechanism driver.

        :returns: OVN client instance or None if OVN driver not available
        """
        try:
            # Get the mechanism driver manager
            plugin = directory.get_plugin()
            if not hasattr(plugin, 'mechanism_manager'):
                LOG.warning("ML2 plugin does not have mechanism_manager")
                return None

            # Find the OVN mechanism driver
            for driver in plugin.mechanism_manager.ordered_mech_drivers:
                if hasattr(driver.obj, '_ovn_client'):
                    return driver.obj._ovn_client

            LOG.warning("OVN mechanism driver not found")
            return None
        except Exception as e:
            LOG.error("Failed to get OVN client: %s", e)
            return None

    def _get_local_chassis_name(self, ovn_client):
        """Get the local OVN chassis name.

        :param ovn_client: OVN client instance
        :returns: Chassis name string or None
        """
        try:
            # Try to get from OVN mech driver
            if hasattr(ovn_client, 'chassis'):
                return ovn_client.chassis

            # Try to get from config
            if hasattr(cfg.CONF, 'ovn') and hasattr(cfg.CONF.ovn,
                                                    'ovn_chassis_name'):
                return cfg.CONF.ovn.ovn_chassis_name

            # Fall back to hostname
            hostname = socket.gethostname()
            LOG.debug("Using hostname as chassis name: %s", hostname)
            return hostname

        except Exception as e:
            LOG.error("Failed to determine local chassis name: %s", e)
            return None

    def _get_local_chassis(self, ovn_client):
        """Get the local OVN chassis object.

        :param ovn_client: OVN client instance
        :returns: Chassis object or None
        """
        try:
            local_chassis_name = self._get_local_chassis_name(ovn_client)
            if not local_chassis_name:
                return None

            # Get chassis from OVN Southbound
            if not hasattr(ovn_client, '_sb_idl'):
                LOG.debug("No southbound connection available")
                return None

            # TODO(TheJulia): At some point soon, once we have a CI job
            # validating all of this, we should look at a different query
            # pattern. See:
            # https://review.opendev.org/c/openstack/networking-baremetal/+/973889/9/networking_baremetal/plugins/ml2/baremetal_l2vni_mapping.py

            # Query chassis from Southbound database
            for ch in ovn_client._sb_idl.tables['Chassis'].rows.values():
                if ch.name == local_chassis_name or \
                   ch.hostname == local_chassis_name:
                    return ch

            LOG.warning("Local chassis %s not found in OVN",
                        local_chassis_name)
            return None

        except Exception as e:
            LOG.error("Error getting local chassis: %s", e)
            return None

    def _chassis_can_forward_physnet(self, ovn_client, physnet):
        """Check if any chassis in the cluster can forward for this physnet.

        Checks all chassis in the OVN cluster to see if at least one has
        the physnet configured in its ovn-bridge-mappings. Since localnet
        ports are realized on all chassis with the matching bridge-mapping,
        we only need one chassis to have the physnet available.

        :param ovn_client: OVN client instance
        :param physnet: Physical network name
        :returns: True if any chassis has physnet, False otherwise
        """
        # TODO(TheJulia): We should look at simplifying this logic, see
        # https://review.opendev.org/c/openstack/networking-baremetal/+/973889/9/networking_baremetal/plugins/ml2/baremetal_l2vni_mapping.py
        try:
            if not hasattr(ovn_client, '_sb_idl'):
                LOG.warning("No southbound connection available, cannot "
                            "verify physnet %s exists", physnet)
                # Return True to allow creation - fail open rather than
                # closed
                return True

            # Check all chassis in the cluster
            for chassis in ovn_client._sb_idl.tables['Chassis'].rows.values():
                # Check bridge mappings in external_ids
                bridge_mappings = chassis.external_ids.get(
                    'ovn-bridge-mappings', '')

                # Format is "physnet1:br-provider,physnet2:br-ex"
                physnets = [mapping.split(':')[0].strip()
                            for mapping in bridge_mappings.split(',')
                            if ':' in mapping]

                if physnet in physnets:
                    LOG.debug("Found physnet %s on chassis %s with bridge "
                              "mappings: %s", physnet, chassis.name,
                              bridge_mappings)
                    return True

            # No chassis has this physnet - this is an error condition
            LOG.error("Physical network %s not found in bridge-mappings "
                      "on any chassis in the OVN cluster. Check OVN "
                      "configuration.", physnet)
            return False

        except Exception as e:
            LOG.error("Error checking chassis bridge mappings: %s. "
                      "Failing open - allowing localnet port creation.",
                      e)
            # Fail open - let the creation proceed and let OVN handle it
            return True

    def _ensure_router_gateway_chassis(self, ovn_client, network_id):
        """Ensure router ports for this network are bound to local chassis.

        Finds logical router ports (LRPs) connected to this network and
        ensures they have gateway chassis bindings. This is necessary for
        router services (L3) to be available on the network node.

        :param ovn_client: OVN client instance
        :param network_id: Neutron network UUID
        """
        if not cfg.CONF.baremetal_l2vni.create_localnet_ports:
            # If localnet port creation is disabled, skip gateway binding
            return

        try:
            chassis = self._get_local_chassis(ovn_client)
            if not chassis:
                LOG.warning("Cannot bind router gateway - local chassis "
                            "not found")
                return

            ls_name = ovn_utils.ovn_name(network_id)

            # Find all logical router ports connected to this logical switch
            if not hasattr(ovn_client, '_nb_idl'):
                LOG.debug("No northbound connection available")
                return

            # Query all logical router ports
            for lrp in ovn_client._nb_idl.tables.get(
                    'Logical_Router_Port', {}).rows.values():
                # Check if this LRP has a peer to our logical switch
                # (LRP peers are typically switch router ports)
                lrp_name = lrp.name

                # Look for corresponding switch port that peers with this LRP
                for lsp in ovn_client._nb_idl.tables.get(
                        'Logical_Switch_Port', {}).rows.values():
                    # Router ports on switches have type 'router' and
                    # options['router-port'] pointing to the LRP
                    if (hasattr(lsp, 'type')
                            and lsp.type == 'router'
                            and hasattr(lsp, 'options')
                            and lsp.options.get('router-port') == lrp_name):

                        # Check if this switch port belongs to our network
                        # by checking the logical switch
                        for ls in ovn_client._nb_idl.tables.get(
                                'Logical_Switch', {}).rows.values():
                            if (ls.name == ls_name
                                    and hasattr(ls, 'ports')
                                    and lsp.uuid in [p.uuid
                                                     for p in ls.ports]):

                                # Found a router port on our network
                                LOG.debug("Found router port %s on network %s",
                                          lrp_name, network_id)

                                # Check if it has gateway chassis set
                                if not lrp.gateway_chassis:
                                    LOG.info("Setting gateway chassis for "
                                             "router port %s to chassis %s",
                                             lrp_name, chassis.name)

                                    # Set gateway chassis using northbound API
                                    cmd = ovn_client._nb_idl.lrp_set_gateway_chassis(  # noqa: E501
                                        lrp_name,
                                        chassis.name,
                                        priority=1
                                    )
                                    ovn_client._transaction([cmd])

                                    LOG.info("Successfully set gateway "
                                             "chassis for router port %s",
                                             lrp_name)
                                else:
                                    LOG.debug("Router port %s already has "
                                              "gateway chassis configured",
                                              lrp_name)
                                break

        except Exception as e:
            LOG.error("Failed to ensure router gateway chassis for "
                      "network %s: %s", network_id, e)
            # Don't raise - this is an optimization, binding can still work

    def _ensure_localnet_port(self, context, network_id, physnet,
                              vlan_id: int):
        """Ensure a localnet port exists in OVN to bridge overlay to physnet.

        Creates a localnet port in OVN's logical switch that bridges the
        VXLAN overlay network to the physical network via the dynamic VLAN
        segment. This is idempotent - if the port already exists, it will
        not be recreated.

        :param context: PortContext instance
        :param network_id: Neutron network UUID
        :param physnet: Physical network name
        :param vlan_id: VLAN tag for the physical network (None for untagged)
        """
        if not cfg.CONF.baremetal_l2vni.create_localnet_ports:
            LOG.debug("Localnet port creation disabled by config")
            return

        ovn_client = self._get_ovn_client
        if not ovn_client:
            LOG.warning("Cannot create localnet port - OVN client unavailable")
            return

        # TODO(TheJulia): We should consider simplifying and just using
        # ovn_client on the class method directly on helper methods as
        # opposed to pasisng a variable. Refactoring for later.

        # Check if this chassis can forward traffic for the physnet
        if not self._chassis_can_forward_physnet(ovn_client, physnet):
            LOG.debug("Chassis cannot forward physnet %s, skipping "
                      "localnet port creation", physnet)
            return

        try:
            ls_name = ovn_utils.ovn_name(network_id)

            # Localnet port name includes physnet for uniqueness
            port_name = _get_port_name(ls_name, physnet)

            # Check if localnet port already exists
            existing_port = ovn_client._nb_idl.lsp_get(port_name).execute(
                check_error=False)

            if existing_port:
                LOG.debug("Localnet port %s already exists for network %s "
                          "on physnet %s", port_name, network_id, physnet)
                return

            # Create the localnet port using atomic create_lswitch_port
            LOG.info("Creating localnet port %s for network %s to bridge "
                     "to physnet %s with VLAN %s", port_name, network_id,
                     physnet, vlan_id)

            # Build options for localnet port
            options = {
                "network_name": physnet,
                ovn_const.LSP_OPTIONS_LOCALNET_LEARN_FDB: 'true',
                ovn_const.LSP_OPTIONS_MCAST_FLOOD: 'true',
                ovn_const.LSP_OPTIONS_MCAST_FLOOD_REPORTS: 'true',
            }

            # Create localnet port atomically
            cmd = ovn_client._nb_idl.create_lswitch_port(
                lport_name=port_name,
                lswitch_name=ls_name,
                addresses=[ovn_const.UNKNOWN_ADDR],
                external_ids={},
                type=ovn_const.LSP_TYPE_LOCALNET,
                tag=vlan_id if vlan_id else [],
                options=options
            )

            # Execute the transaction
            ovn_client._transaction([cmd])

            LOG.info("Successfully created localnet port %s with VLAN tag %s",
                     port_name, vlan_id)

            # Ensure router gateway ports are bound to this chassis
            self._ensure_router_gateway_chassis(ovn_client, network_id)

        except Exception as e:
            LOG.error("Failed to create localnet port for network %s "
                      "on physnet %s: %s", network_id, physnet, e)
            # Don't raise - this is an optimization, binding can still work

    def _remove_localnet_port(self, context, network_id, physnet):
        """Remove localnet port from OVN when dynamic segment is released.

        Cleans up the localnet port that bridges the VXLAN overlay to the
        physical network when the dynamic VLAN segment is no longer needed.

        :param context: PortContext instance
        :param network_id: Neutron network UUID
        :param physnet: Physical network name
        """
        if not cfg.CONF.baremetal_l2vni.create_localnet_ports:
            return

        ovn_client = self._get_ovn_client
        if not ovn_client:
            LOG.debug("Cannot remove localnet port - OVN client unavailable")
            return

        try:
            ls_name = ovn_utils.ovn_name(network_id)

            # Localnet port name includes physnet for uniqueness
            port_name = _get_port_name(ls_name, physnet)

            # Check if localnet port exists
            existing_port = ovn_client._nb_idl.lsp_get(port_name).execute(
                check_error=False)

            if not existing_port:
                LOG.debug("Localnet port %s does not exist, nothing to "
                          "remove", port_name)
                return

            # Remove the localnet port
            LOG.info("Removing localnet port %s for network %s on physnet %s "
                     "as dynamic segment is being released",
                     port_name, network_id, physnet)

            ovn_client._nb_idl.lsp_del(port_name).execute(check_error=True)

            LOG.info("Successfully removed localnet port %s", port_name)

        except Exception as e:
            LOG.error("Failed to remove localnet port for network %s "
                      "on physnet %s: %s", network_id, physnet, e)
            # Don't raise - segment cleanup should continue

    def update_port_postcommit(self, context):
        vnic_type = context.current[portbindings.VNIC_TYPE]
        if vnic_type not in SUPPORTED_VNIC_TYPES:
            return

        vif_type = context.current[portbindings.VIF_TYPE]

        if vif_type == portbindings.VIF_TYPE_UNBOUND:
            # The lowest bound segment should be our dynamic segment
            segment = context.original_bottom_bound_segment
            if segment and segment[api.NETWORK_TYPE] == p_const.TYPE_VLAN:
                # If no host is bound to this segment now, release it
                if not ports.PortBindingLevel.get_objects(
                        context.plugin_context, segment_id=segment[api.ID]
                ):
                    # Clean up the localnet port before releasing the segment
                    physnet = segment.get(api.PHYSICAL_NETWORK)
                    if physnet:
                        self._remove_localnet_port(
                            context,
                            context.network.current['id'],
                            physnet
                        )
                    context.release_dynamic_segment(segment[api.ID])

        if vif_type == portbindings.VIF_TYPE_OTHER:
            # Complete OVN's L2 provisioning block for baremetal
            # This is really a workaround for odd OVN behavior which
            # could be a misconfiguration, we're not 100% sure yet.
            # Without it, the port never moves to ACTIVE, but realistically
            # the binding is still incomplete and the port doesn't entirely
            # work yet on the controller side because the created port is
            # declared shutdown which triggers the config reconcile which
            # creates this entry.
            provisioning_blocks.provisioning_complete(
                context._plugin_context, context.current['id'],
                resources.PORT, 'L2')

    def bind_port(self, context):
        if context.current[portbindings.VNIC_TYPE] not in SUPPORTED_VNIC_TYPES:
            return

        # This mechanism wants to trigger extra steps against networks
        # for which this plugin helps provide mappings and attachments for.
        for segment in context.network.network_segments:
            if segment[api.NETWORK_TYPE] in EVPN_TYPES:
                self._bind_port_segment(context, segment)
                # Fast out to avoid walking the rest of the list
                break

    def _bind_port_segment(self, context, bind_segment):
        """Dynamically allocates a VLAN segment to bind the segment to."""
        # This will only be set by
        # https://review.opendev.org/c/openstack/ironic/+/964570
        # Get physical network from port binding profile, fallback to config
        physnet = context.current[portbindings.PROFILE].get(
            api.PHYSICAL_NETWORK)

        if not physnet:
            # Fallback to configured default physical network
            physnet = cfg.CONF.baremetal_l2vni.default_physical_network
            if physnet:
                LOG.debug("Port %s does not specify physical_network in "
                          "binding profile, using default: %s",
                          context.current['id'], physnet)

        if not physnet:
            # No physnet from profile or config - cannot bind
            LOG.error("Port %s cannot be bound: no physical_network "
                      "specified in binding profile and no default "
                      "physical network configured. Set "
                      "[baremetal_l2vni]default_physical_network or ensure "
                      "ports have physical_network in binding profile.",
                      context.current['id'])
            raise exc.InvalidInput(
                error_message="Port binding requires physical_network in "
                              "binding profile or default_physical_network "
                              "configuration.")

        lower_segment = None
        for segment in context.network.network_segments:
            if (segment[api.NETWORK_TYPE] == p_const.TYPE_VLAN
                    and segment[api.PHYSICAL_NETWORK] == physnet):
                lower_segment = segment
                break
        if lower_segment:
            # NOTE(TheJulia): This may be overkill logging wise, but it makes
            # it pretty clear logging wise.
            LOG.debug("A lower segment (%s) is already exists in physical "
                      "network %s to attach to segmentation id %s.",
                      lower_segment.get(api.SEGMENTATION_ID),
                      physnet,
                      bind_segment.get(api.SEGMENTATION_ID))
            if context.is_partial_segment(lower_segment):
                LOG.error("Lower segment in physical network %s is lacking a "
                          "segmentation ID.", physnet)
                raise exc.InvalidInput(
                    error_message="Lower segment is lacking a "
                                  "segmentation id.")
        else:
            # If we do not have a lower segment, we need to allocate it.
            lower_segment = context.allocate_dynamic_segment(
                {
                    api.PHYSICAL_NETWORK: physnet,
                    api.NETWORK_TYPE: p_const.TYPE_VLAN,
                }
            )
            if not lower_segment:
                LOG.error("Failed to allocate dynamic VLAN segment for "
                          "physical network %s on port %s",
                          physnet, context.current['id'])
                raise exc.InvalidInput(
                    error_message=f"Failed to allocate dynamic VLAN segment "
                                  f"for physical network {physnet}")
            LOG.debug("A lower_segment was not found to bind segmentation id "
                      "%s to physical network %s. Allocated: %s",
                      bind_segment.get(api.SEGMENTATION_ID),
                      physnet,
                      lower_segment.get(api.SEGMENTATION_ID))

        # Validate lower segment has a segmentation ID before proceeding
        vlan_id = lower_segment.get(api.SEGMENTATION_ID)
        if not vlan_id:
            LOG.error("Lower segment for physical network %s is missing "
                      "segmentation ID on port %s",
                      physnet, context.current['id'])
            raise exc.InvalidInput(
                error_message=f"Lower segment on physical network {physnet} "
                              f"is missing segmentation ID")

        # Ensure OVN has a localnet port to bridge the overlay to the physnet
        vlan_id = cast(int, vlan_id)
        self._ensure_localnet_port(
            context,
            context.network.current['id'],
            physnet,
            vlan_id
        )

        # record the current EVPN segment as bound and move on to binding
        # the VLAN segment
        context.continue_binding(bind_segment[api.ID], [lower_segment])
