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
import socket
import sys
from urllib import parse as urlparse

import eventlet
# oslo_messaging/notify/listener.py documents that monkeypatching is required
eventlet.monkey_patch()
from neutron.agent import rpc as agent_rpc
from neutron.common import config as common_config
from neutron.conf.agent import common as agent_config
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
from tooz import hashring

from networking_baremetal import constants
from networking_baremetal import ironic_client

CONF = cfg.CONF
LOG = logging.getLogger(__name__)
CONF.import_group('AGENT', 'neutron.plugins.ml2.drivers.agent.config')


def list_opts():
    return [('agent', agent_config.AGENT_STATE_OPTS)]


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
        transport, targets, endpoints, executor='eventlet', pool=agent_id)


class HashRingMemberManagerNotificationEndpoint(object):
    """Class variables members and hashring is shared by all instances"""

    filter_rule = oslo_messaging.NotificationFilter(
        publisher_id='^ironic-neutron-agent.*')

    members = []
    hashring = hashring.HashRing([])

    def info(self, ctxt, publisher_id, event_type, payload, metadata):

        timestamp = timeutils.utcnow_ts()
        # Add members or update timestamp for existing members
        if not payload['id'] in [x['id'] for x in self.members]:
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
        LOG.info('Agent networking-baremetal initialized.')

    def start(self):
        LOG.info('Starting agent networking-baremetal.')
        self.pool_listener.start()
        self.listener.start()
        self.notify_agents = loopingcall.FixedIntervalLoopingCall(
            self._notify_peer_agents)
        self.notify_agents.start(interval=(CONF.AGENT.report_interval / 3))
        self.heartbeat = loopingcall.FixedIntervalLoopingCall(
            self._report_state)
        self.heartbeat.start(interval=CONF.AGENT.report_interval,
                             initial_delay=CONF.AGENT.report_interval)

    def stop(self, failure=False):
        LOG.info('Stopping agent networking-baremetal.')
        if self.heartbeat:
            self.heartbeat.stop()
        if self.notify_agents:
            self.notify_agents.stop()
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
            'agent_type': constants.BAREMETAL_AGENT_TYPE}

    def _report_state(self):
        node_states = {}
        ironic_ports = self.ironic_client.ports(details=True)

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
        if abort_operation:
            # We don't expect the agent to work, and as such we should call
            # stop so the program unwinds and begins to exit.
            self.stop(failure=True)


def _unregiser_deprecated_opts():
    CONF.reset()
    CONF.unregister_opts(
        [CONF._groups[ironic_client.IRONIC_GROUP]._opts[opt]['opt']
         for opt in ironic_client._deprecated_opts],
        group=ironic_client.IRONIC_GROUP)


def main():
    common_config.register_common_config_options()
    # TODO(hjensas): Imports from neutron in ironic_neutron_agent registers the
    # client options. We need to unregister the options we are deprecating
    # first to avoid DuplicateOptError. Remove this when dropping deprecations.
    _unregiser_deprecated_opts()
    common_config.init(sys.argv[1:])
    common_config.setup_logging()
    agent = BaremetalNeutronAgent()
    launcher = service.launch(cfg.CONF, agent, restart_method='mutate')
    launcher.wait()
