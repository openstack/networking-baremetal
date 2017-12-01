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

import eventlet
# oslo_messaging/notify/listener.py documents that monkeypatching is required
eventlet.monkey_patch()

import socket
import sys

from ironicclient import client
import ironicclient.common.apiclient.exceptions as ironic_exc
from keystoneauth1 import loading
from neutron.agent import rpc as agent_rpc
from neutron.common import config as common_config
from neutron.common import topics
from neutron.conf.agent import common as agent_config
from neutron_lib import constants as n_const
from neutron_lib import context
from oslo_config import cfg
from oslo_log import log as logging
import oslo_messaging
from oslo_service import loopingcall
from oslo_utils import timeutils
from oslo_utils import uuidutils
from tooz import hashring

from networking_baremetal import constants

DEFAULT_IRONIC_API_VERSION = 'latest'
CONF = cfg.CONF
LOG = logging.getLogger(__name__)
IRONIC_SESSION = None
IRONIC_GROUP = 'ironic'


IRONIC_OPTS = [
    cfg.StrOpt('os_region',
               help=_('Keystone region used to get Ironic endpoints.')),
    cfg.StrOpt('auth_strategy',
               default='keystone',
               choices=('keystone', 'noauth'),
               help=_('Method to use for authentication: noauth or '
                      'keystone.')),
    cfg.StrOpt('ironic_url',
               default='http://localhost:6385/',
               help=_('Ironic API URL, used to set Ironic API URL when '
                      'auth_strategy option is noauth to work with standalone '
                      'Ironic without keystone.')),
    cfg.IntOpt('retry_interval',
               default=2,
               help=_('Interval between retries in case of conflict error '
                      '(HTTP 409).')),
    cfg.IntOpt('max_retries',
               default=30,
               help=_('Maximum number of retries in case of conflict error '
                      '(HTTP 409).')),
]

CONF.register_opts(IRONIC_OPTS, group=IRONIC_GROUP)
CONF.import_group('AGENT', 'neutron.plugins.ml2.drivers.agent.config')


def list_opts():
    return [(IRONIC_GROUP, IRONIC_OPTS +
             loading.get_session_conf_options() +
             loading.get_auth_plugin_conf_options('v3password')),
            ('agent', agent_config.AGENT_STATE_OPTS)]


def get_session(group):
    loading.register_session_conf_options(CONF, group)
    loading.register_auth_conf_options(CONF, group)
    auth = loading.load_auth_from_conf_options(CONF, group)
    session = loading.load_session_from_conf_options(
        CONF, group, auth=auth)
    return session


def get_client(api_version=DEFAULT_IRONIC_API_VERSION):
    """Get Ironic client instance."""
    # NOTE: To support standalone ironic without keystone
    if CONF.ironic.auth_strategy == 'noauth':
        args = {'token': 'noauth',
                'endpoint': CONF.ironic.ironic_url}
    else:
        global IRONIC_SESSION
        if not IRONIC_SESSION:
            IRONIC_SESSION = get_session(IRONIC_GROUP)
        args = {'session': IRONIC_SESSION,
                'region_name': CONF.ironic.os_region}
    args['os_ironic_api_version'] = api_version
    args['max_retries'] = CONF.ironic.max_retries
    args['retry_interval'] = CONF.ironic.retry_interval
    return client.Client(1, **args)


def _set_up_notifier(transport, uuid):
    return oslo_messaging.Notifier(
        transport,
        publisher_id='ironic-neutron-agent-' + uuid,
        driver='messagingv2',
        topics=['ironic-neutron-agent-heartbeat'])


def _set_up_listener(transport, agent_id):
    targets = [oslo_messaging.Target(topic='ironic-neutron-agent-heartbeat')]
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
                LOG.exception(
                    'Failed to add member %s to hash ring!' % payload['id'])
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
                    LOG.exception('Failed to remove member %s from hash ring!'
                                  % member['id'])

        return oslo_messaging.NotificationResult.HANDLED


class BaremetalNeutronAgent(object):

    def __init__(self):
        self.context = context.get_admin_context_without_session()
        self.agent_id = uuidutils.generate_uuid(dashed=True)
        self.agent_host = socket.gethostname()

        # Set up oslo_messaging notifier and listener to keep track of other
        # members
        self.transport = oslo_messaging.get_notification_transport(CONF)
        self.notifier = _set_up_notifier(self.transport, self.agent_id)
        self.listener = _set_up_listener(self.transport, self.agent_id)
        self.listener.start()

        self.member_manager = HashRingMemberManagerNotificationEndpoint()

        self.state_rpc = agent_rpc.PluginReportStateAPI(topics.REPORTS)
        self.ironic_client = get_client()
        self.reported_nodes = {}
        LOG.info('Agent networking-baremetal initialized.')

    def start_looping_calls(self):
        self.notify_agents = loopingcall.FixedIntervalLoopingCall(
            self._notify_peer_agents)
        self.notify_agents.start(interval=(CONF.AGENT.report_interval / 3))
        self.heartbeat = loopingcall.FixedIntervalLoopingCall(
            self._report_state)
        self.heartbeat.start(interval=CONF.AGENT.report_interval,
                             initial_delay=CONF.AGENT.report_interval)

    def _notify_peer_agents(self):
        try:
            self.notifier.info({
                'ironic-neutron-agent': 'heartbeat'},
                'ironic-neutron-agent-hearbeat',
                {'id': self.agent_id,
                 'host': self.agent_host,
                 'timestamp': timeutils.utcnow_ts()})
        except Exception:
            LOG.exception('Failed to send hash ring membership hearbeat!')

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

    def _get_ironic_ports(self):
        ironic_ports = []
        try:
            ironic_ports = self.ironic_client.port.list(detail=True)
        except ironic_exc.UnsupportedVersion:
            LOG.exception("Failed to get ironic port data! Ironic Client is "
                          "using unsupported version of the API. State "
                          "reporting for agent will be disabled.")
            self.heartbeat.stop()
        except (ironic_exc.AuthPluginOptionsMissing,
                ironic_exc.AuthSystemNotFound):
            LOG.exception("Failed to get ironic port data! Ironic Client "
                          "autorization failure. State reporting for agent "
                          "will be disabled.")
            self.heartbeat.stop()
        except Exception:
            LOG.exception("Failed to get ironic port data!")

        return ironic_ports

    def _report_state(self):
        node_states = {}
        ironic_ports = self._get_ironic_ports()
        if not ironic_ports:
            return
        for port in ironic_ports:
            node = port.node_uuid
            if (self.agent_id not in
                    self.member_manager.hashring[node.encode('utf-8')]):
                continue
            node_states.setdefault(node, self.get_template_node_state(node))
            mapping = node_states[node]["configurations"]["bridge_mappings"]
            if port.physical_network is not None:
                mapping[port.physical_network] = "yes"

        for state in node_states.values():
            # If the node was not previously reported with current
            # configuration set the start_flag True.
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
                self.heartbeat.stop()
                # Don't continue reporting the remaining agents in this case.
                return
            except Exception:
                LOG.exception("Failed reporting state!")
                # Don't continue reporting the remaining nodes if one failed.
                return
            self.reported_nodes.update(
                {state['host']: state['configurations']})

    def run(self):
        self.start_looping_calls()
        self.heartbeat.wait()
        self.notify_agents.wait()


def main():
    common_config.init(sys.argv[1:])
    common_config.setup_logging()
    agent = BaremetalNeutronAgent()
    agent.run()
