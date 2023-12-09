# Copyright 2015 Mirantis, Inc.
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

from neutron.db import provisioning_blocks
from neutron.plugins.ml2.drivers import mech_agent
from neutron_lib.api.definitions import portbindings
from neutron_lib.api.definitions import provider_net
from neutron_lib.callbacks import resources
from neutron_lib import constants as n_const
from neutron_lib.plugins.ml2 import api
from oslo_config import cfg
from oslo_log import log as logging

from networking_baremetal import common
from networking_baremetal import config
from networking_baremetal import constants
from networking_baremetal import exceptions

CONF = cfg.CONF
LOG = logging.getLogger(__name__)

BAREMETAL_DRV_ENTITY = 'BAREMETAL_DRV_ENTITIY'


class BaremetalMechanismDriver(mech_agent.SimpleAgentMechanismDriverBase):

    def __init__(self):
        super(BaremetalMechanismDriver, self).__init__(
            agent_type=constants.BAREMETAL_AGENT_TYPE,
            vif_type=portbindings.VIF_TYPE_OTHER,
            vif_details={
                portbindings.VIF_DETAILS_CONNECTIVITY: self.connectivity},
            supported_vnic_types=[portbindings.VNIC_BAREMETAL])

        self.devices = config.get_devices()
        # Use set to remove duplicates,
        # i.e device has both switch_id and switch_info
        for device_id in set(self.devices.values()):
            device_driver = common.driver_mgr(device_id)
            device_driver.load_config()
            try:
                device_driver.validate()
            except exceptions.DriverValidationError as e:
                LOG.exception(e)

    @property
    def connectivity(self):
        return portbindings.CONNECTIVITY_L2

    def get_allowed_network_types(self, agent):
        """Return the agent's or driver's allowed network types.

        For example: return ('flat', ...). You can also refer to the
        configuration the given agent exposes.
        """
        return [n_const.TYPE_FLAT, n_const.TYPE_VLAN]

    def get_mappings(self, agent):
        """Return the agent's bridge or interface mappings.

        For example: agent['configurations'].get('bridge_mappings', {}).
        """
        return agent['configurations'].get('bridge_mappings', {})

    def create_network_precommit(self, context):
        """Allocate resources for a new network.

        Create a new network, allocating resources as necessary in the
        database. Called inside transaction context on session. Call
        cannot block.  Raising an exception will result in a rollback
        of the current transaction.

        :param context: NetworkContext instance describing the new
            network.
        """
        pass

    def create_network_postcommit(self, context):
        """Create a network.

        Called after the transaction commits. Call can block, though
        will block the entire process so care should be taken to not
        drastically affect performance. Raising an exception will
        cause the deletion of the resource.

        :param context: NetworkContext instance describing the new
            network.
        """
        network = context.current
        network_type = network[provider_net.NETWORK_TYPE]
        segmentation_id = network[provider_net.SEGMENTATION_ID]
        physical_network = network[provider_net.PHYSICAL_NETWORK]

        # If not VLAN network, or no segmentation_id - nothing to do.
        if network_type != n_const.TYPE_VLAN or not segmentation_id:
            return

        # TODO(hjensas): This should be parallelized
        for device in CONF.networking_baremetal.enabled_devices:
            # VLAN management is disabled for this device
            if not CONF[device].manage_vlans:
                continue
            # Skip device if not on physical network
            if not self._is_device_on_physnet(device, physical_network):
                continue

            driver = common.driver_mgr(device)
            driver.create_network(context)

    def update_network_precommit(self, context):
        """Update resources of a network.

        Update values of a network, updating the associated resources
        in the database. Called inside transaction context on session.
        Raising an exception will result in rollback of the
        transaction.
        update_network_precommit is called for all changes to the
        network state. It is up to the mechanism driver to ignore
        state or state changes that it does not know or care about.

        :param context: NetworkContext instance describing the new
            state of the network, as well as the original state prior
            to the update_network call.
        """
        pass

    def update_network_postcommit(self, context):
        """Update a network.

        Called after the transaction commits. Call can block, though
        will block the entire process so care should be taken to not
        drastically affect performance. Raising an exception will
        cause the deletion of the resource.
        update_network_postcommit is called for all changes to the
        network state.  It is up to the mechanism driver to ignore
        state or state changes that it does not know or care about.

        :param context: NetworkContext instance describing the new
            state of the network, as well as the original state prior
            to the update_network call.
        """
        network = context.current
        network_orig = context.original
        network_type = network[provider_net.NETWORK_TYPE]
        segmentation_id = network[provider_net.SEGMENTATION_ID]
        network_type_orig = network_orig[provider_net.NETWORK_TYPE]
        physical_network = network[provider_net.PHYSICAL_NETWORK]

        if (network_type != n_const.TYPE_VLAN
                and network_type_orig != n_const.TYPE_VLAN):
            return

        if not segmentation_id and not network_type_orig:
            return

        # TODO(hjensas): This should be parallelized
        for device in CONF.networking_baremetal.enabled_devices:
            # VLAN management is disabled for this device
            if not CONF[device].manage_vlans:
                continue
            # Skip device if not on physical network
            if not self._is_device_on_physnet(device, physical_network):
                continue

            driver = common.driver_mgr(device)
            driver.update_network(context)

    def delete_network_precommit(self, context):
        """Delete resources for a network.

        Delete network resources previously allocated by this
        mechanism driver for a network. Called inside transaction
        context on session. Runtime errors are not expected, but
        raising an exception will result in rollback of the
        transaction.

        :param context: NetworkContext instance describing the current
            state of the network, prior to the call to delete it.
        """
        pass

    def delete_network_postcommit(self, context):
        """Delete a network.

        Called after the transaction commits. Call can block, though
        will block the entire process so care should be taken to not
        drastically affect performance. Runtime errors are not
        expected, and will not prevent the resource from being
        deleted.

        :param context: NetworkContext instance describing the current
            state of the network, prior to the call to delete it.
        """
        network = context.current
        network_type = network[provider_net.NETWORK_TYPE]
        segmentation_id = network[provider_net.SEGMENTATION_ID]
        physical_network = network[provider_net.PHYSICAL_NETWORK]

        # If not VLAN network, or no segmentation_id - nothing to do.
        if network_type != n_const.TYPE_VLAN or not segmentation_id:
            return

        # TODO(hjensas): This should be parallelized
        for device in CONF.networking_baremetal.enabled_devices:
            # VLAN management is disabled for this device
            if not CONF[device].manage_vlans:
                continue
            # Skip device if not on physical network
            if not self._is_device_on_physnet(device, physical_network):
                continue

            driver = common.driver_mgr(device)
            driver.delete_network(context)

    def create_subnet_precommit(self, context):
        """Allocate resources for a new subnet.

        Create a new subnet, allocating resources as necessary in the
        database. Called inside transaction context on session. Call
        cannot block.  Raising an exception will result in a rollback
        of the current transaction.

        :param context: SubnetContext instance describing the new
            subnet.
        """
        pass

    def create_subnet_postcommit(self, context):
        """Create a subnet.

        Called after the transaction commits. Call can block, though
        will block the entire process so care should be taken to not
        drastically affect performance. Raising an exception will
        cause the deletion of the resource.

        :param context: SubnetContext instance describing the new
            subnet.
        """
        pass

    def update_subnet_precommit(self, context):
        """Update resources of a subnet.

        Update values of a subnet, updating the associated resources
        in the database. Called inside transaction context on session.
        Raising an exception will result in rollback of the
        transaction.
        update_subnet_precommit is called for all changes to the
        subnet state. It is up to the mechanism driver to ignore
        state or state changes that it does not know or care about.

        :param context: SubnetContext instance describing the new
            state of the subnet, as well as the original state prior
            to the update_subnet call.
        """
        pass

    def update_subnet_postcommit(self, context):
        """Update a subnet.

        Called after the transaction commits. Call can block, though
        will block the entire process so care should be taken to not
        drastically affect performance. Raising an exception will
        cause the deletion of the resource.
        update_subnet_postcommit is called for all changes to the
        subnet state.  It is up to the mechanism driver to ignore
        state or state changes that it does not know or care about.

        :param context: SubnetContext instance describing the new
            state of the subnet, as well as the original state prior
            to the update_subnet call.
        """
        pass

    def delete_subnet_precommit(self, context):
        """Delete resources for a subnet.

        Delete subnet resources previously allocated by this
        mechanism driver for a subnet. Called inside transaction
        context on session. Runtime errors are not expected, but
        raising an exception will result in rollback of the
        transaction.

        :param context: SubnetContext instance describing the current
            state of the subnet, prior to the call to delete it.
        """
        pass

    def delete_subnet_postcommit(self, context):
        """Delete a subnet.a

        Called after the transaction commits. Call can block, though
        will block the entire process so care should be taken to not
        drastically affect performance. Runtime errors are not
        expected, and will not prevent the resource from being
        deleted.

        :param context: SubnetContext instance describing the current
            state of the subnet, prior to the call to delete it.
        """
        pass

    def create_port_precommit(self, context):
        """Allocate resources for a new port.

        Create a new port, allocating resources as necessary in the
        database. Called inside transaction context on session. Call
        cannot block.  Raising an exception will result in a rollback
        of the current transaction.

        :param context: PortContext instance describing the port.
        """
        pass

    def create_port_postcommit(self, context):
        """Create a port.

        Called after the transaction completes. Call can block, though
        will block the entire process so care should be taken to not
        drastically affect performance.  Raising an exception will
        result in the deletion of the resource.

        :param context: PortContext instance describing the port.
        """
        pass

    def update_port_precommit(self, context):
        """Update resources of a port.

        Called inside transaction context on session to complete a
        port update as defined by this mechanism driver. Raising an
        exception will result in rollback of the transaction.
        update_port_precommit is called for all changes to the port
        state. It is up to the mechanism driver to ignore state or
        state changes that it does not know or care about.

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        """
        pass

    def update_port_postcommit(self, context):
        """Update a port.

        Called after the transaction completes. Call can block, though
        will block the entire process so care should be taken to not
        drastically affect performance.  Raising an exception will
        result in the deletion of the resource.
        update_port_postcommit is called for all changes to the port
        state. It is up to the mechanism driver to ignore state or
        state changes that it does not know or care about.

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        """
        port = context.current
        port_orig = context.original
        if self._is_bound(port):
            if port_orig:
                self._update_port(context)
            provisioning_blocks.provisioning_complete(
                context._plugin_context, port['id'], resources.PORT,
                BAREMETAL_DRV_ENTITY)
        elif self._is_bound(port_orig):
            # The port has been unbound. This will cause the local link
            # information to be lost, so remove the port from the network on
            # the switch now while we have the required information.
            self._unplug_port(context, current=False)

    def delete_port_precommit(self, context):
        """Delete resources of a port.

        Called inside transaction context on session. Runtime errors
        are not expected, but raising an exception will result in
        rollback of the transaction.

        :param context: PortContext instance describing the current
            state of the port, prior to the call to delete it.
        """
        pass

    def delete_port_postcommit(self, context):
        """Delete a port.

        state of the port, prior to the call to delete it.
        Called after the transaction completes. Call can block, though
        will block the entire process so care should be taken to not
        drastically affect performance.  Runtime errors are not
        expected, and will not prevent the resource from being
        deleted.

        :param context: PortContext instance describing the current
            state of the port, prior to the call to delete it.
        """
        self._unplug_port(context)

    def try_to_bind_segment_for_agent(self, context, segment, agent):
        """Try to bind with segment for agent.

        :param context: PortContext instance describing the port
        :param segment: segment dictionary describing segment to bind
        :param agent: agents_db entry describing agent to bind
        :returns: True iff segment has been bound for agent

        Neutron segments api-ref:
          https://docs.openstack.org/api-ref/network/v2/#segments

        Example segment dictionary: {'segmentation_id': 'segmentation_id',
                                     'network_type': 'network_type',
                                     'id': 'segment_uuid'}

        Called outside any transaction during bind_port() so that
        derived MechanismDrivers can use agent_db data along with
        built-in knowledge of the corresponding agent's capabilities
        to attempt to bind to the specified network segment for the
        agent.

        If the segment can be bound for the agent, this function must
        call context.set_binding() with appropriate values and then
        return True. Otherwise, it must return False.
        """
        if not self.check_segment_for_agent(segment, agent):
            return False

        port = context.current
        binding_profile = port[portbindings.PROFILE] or {}
        local_link_information = binding_profile.get(
            constants.LOCAL_LINK_INFO, [])
        local_group_information = binding_profile.get(
            constants.LOCAL_GROUP_INFO, {})
        bond_mode = local_group_information.get('bond_mode')

        by_device = {}
        for link in local_link_information:
            device = self._get_device(link)

            # If there is no device for all links the port will be bound, this
            # keeps backward compatibility.
            if not device:
                continue

            # Device was found, but no port_id in link - fail port binding
            if not link.get(constants.PORT_ID):
                LOG.warning('Cannot bind port %(port)s. no port_id in link '
                            'information: %(link)s', {'port': port[api.ID],
                                                      'link': link})
                return False

            # Check device on physnet, if not fail port binding
            if not self._is_device_on_physnet(device,
                                              segment[api.PHYSICAL_NETWORK]):
                LOG.warning(
                    'Cannot bind port %(port)s, device %(device)s is '
                    'not on physical network %(physnet)s',
                    {'port': port[api.ID], 'device': device,
                     'physnet': segment[api.PHYSICAL_NETWORK]})
                return False

            by_device.setdefault(device, {})
            by_device[device].setdefault('links', [])

            if 'driver' not in by_device[device]:
                # Load the driver, fail port binding on load error
                try:
                    driver = common.driver_mgr(device)
                    by_device[device]['driver'] = driver
                except exceptions.DriverEntrypointLoadError as e:
                    LOG.warning('Cannot bind port %(port)s, failed to load '
                                'driver for device %(device)s',
                                {'link': link, 'port': port[api.ID],
                                 'device': device})
                    LOG.debug(e.message)
                    return False

            by_device[device]['links'].append(link)

        if not by_device:
            # NOTE(vsaienko): we can call set_binding ONLY when we complete
            # binding for the port in the segment. We do not handle the port
            # and want to let other drivers to bind it.
            return False

        # Check driver(s) support the bond_mode - if not fail port binding
        if (bond_mode and by_device
                and not self._is_bond_mode_supported(bond_mode, by_device)):
            LOG.warning('Cannot bind port %(port)s, unsupported '
                        'bond_mode %(bond_mode)s',
                        {'port': port[api.ID], 'bond_mode': bond_mode})
            return False

        # Call each drivers create_port method to plug the device links
        for device, args in by_device.items():
            driver = args['driver']
            driver.create_port(context, segment, args['links'])

        # Complete the port binding
        provisioning_blocks.add_provisioning_component(
            context._plugin_context, port[api.ID], resources.PORT,
            BAREMETAL_DRV_ENTITY)
        context.set_binding(segment[api.ID],
                            self.get_vif_type(context, agent, segment),
                            self.get_vif_details(context, agent, segment))

        return True

    def _is_bound(self, context):
        """Check if port is currently bound by this driver

        :param context: Port context
        :returns: True/False
        """
        return (context[portbindings.VNIC_TYPE] in self.supported_vnic_types
                and context[portbindings.VIF_TYPE] == self.vif_type)

    @staticmethod
    def _is_device_on_physnet(device, physical_network):
        """Check if Device is connected to physical network

        If the device is not configured to any physical networks, return
        True so that all networks are created on the switch.

        :param device: Netconf device in config
        :param physical_network: Physical network
        :returns: True or False
        """
        if (CONF[device].physical_networks
                and physical_network not in CONF[device].physical_networks):
            return False

        return True

    def _get_device(self, link):
        """Get device identifier from link information

        :param link: Link information
        :returns: Device identifier (switch_id or switch_info)
        """
        device = None
        switch_id = link.get(constants.SWITCH_ID)
        switch_info = link.get(constants.SWITCH_INFO)
        if switch_id and switch_id in self.devices:
            device = self.devices[switch_id]
        elif switch_info and switch_info in self.devices:
            device = self.devices[switch_info]

        return device

    def _update_port(self, context):
        """Update port

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        """
        port = context.current
        binding_profile = port[portbindings.PROFILE]
        local_link_information = binding_profile.get(
            constants.LOCAL_LINK_INFO, [])
        local_group_information = binding_profile.get(
            constants.LOCAL_GROUP_INFO, {})
        bond_mode = local_group_information.get('bond_mode')
        by_device = {}
        for link in local_link_information:
            device = self._get_device(link)
            # No device == noop
            if not device:
                continue

            by_device.setdefault(device, {})
            by_device[device].setdefault('links', [])

            if 'driver' not in by_device[device]:
                # Load the driver, if this fails the link cannot be updated
                try:
                    driver = common.driver_mgr(device)
                    by_device[device]['driver'] = driver
                except exceptions.DriverEntrypointLoadError as e:
                    LOG.warning('Cannot update link %(link)s on port '
                                '%(port)s, failed to load driver for device '
                                '%(device)s', {'link': link,
                                               'port': port[api.ID],
                                               'device': device})
                    LOG.debug(e.message)
                    continue

            by_device[device]['links'].append(link)

        # Check driver(s) support the bond_mode
        if (bond_mode and by_device
                and not self._is_bond_mode_supported(bond_mode, by_device)):
            LOG.error('Cannot update port %(port)s on device, unsupported '
                      'bond_mode %(bond_mode)s', {'port': port[api.ID],
                                                  'bond_mode': bond_mode})
            return

        # Call each drivers update_port method
        for device, args in by_device.items():
            driver = args['driver']
            driver.update_port(context, args['links'])

    def _unplug_port(self, context, current=True):
        """Unplug/Unbind/Delete port

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param current: Boolean, when true use context.current, when
            false use context.original
        """
        if current:
            port = context.current
        else:
            port = context.original

        binding_profile = port[portbindings.PROFILE] or {}
        local_link_information = binding_profile.get(
            constants.LOCAL_LINK_INFO, [])
        local_group_information = binding_profile.get(
            constants.LOCAL_GROUP_INFO, {})
        bond_mode = local_group_information.get('bond_mode')

        by_device = {}
        for link in local_link_information:
            device = self._get_device(link)
            # No device == noop
            if not device:
                continue

            by_device.setdefault(device, {})
            by_device[device].setdefault('links', [])

            if 'driver' not in by_device[device]:
                # Load the driver, if this fails the link cannot be unbound
                try:
                    driver = common.driver_mgr(device)
                    by_device[device]['driver'] = driver
                except exceptions.DriverEntrypointLoadError as e:
                    LOG.warning('Cannot delete link %(link)s for port '
                                '%(port)s, failed to load driver for device '
                                '%(device)s', {'link': link,
                                               'port': port[api.ID],
                                               'device': device})
                    LOG.debug(e.message)
                    continue

            by_device[device]['links'].append(link)

        # Check driver(s) support the bond_mode
        if (bond_mode and by_device
                and not self._is_bond_mode_supported(bond_mode, by_device)):
            LOG.warning('Cannot delete port %(port)s on device, unsupported '
                        'bond_mode %(bond_mode)s', {'port': port[api.ID],
                                                    'bond_mode': bond_mode})
            return

        # Call each drivers delete_port method to unplug the device links
        for device, args in by_device.items():
            driver = args['driver']
            driver.delete_port(context, args['links'], current=current)

    @staticmethod
    def _is_bond_mode_supported(bond_mode, by_device):
        """Check if drivers support the bond mode

        :param bond_mode: The bond mode
        :param by_device: Dictionary of driver and links per-device.
        """
        for device, args in by_device.items():
            driver = args['driver']

            if bond_mode and bond_mode not in driver.SUPPORTED_BOND_MODES:
                return False

        return True
