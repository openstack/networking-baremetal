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
from neutron_lib.callbacks import resources
from neutron_lib import constants as n_const
from neutron_lib.plugins.ml2 import api
from oslo_log import log as logging

from networking_baremetal import constants

LOG = logging.getLogger(__name__)

BAREMETAL_DRV_ENTITY = 'BAREMETAL_DRV_ENTITIY'


class BaremetalMechanismDriver(mech_agent.SimpleAgentMechanismDriverBase):

    def __init__(self):
        super(BaremetalMechanismDriver, self).__init__(
            constants.BAREMETAL_AGENT_TYPE,
            portbindings.VIF_TYPE_OTHER,
            {},
            supported_vnic_types=[portbindings.VNIC_BAREMETAL])

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
        pass

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
        pass

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
        pass

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
        if (port[portbindings.VNIC_TYPE] in self.supported_vnic_types and
                port[portbindings.VIF_TYPE] == self.vif_type):
            provisioning_blocks.provisioning_complete(
                context._plugin_context, port['id'], resources.PORT,
                BAREMETAL_DRV_ENTITY)

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
        pass

    def try_to_bind_segment_for_agent(self, context, segment, agent):
        """Try to bind with segment for agent.

        :param context: PortContext instance describing the port
        :param segment: segment dictionary describing segment to bind
        :param agent: agents_db entry describing agent to bind
        :returns: True iff segment has been bound for agent

        Neutron segments api-ref:
          https://developer.openstack.org/api-ref/network/v2/#segments

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
        if self.check_segment_for_agent(segment, agent):
            port = context.current
            provisioning_blocks.add_provisioning_component(
                context._plugin_context, port['id'], resources.PORT,
                BAREMETAL_DRV_ENTITY)
            context.set_binding(segment[api.ID],
                                self.get_vif_type(context, agent, segment),
                                self.get_vif_details(context, agent, segment))
            return True
        else:
            return False
