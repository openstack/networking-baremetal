====================================
L2VNI Mechanism Driver Configuration
====================================

Overview
========

The L2VNI (Layer 2 Virtual Network Identifier) mechanism driver enables
baremetal servers to connect to VXLAN and Geneve overlay networks by
dynamically creating VLAN segments that bridge the overlay network to the
physical network infrastructure.

This driver is essential for deployments where baremetal nodes need to
participate in tenant overlay networks alongside virtual machines.

Architecture
============

How it Works
------------

The L2VNI mechanism driver operates as follows:

1. **Overlay Network Detection**: When a baremetal port is bound to a VXLAN or
   Geneve network, the driver is triggered.

2. **Dynamic VLAN Allocation**: The driver allocates a dynamic VLAN segment on
   the specified physical network to carry traffic for the overlay network.

3. **OVN Localnet Port Creation**: If OVN is the backend, the driver creates a
   localnet port in OVN to bridge the overlay network to the physical VLAN.

4. **Port Binding**: The driver instructs Neutron to continue binding the port
   using the dynamically allocated VLAN segment.

5. **Traffic Flow**: Traffic flows from the overlay network (VXLAN/Geneve)
   through the localnet port to the VLAN segment, then to the baremetal server.

.. code-block:: text

    ┌──────────────┐
    │  Baremetal   │
    │   Server     │
    └──────┬───────┘
           │ VLAN 100
           │
    ┌──────▼───────┐
    │   Physical   │
    │   Switch     │
    └──────┬───────┘
           │ VLAN 100
           │
    ┌──────▼────────────────┐
    │  OVN Localnet Port    │
    │  (bridges VLAN↔VXLAN) │
    └──────┬────────────────┘
           │
    ┌──────▼───────┐
    │ VXLAN/Geneve │
    │   Overlay    │
    └──────────────┘

Switch Management Integration
==============================

The L2VNI mechanism driver works in conjunction with switch management plugins
(such as genericswitch) to provide complete end-to-end connectivity for
baremetal servers on overlay networks.

Role of Switch Management Plugins
----------------------------------

Switch management plugins handle the crucial task of configuring physical
network switches to map VNI (VXLAN/Geneve Network Identifier) values to VLAN
tags on the physical ports where baremetal servers connect.

When a baremetal port is created or deleted, the following workflow occurs:

1. **L2VNI Driver** (this driver):

   - Allocates a dynamic VLAN segment for the overlay network
   - Creates an OVN localnet port to bridge overlay ↔ VLAN
   - Continues the port binding process

2. **Switch Management Plugin** (e.g., genericswitch):

   - Configures the physical switch to map the VLAN to the server's port
   - This is the **final step** in port binding
   - Must be listed **last** in mechanism_drivers

Mechanism Driver Ordering
--------------------------

The order of mechanism drivers in ``ml2_conf.ini`` is critical:

.. code-block:: ini

   [ml2]
   # CORRECT ORDER - switch management MUST be last
   mechanism_drivers = ovn,baremetal_l2vni,baremetal,genericswitch

   # INCORRECT - will break port binding
   mechanism_drivers = ovn,genericswitch,baremetal_l2vni  # WRONG!

**Why order matters:**

- OVN provides the overlay network backend
- baremetal_l2vni allocates the VLAN and creates localnet ports
- baremetal handles standard baremetal port binding
- genericswitch (or other switch management) performs the **final** switch
  configuration step

If the switch management plugin runs too early, it won't have the correct VLAN
information allocated by baremetal_l2vni, causing port binding to fail.

Requirements
============

- OpenStack Neutron with ML2 plugin
- OVN (Open Virtual Network) backend (**required** - this driver requires OVN)
- Physical network switches configured for VLAN trunking
- Switch management ML2 plugin (e.g., genericswitch) for VNI↔VLAN mapping
- Baremetal nodes with appropriate VLAN configuration

Configuration
=============

Enabling the Driver
-------------------

Edit ``/etc/neutron/plugins/ml2/ml2_conf.ini`` and add ``baremetal_l2vni`` to
the list of mechanism drivers:

.. code-block:: ini

   [ml2]
   mechanism_drivers = ovn,baremetal_l2vni,baremetal,genericswitch

.. important::
   **Driver order is critical:**

   - ``ovn`` must be first (provides overlay network backend)
   - ``baremetal_l2vni`` allocates VLANs and creates localnet ports
   - ``baremetal`` handles standard baremetal port binding
   - ``genericswitch`` (or other switch management) must be **last** to
     perform final switch configuration

Configuration Options
---------------------

Add a ``[baremetal_l2vni]`` section to your configuration file:

.. note::
   A complete configuration example is available at
   :download:`l2vni-example.ini <l2vni-example.ini>`

.. code-block:: ini

   [baremetal_l2vni]
   # Enable automatic creation of OVN localnet ports (default: True)
   create_localnet_ports = True

   # Default physical network for baremetal ports (optional)
   # If not set, ports must specify physical_network in binding profile
   default_physical_network = physnet1

Configuration Parameters
~~~~~~~~~~~~~~~~~~~~~~~~

``create_localnet_ports``
    **Type**: Boolean

    **Default**: ``True``

    **Description**: Automatically create OVN localnet ports to bridge
    VXLAN/Geneve overlay networks to physical networks.

    **When to use True (default):**

    - Direct VLAN-to-VXLAN fabric attachment scenarios
    - When using ML2 plugin for direct attachment to a VLAN-to-VXLAN fabric
    - The OVN localnet ports enable the overlay↔physical network translation

    **When to use False:**

    - Pure EVPN deployments where Neutron is responsible for ensuring
      attachment to the remote network infrastructure through tunnels rather
      than through localnet ports in OVN
    - When localnet ports are managed externally

    .. note::
       If you're using EVPN where network attachment is handled via tunnels,
       you likely want to set this to ``False`` since localnet ports are not
       needed for that architecture.

``default_physical_network``
    **Type**: String

    **Default**: ``None``

    **Description**: Default physical network name to use for baremetal L2VNI
    bindings when the port binding profile does not specify a
    ``physical_network``. If not set and the port lacks ``physical_network``
    in its binding profile, port binding will fail.

Port Binding Profile
--------------------

When creating baremetal ports, you can specify the physical network in the
binding profile:

.. code-block:: bash

   openstack port create \
     --network overlay-network \
     --vnic-type baremetal \
     --binding-profile physical_network=physnet1 \
     baremetal-port

If ``default_physical_network`` is configured, the binding profile is optional.

Network Configuration
=====================

Physical Networks
-----------------

Ensure your physical networks are properly configured in ML2:

.. code-block:: ini

   [ml2_type_vlan]
   network_vlan_ranges = physnet1:100:200

On each chassis (compute/network node), configure OVN bridge mappings:

.. code-block:: bash

   ovs-vsctl set Open_vSwitch . \
     external-ids:ovn-bridge-mappings=physnet1:br-provider

Router Configuration
--------------------

When baremetal networks are attached to Neutron routers, ensure the router has
an external gateway configured for proper routing behavior. The driver
automatically configures router gateway chassis bindings when necessary.

Deployment Guide
================

Step 1: Enable the Mechanism Driver
------------------------------------

Edit ``/etc/neutron/plugins/ml2/ml2_conf.ini``:

.. code-block:: ini

   [ml2]
   mechanism_drivers = ovn,baremetal_l2vni,baremetal,genericswitch
   type_drivers = flat,vlan,vxlan,geneve
   tenant_network_types = vxlan

   [baremetal_l2vni]
   create_localnet_ports = True
   default_physical_network = physnet1

.. important::
   Ensure mechanism drivers are in the correct order: OVN, baremetal_l2vni,
   baremetal, genericswitch (or other switch management plugin last).

Step 2: Configure Physical Networks
------------------------------------

Ensure VLAN ranges are configured:

.. code-block:: ini

   [ml2_type_vlan]
   network_vlan_ranges = physnet1:100:200

Step 3: Configure OVN Bridge Mappings
--------------------------------------

On each chassis that will handle baremetal traffic:

.. code-block:: bash

   ovs-vsctl set Open_vSwitch . \
     external-ids:ovn-bridge-mappings=physnet1:br-provider

Step 4: Restart Neutron Server
-------------------------------

.. code-block:: bash

   systemctl restart neutron-server

Step 5: Create Overlay Network
-------------------------------

Create a tenant overlay network. You must explicitly specify the network type
as VXLAN or Geneve (the only supported types for this driver):

.. code-block:: bash

   openstack network create \
     overlay-network

   openstack subnet create \
     --network overlay-network \
     --subnet-range 192.168.100.0/24 \
     overlay-subnet

.. warning::
   **Do not use provider networks** (``--provider-physical-network``,
   with this driver. Provider networks are intended to be pre-configured
   for direct attachment, where as this model and interaciton requires
   additional confiuration and actions to occur.

.. note::
   Only VXLAN and Geneve network types are supported. If your default network
   type is configured to something else (e.g., VLAN or flat), then this
   plugin will not work as intended.

Step 6: Create Baremetal Port
------------------------------

Create a baremetal port on the overlay network:

.. NOTE::
   This step is intended for manually triggering the binding logic
   which demonstrates the mechanism driver creating lower binding
   segment. In normal usage flow of this
   plugin, Ironic manages the binding profile and vnic type attributes
   of ports.

.. code-block:: bash

   openstack port create \
     --network overlay-network \
     --vnic-type baremetal \
     --binding-profile physical_network=physnet1 \
     baremetal-port

The driver will automatically:

- Allocate a dynamic VLAN segment (e.g., VLAN 150) on physnet1
- Create an OVN localnet port to bridge VXLAN ↔ VLAN
- Bind the port using the VLAN segment

Troubleshooting
===============

Port Binding Fails
-------------------

**Symptom**: Port remains in ``DOWN`` state or binding fails.

**Possible Causes**:

1. **Missing physical_network**: Port binding profile doesn't specify
   ``physical_network`` and no ``default_physical_network`` is configured.

   **Solution**: Either specify physical_network in binding profile or
   configure ``default_physical_network``.

2. **Physical network not found**: No chassis has the specified physical
   network in bridge-mappings.

   **Solution**: Check logs for error message and verify OVN bridge-mappings
   configuration on all chassis.

3. **VLAN exhaustion**: No available VLANs in the configured range.

   **Solution**: Expand VLAN range in ``ml2_type_vlan`` configuration.

Localnet Port Not Created
--------------------------

**Symptom**: Port binds but traffic doesn't flow.

**Possible Causes**:

1. **Localnet creation disabled**: ``create_localnet_ports = False``

   **Solution**: Set ``create_localnet_ports = True`` or manage localnet
   ports externally.

2. **OVN not available**: Driver cannot connect to OVN.

   **Solution**: Check Neutron logs for OVN connection errors. Verify OVN
   mechanism driver is loaded.

3. **Chassis without physnet**: No chassis has the physical network
   configured.

   **Solution**: Configure ``ovn-bridge-mappings`` on at least one chassis.

Router Attachment Breaks Connectivity
--------------------------------------

**Symptom**: Adding a router to the network breaks baremetal connectivity.

**Possible Causes**:

1. **Router without external gateway**: Router has no gateway port, causing
   OVN to remove external port bindings.

   **Solution**: Configure an external gateway for the router, or ensure the
   gateway interface is up.

2. **Gateway chassis mismatch**: Router gateway is on a different chassis than
   the localnet port.

   **Solution**: The driver handles this automatically. Check logs for gateway
   chassis binding messages.

Checking Logs
-------------

Enable debug logging for detailed information:

.. code-block:: ini

   [DEFAULT]
   debug = True

Check Neutron server logs:

.. code-block:: bash

   journalctl -u neutron-server -f

Look for messages containing:

- ``L2vniMechanismDriver`` - General driver operations
- ``localnet port`` - Localnet port creation/deletion
- ``physical_network`` - Physical network validation
- ``allocate dynamic segment`` - VLAN segment allocation

Verifying OVN State
-------------------

Check OVN Northbound database:

.. code-block:: bash

   # List logical switches and ports
   ovn-nbctl show

   # Look for localnet ports (format: neutron-<network-id>-localnet-<physnet>)
   ovn-nbctl list Logical_Switch_Port | grep localnet

Check OVN Southbound database:

.. code-block:: bash

   # List chassis and their bridge-mappings
   ovn-sbctl list Chassis

   # Check port bindings
   ovn-sbctl list Port_Binding

Advanced Topics
===============

Multiple Physical Networks
---------------------------

You can use different physical networks for different ports:

.. code-block:: bash

   openstack port create \
     --network overlay-network \
     --vnic-type baremetal \
     --binding-profile physical_network=physnet1 \
     port-on-physnet1

   openstack port create \
     --network overlay-network \
     --vnic-type baremetal \
     --binding-profile physical_network=physnet2 \
     port-on-physnet2

The driver will create separate VLAN segments and localnet ports for each
physical network.

VLAN Segment Reuse
------------------

The driver is idempotent - if a VLAN segment already exists for a given
overlay network + physical network combination, it will reuse the existing
segment rather than allocating a new one.

Segment Cleanup
---------------

When the last baremetal port using a dynamic VLAN segment is deleted or
unbound, the driver automatically:

1. Removes the OVN localnet port
2. Releases the dynamic VLAN segment back to the pool

Performance Considerations
==========================

VLAN Pool Sizing
----------------

Plan your VLAN ranges carefully. Each overlay network that has baremetal ports
on a given physical network requires one VLAN from the pool.

For example, with 100 tenant overlay networks and baremetal nodes on 2
physical networks, you need up to 200 VLANs.

OVN Database Load
-----------------

The driver queries the OVN Southbound database to validate physical network
availability. In very large deployments (1000+ chassis), this query may add
latency to port binding operations.

See Also
========

* :doc:`/configuration/ml2/index` - ML2 Plugin Configuration
* :doc:`/contributor/index` - Contributing Guide
* OpenStack Neutron Documentation: https://docs.openstack.org/neutron/
* OVN Documentation: https://www.ovn.org/
