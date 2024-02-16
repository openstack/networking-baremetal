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
import random
import re
from urllib.parse import parse_qs as urlparse_qs
from urllib.parse import urlparse
import uuid
from xml.etree import ElementTree

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
from networking_baremetal import config
from networking_baremetal import constants
from networking_baremetal.constants import NetconfEditConfigOperation as nc_op
from networking_baremetal.drivers import base
from networking_baremetal import exceptions
from networking_baremetal.openconfig.interfaces import interfaces
from networking_baremetal.openconfig.lacp import lacp
from networking_baremetal.openconfig.network_instance import network_instance
from networking_baremetal.openconfig.vlan import vlan

CONF = cfg.CONF
LOG = logging.getLogger(__name__)

LOCK_DENIED_TAG = 'lock-denied'  # [RFC 4741]
CANDIDATE = 'candidate'
RUNNING = 'running'
DEFERRED = 'deferred'

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
                      'currently only "port_mtu" is valid')),
    cfg.BoolOpt('manage_lacp_aggregates',
                default=True,
                help=('When set to true the driver will manage LACP '
                      'aggregates if link_group_information is defined in '
                      'the binding:profile. When this is false the driver '
                      'expect the link aggregation to be pre-configured on '
                      'the device, and only perform vlan plugging.')),
    cfg.StrOpt('link_aggregate_prefix',
               default='Port-Channel',
               help=('The device specific prefix used for link-aggregation '
                     'ports. Common values: "po", "port-channel" or '
                     '"Port-Channel".')),
    cfg.StrOpt('link_aggregate_range',
               default='1000..2000',
               help=('Range of link aggregation interface IDs that the driver '
                     'can use when managing link aggregates.')),
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


def list_driver_opts():
    return [('networking_baremetal', config._opts),
            ('netconf-openconfig-example',
             config._device_opts + _DEVICE_OPTS + _NCCLIENT_OPTS)]


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
        root = ElementTree.fromstring(err_info)
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

    def get(self, **kwargs):
        """Get current configuration/staate from device"""
        # https://github.com/ncclient/ncclient/issues/525
        _ignore_close_issue_525 = False

        query = kwargs.get('query')
        q_filter = ElementTree.tostring(query.to_xml_element()).decode('utf-8')
        try:
            with manager.connect(**self.get_client_args()) as client:
                reply = client.get(filter=('subtree', q_filter))
                _ignore_close_issue_525 = True
        except SessionCloseError as e:
            # https://github.com/ncclient/ncclient/issues/525
            if not _ignore_close_issue_525:
                raise e
        except RPCError as e:
            LOG.error('Netconf XML: %s', q_filter)
            raise e

        return reply.data_xml

    @tenacity.retry(
        reraise=True,
        retry=tenacity.retry_if_exception_type(NetconfLockDenied),
        wait=tenacity.wait_random_exponential(multiplier=1, min=2, max=10),
        stop=tenacity.stop_after_attempt(5))
    def get_lock_and_configure(self, client, source, config,
                               deferred_allocations):
        try:
            with client.locked(source):
                # Aggregate ID deferred until we have config lock
                # Get free aggregate ID by querying the device and update conf
                if deferred_allocations:
                    aggregate_id = self.get_free_aggregate_id(client)
                    self.allocate_deferred(aggregate_id, config)
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

    def edit_config(self, config, deferred_allocations=False):
        """Edit configuration on the device

        :param config: Configuration, or list of configurations
        :param deferred_allocations: Used for link aggregates, the aggregate
          id cannot be allocated before device config is locked. When this
          is true an available aggregate id is identified by querying the
          device, and the configuration objects are updated accordingly before
          configuration is sent to the device.
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
                    self.get_lock_and_configure(
                        client, CANDIDATE, config, deferred_allocations)
                    _ignore_close_issue_525 = True
                elif ':writable-running' in self.capabilities:
                    self.get_lock_and_configure(
                        client, RUNNING, config, deferred_allocations)
                    _ignore_close_issue_525 = True
        except SessionCloseError as e:
            if not _ignore_close_issue_525:
                raise e

    def get_aggregation_ids(self):
        """Get aggregation IDs and aggregation prefix from config"""
        prefix = CONF[self.device].link_aggregate_prefix
        aggregate_id_range = CONF[self.device].link_aggregate_range.split('..')
        aggregate_ids = {f'{prefix}{x}'
                         for x in range(int(aggregate_id_range[0]),
                                        int(aggregate_id_range[1]) + 1)}
        return aggregate_ids

    @staticmethod
    def allocate_deferred(aggregate_id, config):
        """Set aggregation id where it was deferred

        :param aggregate_id: Aggregation ID for the link aggregate,
            for example 'po123'
        :param config: Configuration objects to update
        """
        for conf in config:
            if isinstance(conf, interfaces.Interfaces):
                for iface in conf:
                    if isinstance(iface, interfaces.InterfaceAggregate):
                        if iface.name == DEFERRED:
                            iface.name = aggregate_id
                        if iface.config.name == DEFERRED:
                            iface.config.name = aggregate_id
                    elif isinstance(iface, interfaces.InterfaceEthernet):
                        if iface.ethernet.config.aggregate_id == DEFERRED:
                            iface.ethernet.config.aggregate_id = aggregate_id
            if isinstance(conf, lacp.LACP):
                for lacp_iface in conf.interfaces.interfaces:
                    if lacp_iface.name == DEFERRED:
                        lacp_iface.name = aggregate_id

    def get_free_aggregate_id(self, client_locked):
        """Get free aggregate id by querying device config

        :param client_locked: Netconf client with active
            configuration lock
        """
        aggregate_prefix = CONF[self.device].link_aggregate_prefix
        aggregate_ids = self.get_aggregation_ids()
        # Create a interfaces query
        oc_ifaces = interfaces.Interfaces()
        # Use empty string for the name, so the 'get' return all interfaces
        oc_iface = oc_ifaces.add('', interface_type=constants.IFACE_TYPE_BASE)
        # Don't need the config group
        del oc_iface.config
        # Get interfaces from device
        element = oc_ifaces.to_xml_element()
        device_interfaces = client_locked.get(filter=(
            'subtree', ElementTree.tostring(element).decode("utf-8")))
        # Find all interface names and filter on aggregate_prefix
        root = ElementTree.fromstring(device_interfaces.data_xml)
        used_aggregate_ids = {
            x.text for x in root.findall(f'.//{{{oc_ifaces.NAMESPACE}}}name')
            if x.text.startswith(aggregate_prefix)}
        # Get the difference, and make a random choice
        available_aggregate_ids = aggregate_ids.difference(used_aggregate_ids)

        return random.choice(list(available_aggregate_ids))


class NetconfOpenConfigDriver(base.BaseDeviceDriver):

    SUPPORTED_BOND_MODES = set().union(constants.NON_SWITCH_BOND_MODES,
                                       constants.LACP_BOND_MODES,
                                       constants.PRE_CONF_ONLY_BOND_MODES)

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
            if CONF[self.device].manage_lacp_aggregates:
                self.create_lacp_aggregate(context, switched_vlan, links)
            else:
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
        port = context.current
        network = context.network.current
        binding_profile = port[portbindings.PROFILE]
        local_group_information = binding_profile.get(
            constants.LOCAL_GROUP_INFO, {})
        dev_type = CONF[self.device].device_params.get('name')
        bond_properties = local_group_information.get('bond_properties', {})
        lacp_interval = bond_properties.get(constants.LACP_INTERVAL)
        min_links = bond_properties.get(constants.LACP_MIN_LINKS)
        ifaces = interfaces.Interfaces()
        _lacp = lacp.LACP()
        lacp_iface = _lacp.interfaces.add(DEFERRED)
        lacp_iface.operation = nc_op.REPLACE
        lacp_iface.config.interval = (constants.LACP_PERIOD_FAST
                                      if lacp_interval in {'fast', 1, '1'}
                                      else constants.LACP_PERIOD_SLOW)
        # NX-API only allows configuring LACP interval rate on a port-channel
        # member which is not in shutdown state. Support would require a two
        # commit approach.
        if dev_type in {'nexus'}:
            LOG.warning('IGNORING LACP interval (bond_lacp_rate). The driver '
                        'does not support LACP interval for this device type. '
                        'Device: %(device)s, Port: %(port)s',
                        {'device': self.device, 'port': port[api.ID]})
            del lacp_iface.config.interval

        for link in links:
            link_port_id = link.get(constants.PORT_ID)
            link_port_id = self._port_id_resub(link_port_id)
            iface = ifaces.add(
                link_port_id, interface_type=constants.IFACE_TYPE_ETHERNET)
            iface.config.operation = nc_op.MERGE
            iface.config.enabled = port['admin_state_up']
            if 'port_mtu' not in CONF[self.device].disabled_properties:
                iface.config.mtu = network[api.MTU]
            iface.config.description = f'neutron-{port[api.ID]}'
            iface.ethernet.config.aggregate_id = DEFERRED

        iface = ifaces.add(DEFERRED,
                           interface_type=constants.IFACE_TYPE_AGGREGATE)
        iface.config.operation = nc_op.MERGE
        iface.config.name = DEFERRED
        iface.config.enabled = port['admin_state_up']
        iface.config.description = f'neutron-{port[api.ID]}'
        iface.aggregation.config.lag_type = constants.LAG_TYPE_LACP
        if min_links:
            iface.aggregation.config.min_links = int(min_links)
        if switched_vlan is not None:
            iface.aggregation.switched_vlan = switched_vlan
        else:
            del iface.aggregation.switched_vlan

        self.client.edit_config([ifaces, _lacp], deferred_allocations=True)

    def create_pre_conf_aggregate(self, context, switched_vlan, links):
        """Create/Configure pre-configured aggregate on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param switched_vlan: switched_vlan OpenConfig object
        :param links: Local link information filtered for the device.
        """
        port = context.current
        aggregate_ids = self.get_aggregate_ids(links)
        if not aggregate_ids:
            raise exceptions.PreConfiguredAggrergateNotFound(
                links=links, device=self.device)
        ifaces = interfaces.Interfaces()
        for aggregate_id in aggregate_ids:
            iface = ifaces.add(aggregate_id,
                               interface_type=constants.IFACE_TYPE_AGGREGATE)
            iface.operation = nc_op.MERGE
            iface.config.enabled = port['admin_state_up']
            if switched_vlan is not None:
                iface.aggregation.switched_vlan = switched_vlan
            else:
                del iface.aggregation.switched_vlan

        self.client.edit_config(ifaces)

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
        local_group_information = binding_profile.get(
            constants.LOCAL_GROUP_INFO, {})
        bond_mode = local_group_information.get('bond_mode')

        if not bond_mode or bond_mode in constants.NON_SWITCH_BOND_MODES:
            self.update_non_bond(context, links)
        elif bond_mode in constants.LACP_BOND_MODES:
            if CONF[self.device].manage_lacp_aggregates:
                self.update_lacp_aggregate(context, links)
            else:
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
        port = context.current
        network = context.network.current
        aggregate_ids = self.get_aggregate_ids(links)
        ifaces = interfaces.Interfaces()
        for link in links:
            link_port_id = link.get(constants.PORT_ID)
            link_port_id = self._port_id_resub(link_port_id)

            iface = ifaces.add(link_port_id,
                               interface_type=constants.IFACE_TYPE_ETHERNET)
            iface.config.enabled = port['admin_state_up']
            if 'port_mtu' not in CONF[self.device].disabled_properties:
                iface.config.mtu = network[api.MTU]

            del iface.ethernet

        for aggregate_id in aggregate_ids:
            iface = ifaces.add(aggregate_id,
                               interface_type=constants.IFACE_TYPE_AGGREGATE)
            iface.operation = nc_op.MERGE
            iface.config.enabled = port['admin_state_up']
            del iface.aggregation

        self.client.edit_config(ifaces)

    def update_pre_conf_aggregate(self, context, links):
        """Update pre-configured aggregate on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param links: Local link information filtered for the device.
        """
        port = context.current
        aggregate_ids = self.get_aggregate_ids(links)
        if not aggregate_ids:
            raise exceptions.PreConfiguredAggrergateNotFound(
                links=links, device=self.device)
        ifaces = interfaces.Interfaces()
        for aggregate_id in aggregate_ids:
            iface = ifaces.add(aggregate_id,
                               interface_type=constants.IFACE_TYPE_AGGREGATE)
            iface.operation = nc_op.MERGE
            iface.config.enabled = port['admin_state_up']

        self.client.edit_config(ifaces)

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
        local_group_information = binding_profile.get(
            constants.LOCAL_GROUP_INFO, {})
        bond_mode = local_group_information.get('bond_mode')

        if not bond_mode or bond_mode in constants.NON_SWITCH_BOND_MODES:
            self.delete_non_bond(context, links)
        elif bond_mode in constants.LACP_BOND_MODES:
            if CONF[self.device].manage_lacp_aggregates:
                self.delete_lacp_aggregate(context, links)
            else:
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
        network = context.network.current
        aggregate_ids = self.get_aggregate_ids(links)
        ifaces = interfaces.Interfaces()
        for link in links:
            link_port_id = link.get(constants.PORT_ID)
            link_port_id = self._port_id_resub(link_port_id)
            # Set up interface links for config remove
            iface = ifaces.add(link_port_id,
                               interface_type=constants.IFACE_TYPE_ETHERNET)
            iface.config.operation = nc_op.REMOVE
            iface.config.description = ''
            iface.config.enabled = False
            if 'port_mtu' not in CONF[self.device].disabled_properties:
                iface.config.mtu = 0
            iface.ethernet.config.operation = nc_op.REMOVE
            if network[provider_net.NETWORK_TYPE] == n_const.TYPE_VLAN:
                iface.ethernet.switched_vlan.config.operation = nc_op.REMOVE
            else:
                del iface.ethernet.switched_vlan

        # Set up lacp and aggregate interface for removal
        _lacp = lacp.LACP()
        for aggregate_id in aggregate_ids:
            # Remove LACP interface
            lacp_iface = _lacp.interfaces.add(aggregate_id)
            lacp_iface.operation = nc_op.REMOVE
            del lacp_iface.config
            # Remove Aggregate interface
            iface = ifaces.add(aggregate_id,
                               interface_type=constants.IFACE_TYPE_AGGREGATE)
            iface.operation = nc_op.REMOVE
            del iface.config
            del iface.aggregation

        self.client.edit_config([_lacp, ifaces])

    def delete_pre_conf_aggregate(self, links):
        """Delete/Un-configure pre-configured aggregate on device

        :param links: Local link information filtered for the device.
        """
        aggregate_ids = self.get_aggregate_ids(links)
        if not aggregate_ids:
            raise exceptions.PreConfiguredAggrergateNotFound(
                links=links, device=self.device)
        ifaces = interfaces.Interfaces()
        for aggregate_id in aggregate_ids:
            iface = ifaces.add(aggregate_id,
                               interface_type=constants.IFACE_TYPE_AGGREGATE)
            iface.config.enabled = False
            iface.aggregation.switched_vlan.config.operation = nc_op.REMOVE

        self.client.edit_config(ifaces)

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

    def get_aggregate_ids(self, links):
        query = interfaces.Interfaces()
        for link in links:
            link_port_id = link.get(constants.PORT_ID)
            link_port_id = self._port_id_resub(link_port_id)
            # Set up query
            q_iface = query.add(link_port_id,
                                interface_type=constants.IFACE_TYPE_ETHERNET)
            # Remove config and ethernet for broad filter.
            del q_iface.config
            del q_iface.ethernet

        # Get aggregate ids by querying the link interfaces
        xml_result = self.client.get(query=query)
        root = ElementTree.fromstring(xml_result)
        xpath_query_result = root.findall(
            './/{http://openconfig.net/yang/interfaces/aggregate}'
            'aggregate-id')
        aggregate_ids = {x.text for x in xpath_query_result}

        return aggregate_ids

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
