# Copyright 2017 Cisco Systems, Inc.
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

import os
import secrets
import socket
import sys
import threading
from urllib import parse as urlparse

from neutron.agent import rpc as agent_rpc
from neutron.common import config as common_config
from neutron.common.ovn import utils as ovn_utils
from neutron.conf.agent import common as neutron_agent_config
try:
    from neutron.conf.plugins.ml2.drivers.ovn import ovn_conf
except ImportError:
    ovn_conf = None
from neutron_lib.agent import topics
from neutron_lib import constants as n_const
from neutron_lib import context
from openstack import exceptions as sdk_exc
from oslo_config import cfg
from oslo_log import log as logging
import oslo_messaging
from oslo_service import loopingcall
from oslo_service import service
from oslo_utils import timeutils
from oslo_utils import uuidutils
from ovsdbapp.backend.ovs_idl import idlutils
from ovsdbapp import exceptions as ovs_exc
from tooz import hashring

from networking_baremetal.agent import agent_config
from networking_baremetal.agent import l2vni_trunk_manager
from networking_baremetal.agent import ovn_client
from networking_baremetal import constants
from networking_baremetal import ironic_client
from networking_baremetal import neutron_client

CONF = cfg.CONF
LOG = logging.getLogger(__name__)
CONF.import_group('AGENT', 'neutron.plugins.ml2.drivers.agent.config')


def list_opts():
    return [
        ('agent', neutron_agent_config.AGENT_STATE_OPTS),
    ] + agent_config.list_opts() + neutron_client.list_opts()


def _get_notification_transport_url():
    url = urlparse.urlparse(CONF.transport_url)
    if (CONF.oslo_messaging_rabbit.amqp_auto_delete is False
            and not getattr(CONF.oslo_messaging_rabbit, 'rabbit_quorum_queue',
                            None)):
        q = urlparse.parse_qs(url.query)
        q.update({'amqp_auto_delete': ['true']})
        query = urlparse.urlencode({k: v[0] for k, v in q.items()})
        url = url._replace(query=query)
    return urlparse.urlunparse(url)


def _set_up_notifier(transport, uuid):
    return oslo_messaging.Notifier(
        transport,
        publisher_id='ironic-neutron-agent-' + uuid,
        driver='messagingv2',
        topics=['ironic-neutron-agent-member-manager'])


def _set_up_listener(transport, agent_id):
    targets = [
        oslo_messaging.Target(topic='ironic-neutron-agent-member-manager')]
    endpoints = [HashRingMemberManagerNotificationEndpoint()]
    return oslo_messaging.get_notification_listener(
        transport, targets, endpoints, pool=agent_id)


class HashRingMemberManagerNotificationEndpoint(object):
    """Class variables members and hashring is shared by all instances"""

    filter_rule = oslo_messaging.NotificationFilter(
        publisher_id='^ironic-neutron-agent.*')

    members = []
    hashring = hashring.HashRing([])

    def info(self, ctxt, publisher_id, event_type, payload, metadata):

        timestamp = timeutils.utcnow_ts()
        # Add members or update timestamp for existing members
        if payload['id'] not in [x['id'] for x in self.members]:
            try:
                LOG.info('Adding member id %s on host %s to hashring.',
                         payload['id'], payload['host'])
                self.hashring.add_node(payload['id'])
                self.members.append(payload)
            except Exception:
                LOG.exception('Failed to add member %s to hash ring!',
                              payload['id'])
        else:
            for member in self.members:
                if payload['id'] == member['id']:
                    member['timestamp'] = payload['timestamp']

        # Remove members that have not checked in for a while
        for member in self.members:
            if (timestamp - member['timestamp']) > (
                    CONF.AGENT.report_interval * 3):
                try:
                    LOG.info('Removing member %s on host %s from hashring.',
                             member['id'], member['host'])
                    self.hashring.remove_node(member['id'])
                    self.members.remove(member)
                except Exception:
                    LOG.exception('Failed to remove member %s from hash ring!',
                                  member['id'])

        return oslo_messaging.NotificationResult.HANDLED


class BaremetalNeutronAgent(service.ServiceBase):

    def __init__(self):
        self.context = context.get_admin_context_without_session()
        self.agent_id = uuidutils.generate_uuid(dashed=True)
        LOG.info('Agent ID generated: %s', self.agent_id)
        self.agent_host = socket.gethostname()
        self.heartbeat = None
        self.notify_agents = None

        # Set up oslo_messaging notifier and listener to keep track of other
        # members
        # NOTE(hjensas): Override the control_exchange for the notification
        # transport to allow setting amqp_auto_delete = true.
        # TODO(hjensas): Remove this and override the exchange when setting up
        # the notifier once the fix for bug is available.
        #   https://bugs.launchpad.net/oslo.messaging/+bug/1814797
        CONF.set_override('control_exchange', 'ironic-neutron-agent')
        self.transport = oslo_messaging.get_notification_transport(
            CONF, url=_get_notification_transport_url())
        self.notifier = _set_up_notifier(self.transport, self.agent_id)
        # Note(hjensas): We need to have listener consuming the non-pool queue.
        # See bug: https://bugs.launchpad.net/oslo.messaging/+bug/1814544
        self.listener = _set_up_listener(self.transport, None)
        self.pool_listener = _set_up_listener(self.transport, '-'.join(
            ['ironic-neutron-agent-member-manager-pool', self.agent_id]))

        self.member_manager = HashRingMemberManagerNotificationEndpoint()

        self.state_rpc = agent_rpc.PluginReportStateAPI(topics.REPORTS)
        self.ironic_client = ironic_client.get_client()
        self.reported_nodes = {}

        # L2VNI trunk reconciliation (optional feature)
        self.trunk_manager = None
        self.l2vni_reconcile = None
        self._l2vni_reconciliation_lock = threading.Lock()

        # Initialize trunk manager if either periodic or event-driven
        # reconciliation is enabled
        if (CONF.l2vni.enable_l2vni_trunk_reconciliation
                or CONF.l2vni.enable_l2vni_trunk_reconciliation_events):
            if CONF.l2vni.enable_l2vni_trunk_reconciliation:
                LOG.info('L2VNI trunk reconciliation enabled, initializing...')
            if CONF.l2vni.enable_l2vni_trunk_reconciliation_events:
                LOG.info('Event-driven L2VNI trunk reconciliation enabled')

            neutron = self._get_neutron_client()

            # Try to connect to OVN, but allow startup if OVN is unavailable
            # The reconciliation loop will retry connecting
            ovn_nb_idl = None
            ovn_sb_idl = None
            try:
                ovn_nb_idl = ovn_client.get_ovn_nb_idl()
                ovn_sb_idl = ovn_client.get_ovn_sb_idl()
                LOG.info('Successfully connected to OVN databases')
            except Exception:
                LOG.warning(
                    'Failed to connect to OVN databases during startup. '
                    'This is expected if OVN is restarting or not yet '
                    'available. The agent will retry connecting during '
                    'reconciliation cycles.', exc_info=True)

            self.trunk_manager = (
                l2vni_trunk_manager.L2VNITrunkManager(
                    neutron_client=neutron,
                    ovn_nb_idl=ovn_nb_idl,
                    ovn_sb_idl=ovn_sb_idl,
                    ironic_client=self.ironic_client,
                    member_manager=self.member_manager,
                    agent_id=self.agent_id
                ))
            LOG.info('L2VNI trunk manager initialized')

            # Register OVN event handlers for L2VNI reconciliation
            if (CONF.l2vni.enable_l2vni_trunk_reconciliation_events
                    and self.trunk_manager.ovn_nb_idl):
                from networking_baremetal.agent import ovn_events

                # Use dedicated event-only connection for event watching
                # This connection has selective table registration to minimize
                # event notification overhead
                try:
                    ovn_nb_event_idl = ovn_client.get_ovn_nb_event_idl()
                    self._localnet_event = ovn_events.LocalnetPortEvent(self)
                    LOG.info('Created LocalnetPortEvent with agent_id: %s',
                             self._localnet_event.agent_id)
                    ovn_nb_event_idl.idl.notify_handler.watch_event(
                        self._localnet_event)
                    LOG.info('Registered OVN event handler for L2VNI localnet '
                             'port changes (CREATE/DELETE) using dedicated '
                             'event-only connection')
                except Exception:
                    LOG.exception(
                        'Failed to create OVN event-only connection, '
                        'OVN event-driven reconciliation disabled. Using '
                        'periodic reconciliation only.')
            elif CONF.l2vni.enable_l2vni_trunk_reconciliation_events:
                LOG.error('OVN connection not available, event-driven L2VNI '
                          'trunk reconciliation disabled. Using periodic '
                          'reconciliation only. The agent will retry OVN '
                          'connection on subsequent reconciliation cycles.')

        # HA chassis group alignment reconciliation (optional feature)
        self.ha_alignment_reconcile = None
        self._ha_alignment_lock = threading.Lock()
        if CONF.baremetal_agent.enable_ha_chassis_group_alignment:
            LOG.info('HA chassis group alignment reconciliation enabled')

        LOG.info('Agent networking-baremetal initialized.')

    def start(self):
        LOG.info('Starting agent networking-baremetal.')
        cfg.CONF.log_opt_values(LOG, logging.INFO)
        self.pool_listener.start()
        self.listener.start()
        self.notify_agents = loopingcall.FixedIntervalLoopingCall(
            self._notify_peer_agents)
        self.notify_agents.start(interval=(CONF.AGENT.report_interval / 3))
        self.heartbeat = loopingcall.FixedIntervalLoopingCall(
            self._report_state)
        self.heartbeat.start(interval=CONF.AGENT.report_interval,
                             initial_delay=CONF.AGENT.report_interval)
        self.cleanup_stale_agents()

        # Start L2VNI trunk reconciliation loop if periodic reconciliation
        # is enabled (event-driven reconciliation works without the loop)
        if self.trunk_manager and CONF.l2vni.enable_l2vni_trunk_reconciliation:
            # Add random jitter to prevent thundering herd on restart
            # First run happens after jitter only, subsequent runs at interval
            jitter = secrets.randbelow(
                CONF.l2vni.l2vni_startup_jitter_max + 1)

            self.l2vni_reconcile = loopingcall.FixedIntervalLoopingCall(
                self._reconcile_l2vni_trunks)
            self.l2vni_reconcile.start(
                interval=CONF.l2vni.l2vni_reconciliation_interval,
                initial_delay=jitter)
            LOG.info('Started L2VNI trunk reconciliation loop '
                     '(interval: %ds, first run in %ds)',
                     CONF.l2vni.l2vni_reconciliation_interval, jitter)

        # Start HA chassis group alignment reconciliation if enabled
        if CONF.baremetal_agent.enable_ha_chassis_group_alignment:
            # Add random jitter to prevent thundering herd on restart
            # NOTE: Using pseudo-random is acceptable for jitter (S311)
            jitter = secrets.randbelow(
                CONF.baremetal_agent.ha_chassis_group_alignment_interval + 1)

            self.ha_alignment_reconcile = loopingcall.FixedIntervalLoopingCall(
                self._reconcile_ha_chassis_group_alignment)
            self.ha_alignment_reconcile.start(
                interval=CONF.baremetal_agent
                .ha_chassis_group_alignment_interval,
                initial_delay=jitter)
            LOG.info('Started HA chassis group alignment reconciliation loop '
                     '(interval: %ds, first run in %ds)',
                     CONF.baremetal_agent
                     .ha_chassis_group_alignment_interval, jitter)

    def stop(self, failure=False):
        LOG.info('Stopping agent networking-baremetal.')
        if self.heartbeat:
            self.heartbeat.stop()
        if self.notify_agents:
            self.notify_agents.stop()
        if self.l2vni_reconcile:
            self.l2vni_reconcile.stop()
            LOG.info('Stopped L2VNI trunk reconciliation loop')
        if self.ha_alignment_reconcile:
            self.ha_alignment_reconcile.stop()
            LOG.info('Stopped HA chassis group alignment reconciliation loop')
        self.listener.stop()
        self.pool_listener.stop()
        self.listener.wait()
        self.pool_listener.wait()
        if failure:
            # This will generate a SIGABORT for the process which forces it
            # to exit, which seems cleaner to force the process to exit
            # than os.exit and avoids threading constraints.
            os.abort()

    def reset(self):
        LOG.info('Resetting agent networking-baremetal.')
        if self.heartbeat:
            self.heartbeat.stop()
        if self.notify_agents:
            self.notify_agents.stop()
        self.listener.stop()
        self.pool_listener.stop()
        self.listener.wait()
        self.pool_listener.wait()

    def wait(self):
        pass

    def _notify_peer_agents(self):
        try:
            self.notifier.info({
                'ironic-neutron-agent': 'heartbeat'},
                'ironic-neutron-agent-member-manager',
                {'id': self.agent_id,
                 'host': self.agent_host,
                 'timestamp': timeutils.utcnow_ts()})
        except Exception:
            LOG.exception('Failed to send hash ring membership heartbeat!')

    def get_template_node_state(self, node_uuid):
        return {
            'binary': constants.BAREMETAL_BINARY,
            'host': node_uuid,
            'topic': n_const.L2_AGENT_TOPIC,
            'configurations': {
                'bridge_mappings': {},
                'log_agent_heartbeats': CONF.AGENT.log_agent_heartbeats,
            },
            'start_flag': False,
            'agent_type': constants.BAREMETAL_AGENT_TYPE,
            'action': 'update'}

    def _report_state(self):
        node_states = {}
        conductor_groups_config = getattr(CONF, 'conductor_groups', None)
        conductor_groups = getattr(
            conductor_groups_config, 'conductor_groups', None) or []

        if conductor_groups:
            LOG.info("Using conductor groups filter: %s", conductor_groups)

        ironic_ports = self.ironic_client.ports(
            details=True, conductor_groups=conductor_groups)

        # NOTE: the above calls returns a generator, so we need to handle
        # exceptions that happen just before the first loop iteration, when
        # the actual request to ironic happens
        try:
            for port in ironic_ports:
                node = port.node_id
                if (self.agent_id not in
                        self.member_manager.hashring[node.encode('utf-8')]):
                    continue
                template_node_state = self.get_template_node_state(node)
                node_states.setdefault(node, template_node_state)
                mapping = node_states[
                    node]["configurations"]["bridge_mappings"]
                if port.physical_network is not None:
                    mapping[port.physical_network] = "yes"
        except sdk_exc.OpenStackCloudException:
            LOG.exception("Failed to get ironic ports data! "
                          "Not reporting state.")
            try:
                # Replace the client, just to be on the safe side in
                # the event there was some sort of hard/breaking failure.
                self.ironic_client = ironic_client.get_client()
            except Exception:
                # Failed to re-launch a new client, aborting.
                self.stop(failure=True)
            return
        abort_operation = False
        for state in node_states.values():
            # If the node was not previously reported with current
            # configuration set the start_flag True.
            # NOTE(TheJulia) reported_nodes is an internal list of nodes
            # we *have* updated.
            if not state['configurations'] == self.reported_nodes.get(
                    state['host']):
                state.update({'start_flag': True})
                LOG.info('Reporting state for host agent %s with new '
                         'configuration: %s',
                         state['host'], state['configurations'])
            try:
                LOG.debug('Reporting state for host: %s with configuration: '
                          '%s', state['host'], state['configurations'])
                self.state_rpc.report_state(self.context, state)
            except AttributeError:
                # This means the server does not support report_state
                LOG.exception("Neutron server does not support state report. "
                              "State report for this agent will be disabled.")
                # Don't continue reporting the remaining agents in this case.
                abort_operation = True
                break
            except Exception:
                LOG.exception("Failed reporting state!")
                # Don't continue reporting the remaining nodes if one failed.
                return
            self.reported_nodes.update(
                {state['host']: state['configurations']})

        # Identify nodes that are no longer present in Ironic by subtracting
        # the keys of `node_states` from the keys of `reported_nodes`. Then
        # delete agents for nodes that are no longer present.
        deleted_nodes = self.reported_nodes.keys() - node_states.keys()
        deleted_agents = self._delete_agents(deleted_nodes)
        for node in deleted_agents:
            self.reported_nodes.pop(node)

        if abort_operation:
            # We don't expect the agent to work, and as such we should call
            # stop so the program unwinds and begins to exit.
            self.stop(failure=True)

    def _get_down_agents(self):
        """Retrieves a list of inactive Baremetal agents.

        Fetch a list of inactive Baremetal agents. It interacts with
        the state_rpc object to call the 'get_agents' method, which
        retrieves agents based on the provided parameters.

        :returns: (list) Inactive Baremetal agents.
        """
        down_bm_agents = []
        try:
            down_bm_agents = self.state_rpc.get_agents(
                self.context,
                agent_type=constants.BAREMETAL_AGENT_TYPE,
                is_active=False)
        except oslo_messaging.NoSuchMethod:
            LOG.warning("Neutron server doesn't support "
                        "`get_agents` endpoint.")

        return down_bm_agents

    def _get_nodes_not_found(self, down_bm_agents):
        """Identifies nodes that are not found in the Ironic

        The method iterates over each agent in the 'down_bm_agents' list.
        For each agent, it attempts to retrieve the corresponding node using
        the Ironic client's 'get_node' method. If the node is not found the
        node is appended to the 'nodes_not_found' list.

        :param down_bm_agents: (list) Agents that are down in Neutron.
        :return: (list) Nodes that are not found in Ironic.
        """
        nodes_not_found = []
        for agent in down_bm_agents:
            node = agent['host']
            try:
                self.ironic_client.get_node(node)
            except sdk_exc.NotFoundException:
                nodes_not_found.append(node)

        return nodes_not_found

    def _delete_agents(self, nodes, log=True):
        """Delete agents for nodes that are not found in ironic

        Clean up agent records in neutron for ironic nodes that have been
        removed from the system.

        :param nodes_not_found: (list) Nodes that are not found in Ironic.
        :log: (bool) Log the actions taken.
        :return: (list) Agents that have been deleted in Neutron.
        """
        deleted_agents = []
        for node in nodes:
            if log:
                LOG.info('Removing agent for host: %s', node)
            try:
                kwargs = {'host': node,
                          'agent_type': constants.BAREMETAL_AGENT_TYPE}
                self.state_rpc.delete_agent(self.context, **kwargs)
                deleted_agents.append(node)
            except oslo_messaging.NoSuchMethod:
                LOG.warning("Neutron server doesn't support "
                            "`delete_agent` endpoint.")
                break

        return deleted_agents

    def cleanup_stale_agents(self):
        """Cleans up stale baremetal agents

        This method identifies baremetal agents that are marked as
        inactive in the Neutron server and are not associated with
        any nodes in Ironic. It then deletes these stale agents.
        """
        down_bm_agents = self._get_down_agents()
        nodes_not_found = self._get_nodes_not_found(down_bm_agents)
        deleted_agents = self._delete_agents(nodes_not_found, log=False)

        if deleted_agents:
            LOG.info("Stale baremetal agent for hosts was removed: %s",
                     ", ".join(deleted_agents))

    def _get_neutron_client(self):
        """Get Neutron client using OpenStack SDK.

        Uses Neutron-specific credentials from [neutron] section if configured,
        otherwise falls back to [ironic] section credentials for backwards
        compatibility.

        :returns: OpenStack SDK Connection object for accessing network APIs
        """
        return neutron_client.get_client()

    def _reconcile_single_vlan_blocking(
            self, network_id, physnet, vlan_id, action):
        """Targeted reconciliation for a single VLAN (blocking lock).

        Called by OVN event handlers. Uses blocking lock acquisition to ensure
        the event is processed (unlike periodic reconciliation which skips if
        locked).

        :param network_id: Neutron network UUID
        :param physnet: Physical network name
        :param vlan_id: VLAN ID to add or remove
        :param action: 'add' or 'remove'
        """
        LOG.debug("Acquiring lock for targeted VLAN reconciliation...")
        with self._l2vni_reconciliation_lock:
            LOG.debug("Lock acquired, processing targeted reconciliation for "
                      "VLAN %d on physnet %s", vlan_id, physnet)
            try:
                self.trunk_manager.reconcile_single_vlan(
                    network_id, physnet, vlan_id, action)
            except Exception:
                LOG.exception("Failed targeted reconciliation for VLAN %d",
                              vlan_id)

    def _reconcile_l2vni_trunks(self):
        """Periodic L2VNI trunk reconciliation"""
        if not self._l2vni_reconciliation_lock.acquire(blocking=False):
            LOG.debug("L2VNI reconciliation already in progress, skipping")
            return

        try:
            LOG.debug("L2VNI reconciliation triggered.")

            # Retry OVN connection if not established
            if (self.trunk_manager.ovn_nb_idl is None
                    or self.trunk_manager.ovn_sb_idl is None):
                LOG.debug("OVN connection not established, attempting to "
                          "connect...")
                try:
                    if self.trunk_manager.ovn_nb_idl is None:
                        self.trunk_manager.ovn_nb_idl = (
                            ovn_client.get_ovn_nb_idl())
                        LOG.info("Successfully connected to OVN Northbound "
                                 "database")
                    if self.trunk_manager.ovn_sb_idl is None:
                        self.trunk_manager.ovn_sb_idl = (
                            ovn_client.get_ovn_sb_idl())
                        LOG.info("Successfully connected to OVN Southbound "
                                 "database")
                except Exception:
                    LOG.info("OVN databases not available, skipping L2VNI "
                             "reconciliation cycle. Will retry on next cycle.")
                    return

            self.trunk_manager.reconcile()
            LOG.debug("L2VNI trunk reconciliation completed.")

        except Exception:
            LOG.exception("Failed to reconcile L2VNI trunks")
        finally:
            self._l2vni_reconciliation_lock.release()

    def _reconcile_ha_chassis_group_alignment(self):
        """Periodic HA chassis group alignment reconciliation.

        This reconciliation ensures that router ports on networks with
        baremetal external ports use the same ha_chassis_group as those
        baremetal ports. This fixes LP#1995078 where mismatched priorities
        cause intermittent connectivity issues.
        """
        if not self._ha_alignment_lock.acquire(blocking=False):
            LOG.debug("HA alignment reconciliation already in progress, "
                      "skipping")
            return

        try:
            LOG.debug("HA chassis group alignment reconciliation triggered.")

            neutron = self._get_neutron_client()

            # Get OVN connection (reuse from trunk manager if available,
            # otherwise create new connection)
            ovn_nb_idl = None
            if self.trunk_manager and self.trunk_manager.ovn_nb_idl:
                ovn_nb_idl = self.trunk_manager.ovn_nb_idl
            else:
                try:
                    ovn_nb_idl = ovn_client.get_ovn_nb_idl()
                except (ovs_exc.OvsdbAppException, RuntimeError):
                    LOG.warning("Failed to connect to OVN Northbound "
                                "database, skipping reconciliation cycle. "
                                "Will retry on next cycle.", exc_info=True)
                    return

            # Determine time window for filtering recent resources
            cutoff_time = None
            if (CONF.baremetal_agent
                    .limit_ha_chassis_group_alignment_to_recent_changes_only):
                window = CONF.baremetal_agent.ha_chassis_group_alignment_window
                if window > 0:
                    cutoff_time = timeutils.utcnow_ts() - window
                    LOG.debug("Filtering to resources updated after %s "
                              "(window: %ds)", cutoff_time, window)

            # Get all baremetal external ports from Neutron
            # device_owner='baremetal:none' indicates external baremetal ports
            filters = {'device_owner': constants.BAREMETAL_NONE}
            bm_ports = list(neutron.network.ports(**filters))
            LOG.debug("Found %d baremetal external ports", len(bm_ports))

            if not bm_ports:
                LOG.debug("No baremetal external ports found, nothing to do")
                return

            # Group ports by network
            networks_with_bm_ports = {}
            for port in bm_ports:
                network_id = port.network_id

                # Apply time window filtering if enabled
                if cutoff_time is not None:
                    port_updated = timeutils.parse_isotime(
                        port.updated_at).timestamp()
                    if port_updated < cutoff_time:
                        LOG.debug("Skipping port %s (updated %s, before "
                                  "cutoff %s)", port.id, port.updated_at,
                                  cutoff_time)
                        continue

                # Check if this agent should handle this network via hash ring
                # Use network_id as the key for consistent hashing
                network_key = network_id.encode('utf-8')
                if self.agent_id not in self.member_manager.hashring[
                        network_key]:
                    LOG.debug("Network %s not managed by this agent "
                              "(hash ring)", network_id)
                    continue

                if network_id not in networks_with_bm_ports:
                    networks_with_bm_ports[network_id] = []
                networks_with_bm_ports[network_id].append(port)

            LOG.debug("Processing %d networks with baremetal ports managed "
                      "by this agent", len(networks_with_bm_ports))

            # Process each network
            for network_id, ports in networks_with_bm_ports.items():
                try:
                    self._align_ha_chassis_group_for_network(
                        network_id, ports, neutron, ovn_nb_idl)
                except (ovs_exc.OvsdbAppException,
                        sdk_exc.OpenStackCloudException, RuntimeError):
                    LOG.exception("Failed to align HA chassis group for "
                                  "network %s", network_id)

            LOG.debug("HA chassis group alignment reconciliation completed.")

        except (sdk_exc.OpenStackCloudException, ovs_exc.OvsdbAppException,
                ValueError, AttributeError):
            LOG.exception("Failed to reconcile HA chassis group alignment")
        finally:
            self._ha_alignment_lock.release()

    def _align_ha_chassis_group_for_network(self, network_id, bm_ports,
                                            neutron, ovn_nb_idl):
        """Align HA chassis groups for a specific network.

        :param network_id: Neutron network UUID
        :param bm_ports: List of baremetal external ports on this network
        :param neutron: Neutron client
        :param ovn_nb_idl: OVN Northbound IDL connection
        """
        LOG.debug("Aligning HA chassis group for network %s with %d "
                  "baremetal ports", network_id, len(bm_ports))

        # Find the HA chassis group used by baremetal ports via OVN
        # All baremetal ports on the same network should use the same
        # HA chassis group
        bm_ha_chassis_group = None
        found_any_lsp = False
        for port in bm_ports:
            try:
                lsp = ovn_nb_idl.lsp_get(
                    ovn_utils.ovn_name(port.id)).execute(check_error=True)
            except idlutils.RowNotFound:
                LOG.debug("Baremetal port %s not found in OVN (may not be "
                          "bound to OVN driver), skipping", port.id)
                continue
            except (ovs_exc.OvsdbAppException, RuntimeError, AttributeError):
                LOG.debug("Could not get HA chassis group from port %s",
                          port.id, exc_info=True)
                continue

            found_any_lsp = True
            if hasattr(lsp, 'ha_chassis_group'):
                ha_group = lsp.ha_chassis_group
                if ha_group:
                    bm_ha_chassis_group = ha_group[0] if isinstance(
                        ha_group, list) else ha_group
                    LOG.debug("Found HA chassis group %s from port %s",
                              bm_ha_chassis_group, port.id)
                    break

        if not found_any_lsp:
            LOG.debug("Could not find any baremetal ports in OVN for "
                      "network %s, skipping HA chassis group alignment",
                      network_id)
            return

        if not bm_ha_chassis_group:
            LOG.debug("Baremetal ports on network %s have no HA chassis "
                      "group set, nothing to align router ports to",
                      network_id)
            return

        LOG.debug("Target HA chassis group for network %s: %s",
                  network_id, bm_ha_chassis_group)

        # Find all router ports on this network
        router_ports = list(neutron.network.ports(
            network_id=network_id,
            device_owner=n_const.DEVICE_OWNER_ROUTER_INTF))

        if not router_ports:
            LOG.debug("No router ports found on network %s", network_id)
            return

        LOG.debug("Found %d router ports on network %s",
                  len(router_ports), network_id)

        # Check and update each router port's HA chassis group
        for rport in router_ports:
            try:
                lrp_name = ovn_utils.ovn_lrouter_port_name(rport.id)
                lrp = ovn_nb_idl.lrp_get(lrp_name).execute(check_error=True)
            except idlutils.RowNotFound:
                LOG.debug("Logical router port %s not found in OVN",
                          lrp_name)
                continue
            except (ovs_exc.OvsdbAppException, RuntimeError, AttributeError):
                LOG.debug("Could not get router port %s",
                          lrp_name, exc_info=True)
                continue

            try:
                current_ha_group = None
                if hasattr(lrp, 'ha_chassis_group'):
                    ha_group = lrp.ha_chassis_group
                    if ha_group:
                        current_ha_group = ha_group[0] if isinstance(
                            ha_group, list) else ha_group

                if current_ha_group == bm_ha_chassis_group:
                    LOG.debug("Router port %s already has correct HA "
                              "chassis group %s", rport.id,
                              bm_ha_chassis_group)
                    continue

                # Update the router port's HA chassis group
                LOG.info("Updating router port %s HA chassis group from "
                         "%s to %s (network %s)",
                         rport.id, current_ha_group, bm_ha_chassis_group,
                         network_id)

                ovn_nb_idl.lrp_set_ha_chassis_group(
                    lrp_name, bm_ha_chassis_group).execute(check_error=True)

                LOG.info("Successfully updated router port %s HA chassis "
                         "group", rport.id)

            except (ovs_exc.OvsdbAppException, RuntimeError, AttributeError):
                LOG.exception("Failed to update HA chassis group for "
                              "router port %s", rport.id)


def _unregiser_deprecated_opts():
    CONF.reset()
    CONF.unregister_opts(
        [CONF._groups[ironic_client.IRONIC_GROUP]._opts[opt]['opt']
         for opt in ironic_client._deprecated_opts],
        group=ironic_client.IRONIC_GROUP)


def main():
    common_config.register_common_config_options()
    # Register agent configuration options (L2VNI and baremetal agent)
    agent_config.register_agent_opts(CONF)
    # Register Neutron client configuration options
    neutron_client.get_session(neutron_client.NEUTRON_GROUP)

    # Register Neutron OVN options so we can read [ovn] section as fallback
    # for L2VNI OVN connection settings
    if ovn_conf is not None:
        try:
            ovn_conf.register_opts()
        except Exception as e:
            # If neutron OVN config can't be registered, L2VNI will use
            # its own config or defaults
            LOG.debug('Could not register Neutron OVN config options: %s', e)

    # TODO(hjensas): Imports from neutron in ironic_neutron_agent registers the
    # client options. We need to unregister the options we are deprecating
    # first to avoid DuplicateOptError. Remove this when dropping deprecations.
    _unregiser_deprecated_opts()

    # Add ML2 OVN config file to search path for OVN connection settings
    # This allows L2VNI to read Neutron's OVN configuration if available
    # Only include files that actually exist to avoid startup failures
    candidate_config_files = [
        '/etc/neutron/neutron.conf',
        '/etc/neutron/plugins/ml2/ml2_conf.ini',
        '/etc/neutron/plugins/ml2/ovn_agent.ini'
    ]
    default_config_files = [f for f in candidate_config_files
                            if os.path.exists(f)]
    if len(default_config_files) != len(candidate_config_files):
        missing = set(candidate_config_files) - set(default_config_files)
        LOG.warning('Config files not found (skipping): %s',
                    ', '.join(missing))

    common_config.init(sys.argv[1:], default_config_files=default_config_files)
    common_config.setup_logging()
    agent = BaremetalNeutronAgent()
    # Use service.Launcher for single-process execution to ensure hash ring
    # class variables are shared across all threads. service.launch() spawns
    # worker processes via fork, which isolates class variables and breaks
    # hash ring synchronization. See bug LP#2144384
    launcher = service.Launcher(cfg.CONF, restart_method='mutate')
    launcher.launch_service(agent)
    launcher.wait()
