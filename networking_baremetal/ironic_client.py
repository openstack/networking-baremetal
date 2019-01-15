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

from ironicclient import client
from ironicclient.common.i18n import _
from keystoneauth1 import loading
from oslo_config import cfg

DEFAULT_IRONIC_API_VERSION = 'latest'
CONF = cfg.CONF
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


def list_opts():
    return [(IRONIC_GROUP, IRONIC_OPTS +
             loading.get_session_conf_options() +
             loading.get_auth_plugin_conf_options('v3password'))]


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
