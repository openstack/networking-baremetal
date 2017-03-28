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
from neutron.plugins.common import constants as p_const
from neutron_lib.api.definitions import portbindings
from neutron_lib.callbacks import resources
from neutron_lib.plugins.ml2 import api
from oslo_log import log as logging

LOG = logging.getLogger(__name__)

BAREMETAL_DRV_ENTITY = 'BAREMETAL_DRV_ENTITIY'


class BaremetalMechanismDriver(api.MechanismDriver):

    def initialize(self):
        """Perform driver initialization.

        Called after all drivers have been loaded and the database has
        been initialized. No abstract methods defined below will be
        called prior to this method being called.

        """
        self.supported_vnic_types = [portbindings.VNIC_BAREMETAL]
        self.supported_network_types = [p_const.TYPE_FLAT]
        self.vif_type = portbindings.VIF_TYPE_OTHER
        self.vif_details = {}

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

    def bind_port(self, context):
        """Attempt to bind a port.

        This method is called outside any transaction to attempt to
        establish a port binding using this mechanism driver. Bindings
        may be created at each of multiple levels of a hierarchical
        network, and are established from the top level downward. At
        each level, the mechanism driver determines whether it can
        bind to any of the network segments in the
        context.segments_to_bind property, based on the value of the
        context.host property, any relevant port or network
        attributes, and its own knowledge of the network topology. At
        the top level, context.segments_to_bind contains the static
        segments of the port's network. At each lower level of
        binding, it contains static or dynamic segments supplied by
        the driver that bound at the level above. If the driver is
        able to complete the binding of the port to any segment in
        context.segments_to_bind, it must call context.set_binding
        with the binding details. If it can partially bind the port,
        it must call context.continue_binding with the network
        segments to be used to bind at the next lower level.
        If the binding results are committed after bind_port returns,
        they will be seen by all mechanism drivers as
        update_port_precommit and update_port_postcommit calls. But if
        some other thread or process concurrently binds or updates the
        port, these binding results will not be committed, and
        update_port_precommit and update_port_postcommit will not be
        called on the mechanism drivers with these results. Because
        binding results can be discarded rather than committed,
        drivers should avoid making persistent state changes in
        bind_port, or else must ensure that such state changes are
        eventually cleaned up.
        Implementing this method explicitly declares the mechanism
        driver as having the intention to bind ports. This is inspected
        by the QoS service to identify the available QoS rules you
        can use with ports.

        :param context: PortContext instance describing the port
        """
        port = context.current
        LOG.debug("Binding port: %s" % port['id'])
        for segment in context.segments_to_bind:
            if segment[api.NETWORK_TYPE] not in self.supported_network_types:
                continue

            vnic_type = port[portbindings.VNIC_TYPE]
            if vnic_type not in self.supported_vnic_types:
                continue
            # NOTE(vsaienko): Set baremetal port as bound if network
            # is flat to allow Neutron to move it to ACTIVE state.
            # In flat network ports are pre-plugged to specific network by
            # administrator as we do not pass any connection information
            # from Ironic to Neutron
            if segment[api.NETWORK_TYPE] == p_const.TYPE_FLAT:
                provisioning_blocks.add_provisioning_component(
                    context._plugin_context, port['id'], resources.PORT,
                    BAREMETAL_DRV_ENTITY)
                context.set_binding(segment[api.ID],
                                    self.vif_type,
                                    self.vif_details)
                LOG.info("Successfully bound port %(port_id)s in segment "
                         "%(segment_id)s", {'port_id': port['id'],
                                            'segment_id': segment['id']})
