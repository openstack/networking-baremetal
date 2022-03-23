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
import re
from urllib.parse import parse_qs as urlparse_qs
from urllib.parse import urlparse
import uuid
from xml.etree.ElementTree import fromstring as etree_fromstring

from ncclient import manager
from ncclient.operations.rpc import RPCError
from ncclient.transport.errors import AuthenticationError
from ncclient.transport.errors import SessionCloseError
from ncclient.transport.errors import SSHError
from neutron_lib.api.definitions import portbindings
from neutron_lib.api.definitions import provider_net
from neutron_lib import constants as n_const
from neutron_lib import exceptions as n_exec
from neutron_lib.plugins.ml2 import api
from oslo_config import cfg
from oslo_log import log as logging
import tenacity

from networking_baremetal import common
from networking_baremetal import constants
from networking_baremetal.constants import NetconfEditConfigOperation as nc_op
from networking_baremetal.drivers import base
from networking_baremetal import exceptions
from networking_baremetal.openconfig.interfaces import interfaces
from networking_baremetal.openconfig.network_instance import network_instance
from networking_baremetal.openconfig.vlan import vlan

CONF = cfg.CONF
LOG = logging.getLogger(__name__)

LOCK_DENIED_TAG = 'lock-denied'  # [RFC 4741]
CANDIDATE = 'candidate'
RUNNING = 'running'

# Options for the device, maps to the local_link_information in the
# port binding profile.
_DEVICE_OPTS = [
    cfg.StrOpt('network_instance',
               default='default',
               advanced=True,
               help=('The L2, L3, or L2+L3 forwarding instance to use when '
                     'defining VLANs on the device.')),
    cfg.DictOpt('port_id_re_sub',
                default={},
                sample_default={'pattern': 'Ethernet', 'repl': 'eth'},
                help=('Regular expression pattern and replacement string. '
                      'Some devices do not use the port description from '
                      'LLDP in Netconf configuration. If the regular '
                      'expression pattern and replacement string is set the '
                      'port_id will be modified before passing configuration '
                      'to the device.')),
    cfg.ListOpt('disabled_properties',
                item_type=cfg.types.String(
                    choices=['port_mtu']),
                default=[],
                help=('A list of properties that should not be used, '
                      'currently only "port_mtu" is valid'))
]

# Configuration option for Netconf client connection
_NCCLIENT_OPTS = [
    cfg.StrOpt('host',
               help=('The hostname or IP address to use for connecting to the '
                     'netconf device.'),
               sample_default='device.example.com'),
    cfg.StrOpt('username',
               help='The username to use for SSH authentication.',
               sample_default='netconf'),
    cfg.IntOpt('port', default=830,
               help=('The port to use for connection to the netconf '
                     'device.')),
    cfg.StrOpt('password',
               help=('The password used if using password authentication, or '
                     'the passphrase to use for unlocking keys that require '
                     'it. (To disable attempting key authentication '
                     'altogether, set options *allow_agent* and '
                     '*look_for_keys* to `False`.'),
               sample_default='secret'),
    cfg.StrOpt('key_filename',
               help='Private key filename',
               default='~/.ssh/id_rsa'),
    cfg.BoolOpt('hostkey_verify',
                default=True,
                help=('Enables hostkey verification from '
                      '~/.ssh/known_hosts')),
    cfg.DictOpt('device_params',
                default={'name': 'default'},
                help=('ncclient device handler parameters, see ncclient '
                      'documentation for supported device handlers.')),
    cfg.BoolOpt('allow_agent',
                default=True,
                help='Enables querying SSH agent (if found) for keys.'),
    cfg.BoolOpt('look_for_keys',
                default=True,
                help=('Enables looking in the usual locations for ssh keys '
                      '(e.g. :file:`~/.ssh/id_*`)')),
]


class NetconfLockDenied(n_exec.NeutronException):
    message = ('Access to the requested lock is denied because the'
               'lock is currently held by another entity.')


class NetconfOpenConfigClient(base.BaseDeviceClient):

    def __init__(self, device):
        super().__init__(device)
        self.device = device
        self.capabilities = set()

        # Reduce the log level for ncclient, it is very chatty by default
        netconf_logger = logging.getLogger('ncclient')
        netconf_logger.setLevel(logging.WARNING)

    @staticmethod
    def _get_lock_session_id(err_info):
        """Parse lock-denied error [RFC6241]

        error-tag:      lock-denied
        error-type:     protocol
        error-severity: error
        error-info:     <session-id> : session ID of session holding the
                        requested lock, or zero to indicate a non-NETCONF
                        entity holds the lock
        Description:    Access to the requested lock is denied because the
                        lock is currently held by another entity.
        """
        root = etree_fromstring(err_info)
        session_id = root.find(
            "./{urn:ietf:params:xml:ns:netconf:base:1.0}session-id").text

        return session_id

    @staticmethod
    def process_capabilities(server_capabilities):
        capabilities = set()
        for capability in server_capabilities:
            for k, v in constants.IANA_NETCONF_CAPABILITIES.items():
                if v in capability:
                    capabilities.add(k)
            if capability.startswith('http://openconfig.net/yang'):
                openconfig_module = urlparse_qs(
                    urlparse(capability).query).get('module').pop()
                capabilities.add(openconfig_module)

        return capabilities

    def get_capabilities(self):
        # https://github.com/ncclient/ncclient/issues/525
        _ignore_close_issue_525 = False
        args = self.get_client_args()
        try:
            with manager.connect(**args) as nc_client:
                server_capabilities = nc_client.server_capabilities
                _ignore_close_issue_525 = True
        except SessionCloseError as e:
            if not _ignore_close_issue_525:
                raise e
        except (SSHError, AuthenticationError) as e:
            raise exceptions.DeviceConnectionError(device=self.device, err=e)

        return self.process_capabilities(server_capabilities)

    def get_client_args(self):
        """Get client connection arguments from configuration

        :param device: Device identifier
        """
        args = dict(
            host=CONF[self.device].host,
            port=CONF[self.device].port,
            username=CONF[self.device].username,
            hostkey_verify=CONF[self.device].hostkey_verify,
            device_params=CONF[self.device].device_params,
            keepalive=True,
            allow_agent=CONF[self.device].allow_agent,
            look_for_keys=CONF[self.device].look_for_keys,
        )
        if CONF[self.device].key_filename:
            args['key_filename'] = CONF[self.device].key_filename
        if CONF[self.device].password:
            args['password'] = CONF[self.device].password

        return args

    def get(self):
        """Get current configuration/staate from device"""
        pass

    @tenacity.retry(
        reraise=True,
        retry=tenacity.retry_if_exception_type(NetconfLockDenied),
        wait=tenacity.wait_random_exponential(multiplier=1, min=2, max=10),
        stop=tenacity.stop_after_attempt(5))
    def get_lock_and_configure(self, client, source, config):
        try:
            with client.locked(source):
                xml_config = common.config_to_xml(config)
                LOG.info(
                    'Sending configuration to Netconf device %(dev)s: '
                    '%(conf)s',
                    {'dev': self.device, 'conf': xml_config})
                if source == CANDIDATE:
                    # Revert the candidate configuration to the current
                    # running configuration. Any uncommitted changes are
                    # discarded.
                    client.discard_changes()
                    # Edit the candidate configuration
                    client.edit_config(target=source, config=xml_config)
                    # Validate the candidate configuration
                    if (':validate' in self.capabilities
                            or ':validate:1.1' in self.capabilities):
                        client.validate(source='candidate')
                    # Commit the candidate config, 30 seconds timeout
                    if (':confirmed-commit' in self.capabilities
                            or ':confirmed-commit:1.1' in self.capabilities):
                        client.commit(confirmed=True, timeout=str(30))
                        # Confirm the commit, if this commit does not
                        # succeed the device will revert the config after
                        # 30 seconds.
                    client.commit()
                elif source == RUNNING:
                    client.edit_config(target=source, config=xml_config)
                # TODO(hjensas): persist config.
        except RPCError as err:
            if err.tag == LOCK_DENIED_TAG:
                # If the candidate config is modified, some vendors do not
                # permit a new session to take a lock. This is per the RFC,
                # in this case a lock-denied error where session-id == 0 is
                # returned, because no session is actually holding the
                # lock we can discard changes which will release the lock.
                if (source == CANDIDATE
                        and self._get_lock_session_id(err.info) == '0'):
                    client.discard_changes()
                raise NetconfLockDenied()
            else:
                LOG.error('Netconf XML: %s', common.config_to_xml(config))
                raise err

    def edit_config(self, config):
        """Edit configuration on the device

        :param config: Configuration, or list of configurations
        """

        # https://github.com/ncclient/ncclient/issues/525
        _ignore_close_issue_525 = False

        if not isinstance(config, list):
            config = [config]

        try:
            with manager.connect(**self.get_client_args()) as client:
                self.capabilities = self.process_capabilities(
                    client.server_capabilities)
                if ':candidate' in self.capabilities:
                    self.get_lock_and_configure(client, CANDIDATE, config)
                    _ignore_close_issue_525 = True
                elif ':writable-running' in self.capabilities:
                    self.get_lock_and_configure(client, RUNNING, config)
                    _ignore_close_issue_525 = True
        except SessionCloseError as e:
            if not _ignore_close_issue_525:
                raise e


class NetconfOpenConfigDriver(base.BaseDeviceDriver):

    SUPPORTED_BOND_MODES = set().union(constants.NON_SWITCH_BOND_MODES)

    def __init__(self, device):
        super().__init__(device)
        self.client = NetconfOpenConfigClient(device)
        self.device = device

    def validate(self):
        try:
            LOG.info('Device %(device)s was loaded. Device capabilities: '
                     '%(caps)s', {'device': self.device,
                                  'caps': self.client.get_capabilities()})
        except exceptions.DeviceConnectionError as e:
            raise exceptions.DriverValidationError(device=self.device, err=e)

    def load_config(self):
        """Register driver specific configuration"""

        CONF.register_opts(_DEVICE_OPTS, group=self.device)
        CONF.register_opts(_NCCLIENT_OPTS, group=self.device)

    def create_network(self, context):
        """Create network on device

        :param context: NetworkContext instance describing the new
            network.
        """
        network = context.current
        segmentation_id = network[provider_net.SEGMENTATION_ID]

        net_instances = network_instance.NetworkInstances()
        net_instance = net_instances.add(CONF[self.device].network_instance)
        _vlan = net_instance.vlans.add(segmentation_id)
        # Devices has limitations for vlan names, use the hex variant of the
        # network UUID which is shorter.
        _vlan.config.name = self._uuid_as_hex(network[api.ID])
        _vlan.config.status = constants.VLAN_ACTIVE
        self.client.edit_config(net_instances)

    def update_network(self, context):
        """Update network on device

        :param context: NetworkContext instance describing the new
            network.
        """
        network = context.current
        network_orig = context.original
        segmentation_id = network[provider_net.SEGMENTATION_ID]
        segmentation_id_orig = network_orig[provider_net.SEGMENTATION_ID]
        admin_state = network['admin_state_up']
        admin_state_orig = network_orig['admin_state_up']

        add_net_instances = network_instance.NetworkInstances()
        add_net_instance = add_net_instances.add(
            CONF[self.device].network_instance)
        del_net_instances = None
        need_update = False

        if segmentation_id:
            _vlan = add_net_instance.vlans.add(segmentation_id)
            # Devices has limitations for vlan names, use the hex variant of
            # the network UUID which is shorter.
            _vlan.config.name = self._uuid_as_hex(network[api.ID])
            if network['admin_state_up']:
                _vlan.config.status = constants.VLAN_ACTIVE
            else:
                _vlan.config.status = constants.VLAN_SUSPENDED
            if admin_state != admin_state_orig:
                need_update = True
        if segmentation_id_orig and segmentation_id != segmentation_id_orig:
            need_update = True
            del_net_instances = network_instance.NetworkInstances()
            del_net_instance = del_net_instances.add(
                CONF[self.device].network_instance)
            vlan_orig = del_net_instance.vlans.remove(segmentation_id_orig)
            # Not all devices support removing a VLAN, in that case lets
            # make sure the VLAN is suspended and set a name to indicate the
            # network was deleted.
            vlan_orig.config.name = f'neutron-DELETED-{segmentation_id_orig}'
            vlan_orig.config.status = constants.VLAN_SUSPENDED

        if not need_update:
            return

        # If the segmentation ID changed, delete the old VLAN first to avoid
        # vlan name conflict.
        if del_net_instances is not None:
            self.client.edit_config(del_net_instances)

        self.client.edit_config(add_net_instances)

    def delete_network(self, context):
        """Delete network on device

        :param context: NetworkContext instance describing the new
            network.
        """
        network = context.current
        segmentation_id = network[provider_net.SEGMENTATION_ID]

        net_instances = network_instance.NetworkInstances()
        net_instance = net_instances.add(CONF[self.device].network_instance)
        _vlan = net_instance.vlans.remove(segmentation_id)
        # Not all devices support removing a VLAN, in that case lets
        # make sure the VLAN is suspended and set a name to indicate the
        # network was deleted.
        _vlan.config.name = f'neutron-DELETED-{segmentation_id}'
        _vlan.config.status = constants.VLAN_SUSPENDED
        self.client.edit_config(net_instances)

    def create_port(self, context, segment, links):
        """Create/Configure port on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param segment: segment dictionary describing segment to bind
        :param links: Local link information filtered for the device.
        """
        port = context.current
        binding_profile = port[portbindings.PROFILE]
        local_link_information = binding_profile.get(
            constants.LOCAL_LINK_INFO)
        local_group_information = binding_profile.get(
            constants.LOCAL_GROUP_INFO, {})
        bond_mode = local_group_information.get('bond_mode')

        if segment[api.NETWORK_TYPE] != n_const.TYPE_VLAN:
            switched_vlan = None
        else:
            switched_vlan = vlan.VlanSwitchedVlan()
            switched_vlan.config.operation = nc_op.REPLACE
            switched_vlan.config.interface_mode = constants.VLAN_MODE_ACCESS
            switched_vlan.config.access_vlan = segment[api.SEGMENTATION_ID]

        if not bond_mode or bond_mode in constants.NON_SWITCH_BOND_MODES:
            self.create_non_bond(context, switched_vlan, links)
        elif bond_mode in constants.LACP_BOND_MODES:
            if len(local_link_information) == len(links):
                self.create_lacp_aggregate(context, switched_vlan, links)
            else:
                # Some links is on a different device,
                # MLAG aggregate must be pre-configured.
                self.create_pre_conf_aggregate(context, switched_vlan, links)
        elif bond_mode in constants.PRE_CONF_ONLY_BOND_MODES:
            self.create_pre_conf_aggregate(context, switched_vlan, links)

    def create_non_bond(self, context, switched_vlan, links):
        """Create/Configure ports on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param switched_vlan: switched_vlan OpenConfig object
        :param links: Local link information filtered for the device.
        """
        port = context.current
        network = context.network.current

        ifaces = interfaces.Interfaces()
        for link in links:
            link_port_id = link.get(constants.PORT_ID)
            link_port_id = self._port_id_resub(link_port_id)

            iface = ifaces.add(link_port_id)
            iface.config.enabled = port['admin_state_up']
            if 'port_mtu' not in CONF[self.device].disabled_properties:
                iface.config.mtu = network[api.MTU]
            iface.config.description = f'neutron-{port[api.ID]}'
            if switched_vlan is not None:
                iface.ethernet.switched_vlan = switched_vlan
            else:
                del iface.ethernet

        self.client.edit_config(ifaces)

    def create_lacp_aggregate(self, context, switched_vlan, links):
        """Create/Configure LACP aggregate on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param switched_vlan: switched_vlan OpenConfig object
        :param links: Local link information filtered for the device.
        """
        pass

    def create_pre_conf_aggregate(self, context, switched_vlan, links):
        """Create/Configure pre-configured aggregate on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param switched_vlan: switched_vlan OpenConfig object
        :param links: Local link information filtered for the device.
        """
        pass

    def update_port(self, context, links):
        """Update port on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param links: Local link information filtered for the device.
        """
        if (not self.admin_state_changed(context)
                and not self.network_mtu_changed(context)):
            return

        port = context.current
        binding_profile = port[portbindings.PROFILE]
        local_link_information = binding_profile.get(
            constants.LOCAL_LINK_INFO)
        local_group_information = binding_profile.get(
            constants.LOCAL_GROUP_INFO, {})
        bond_mode = local_group_information.get('bond_mode')

        if not bond_mode or bond_mode in constants.NON_SWITCH_BOND_MODES:
            self.update_non_bond(context, links)
        elif bond_mode in constants.LACP_BOND_MODES:
            if len(local_link_information) == len(links):
                self.update_lacp_aggregate(context, links)
            else:
                # Some links is on a different device,
                # MLAG aggregate must be pre-configured.
                self.update_pre_conf_aggregate(context, links)
        elif bond_mode in constants.PRE_CONF_ONLY_BOND_MODES:
            self.update_pre_conf_aggregate(context, links)

    def update_non_bond(self, context, links):
        """Update port on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param links: Local link information filtered for the device.
        """
        network = context.network.current
        ifaces = interfaces.Interfaces()
        port = context.current

        for link in links:
            link_port_id = link.get(constants.PORT_ID)
            link_port_id = self._port_id_resub(link_port_id)

            iface = ifaces.add(link_port_id)
            iface.config.enabled = port['admin_state_up']
            if 'port_mtu' not in CONF[self.device].disabled_properties:
                iface.config.mtu = network[api.MTU]

            del iface.ethernet

        self.client.edit_config(ifaces)

    def update_lacp_aggregate(self, context, links):
        """Update LACP aggregate on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param links: Local link information filtered for the device.
        """
        pass

    def update_pre_conf_aggregate(self, context, links):
        """Update pre-configured aggregate on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param links: Local link information filtered for the device.
        """
        pass

    def delete_port(self, context, links, current=True):
        """Delete/Un-configure port on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param links: Local link information filtered for the device.
        :param current: Boolean, when true use context.current, when
            false use context.original
        """
        port = context.current if current else context.original
        binding_profile = port[portbindings.PROFILE]
        local_link_information = binding_profile.get(
            constants.LOCAL_LINK_INFO)
        local_group_information = binding_profile.get(
            constants.LOCAL_GROUP_INFO, {})
        bond_mode = local_group_information.get('bond_mode')

        if not bond_mode or bond_mode in constants.NON_SWITCH_BOND_MODES:
            self.delete_non_bond(context, links)
        elif bond_mode in constants.LACP_BOND_MODES:
            if len(local_link_information) == len(links):
                self.delete_lacp_aggregate(context, links)
            else:
                # Some links is on a different device,
                # MLAG aggregate must be pre-configured.
                self.delete_pre_conf_aggregate(links)
        elif bond_mode in constants.PRE_CONF_ONLY_BOND_MODES:
            self.delete_pre_conf_aggregate(links)

    def delete_non_bond(self, context, links):
        """Delete/Un-configure port on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param links: Local link information filtered for the device.
        """
        network = context.network.current
        ifaces = interfaces.Interfaces()
        for link in links:
            link_port_id = link.get(constants.PORT_ID)
            link_port_id = self._port_id_resub(link_port_id)

            iface = ifaces.add(link_port_id)
            iface.config.operation = nc_op.REMOVE
            # Not possible mark entire config for removal due to name leaf-ref
            # Set dummy values for properties to remove
            iface.config.description = ''
            iface.config.enabled = False
            if 'port_mtu' not in CONF[self.device].disabled_properties:
                iface.config.mtu = 0
            if network[provider_net.NETWORK_TYPE] == n_const.TYPE_VLAN:
                iface.ethernet.switched_vlan.config.operation = nc_op.REMOVE
            else:
                del iface.ethernet

        self.client.edit_config(ifaces)

    def delete_lacp_aggregate(self, context, links):
        """Delete/Un-configure LACP aggregate on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param links: Local link information filtered for the device.
        """
        pass

    def delete_pre_conf_aggregate(self, links):
        """Delete/Un-configure pre-configured aggregate on device

        :param links: Local link information filtered for the device.
        """
        pass

    @staticmethod
    def _uuid_as_hex(_uuid):
        return uuid.UUID(_uuid).hex

    def _port_id_resub(self, link_port_id):
        """Replace pattern

        Regular expression pattern and replacement string.
        Some devices don not use the port  description from
        LLDP in Netconf configuration. If the regular expression
        pattern and replacement string is set the  port_id will
        be modified before passing configuration to the device.

        Replacing the leftmost non-overlapping occurrences of pattern
        in string by the replacement repl.
        """
        if CONF[self.device].port_id_re_sub:
            pattern = CONF[self.device].port_id_re_sub.get('pattern')
            repl = CONF[self.device].port_id_re_sub.get('repl')
            link_port_id = re.sub(pattern, repl, link_port_id)

        return link_port_id

    @staticmethod
    def admin_state_changed(context):
        port = context.current
        port_orig = context.original
        return (port and port_orig
                and port['admin_state_up'] != port_orig['admin_state_up'])

    @staticmethod
    def network_mtu_changed(context):
        network = context.network.current
        network_orig = context.network.original
        return (network and network_orig
                and network[api.MTU] != network_orig[api.MTU])
