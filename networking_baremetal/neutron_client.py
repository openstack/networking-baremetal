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

from keystoneauth1 import loading
import openstack
from oslo_config import cfg
from oslo_log import log as logging

from networking_baremetal import ironic_client

CONF = cfg.CONF
LOG = logging.getLogger(__name__)

_NEUTRON_SESSION = None
NEUTRON_GROUP = 'neutron'


def list_opts():
    """Return neutron client configuration options."""
    return [
        (NEUTRON_GROUP,
         loading.get_adapter_conf_options()
         + loading.get_session_conf_options()
         + loading.get_auth_plugin_conf_options('v3password'))]


def get_session(group):
    """Get a session for the specified config group.

    :param group: Configuration group name
    :returns: keystoneauth1 session
    """
    loading.register_adapter_conf_options(CONF, group)
    loading.register_session_conf_options(CONF, group)
    loading.register_auth_conf_options(CONF, group)

    auth = loading.load_auth_from_conf_options(CONF, group)
    session = loading.load_session_from_conf_options(CONF, group, auth=auth)
    return session


def _get_neutron_session():
    """Get cached Neutron session, creating if needed.

    Returns Neutron-specific session if configured, otherwise falls back
    to ironic session for backwards compatibility.

    :returns: keystoneauth1 session
    """
    global _NEUTRON_SESSION

    if not _NEUTRON_SESSION:
        # Check if neutron-specific auth is configured
        # If auth_type is set in [neutron], use neutron credentials
        # Otherwise fall back to ironic credentials for backwards compat
        if CONF[NEUTRON_GROUP].auth_type:
            LOG.info('Using Neutron-specific authentication credentials '
                     'from [%s] section', NEUTRON_GROUP)
            _NEUTRON_SESSION = get_session(NEUTRON_GROUP)
        else:
            LOG.info('No Neutron-specific credentials configured, falling '
                     'back to [ironic] section credentials')
            _NEUTRON_SESSION = ironic_client._get_ironic_session()

    return _NEUTRON_SESSION


def get_client():
    """Get a Neutron client connection via OpenStack SDK.

    :returns: OpenStack SDK Connection object for accessing network APIs
    """
    session = _get_neutron_session()

    try:
        # Don't pass oslo_conf - let SDK discover services from
        # service catalog. This allows the same session to access
        # both Neutron and other services without config conflicts.
        return openstack.connection.Connection(session=session)
    except Exception as exc:
        LOG.error('Failed to establish a connection with Neutron, '
                  'reason: %s', exc)
        raise
