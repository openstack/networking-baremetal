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

import sys

from ironicclient import client
import ironicclient.common.apiclient.exceptions as ironic_exc
from keystoneauth1 import loading
from neutron.agent import rpc as agent_rpc
from neutron.common import config as common_config
from neutron.common import topics
from neutron_lib import constants as n_const
from neutron_lib import context
from oslo_config import cfg
from oslo_log import log as logging
from oslo_service import loopingcall

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


class BaremetalNeutronAgent(object):

    def __init__(self):
        self.context = context.get_admin_context_without_session()
        self.state_rpc = agent_rpc.PluginReportStateAPI(topics.REPORTS)
        self.ironic_client = get_client()
        self.reported_nodes = {}
        LOG.info('Agent networking-baremetal initialized.')

    def start_looping_calls(self):
        self.heartbeat = loopingcall.FixedIntervalLoopingCall(
            self._report_state)
        self.heartbeat.start(interval=CONF.AGENT.report_interval)

    def get_template_node_state(self, node_uuid):
        return {
            'binary': constants.BAREMETAL_BINARY,
            'host': node_uuid,
            'topic': n_const.L2_AGENT_TOPIC,
            'configurations': {
                'bridge_mappings': {},
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


def main():
    common_config.init(sys.argv[1:])
    common_config.setup_logging()
    agent = BaremetalNeutronAgent()
    agent.run()
