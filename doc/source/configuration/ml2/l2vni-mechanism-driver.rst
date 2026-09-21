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

User Workflow
=============

This document is written for the operator deploying and configuring the
driver. The end user of the resulting capability is typically a *tenant*
following a self-service workflow: the tenant creates a VXLAN or Geneve
overlay network, then attaches a baremetal instance to it. Ironic and Nova
set the ``vnic-type`` and populate the binding profile on the tenant's
behalf, which is what triggers this driver — the tenant does not create
ports or supply binding profiles manually.

The tenant-facing workflow is documented in Ironic and is not duplicated
here:

* Ironic VXLAN administration guide — connectivity models, network
  creation, and attaching baremetal nodes:
  https://docs.openstack.org/ironic/latest/admin/vxlan.html
* Ironic deploy / user guide — the overall deploy workflow:
  https://docs.openstack.org/ironic/latest/user/deploy.html

The manual ``openstack port create`` steps shown later in this document are a
demonstration and troubleshooting aid for exercising the binding logic
directly; they are not part of the normal tenant workflow.

Deployment Models
=================

The overlay VNI can reach a baremetal server in two ways, distinguished by
*which device terminates the VXLAN/Geneve tunnel* — that is, which device
acts as the VTEP.

**Model 1 — Switch-fabric VXLAN**
    The default (``create_localnet_ports = True``). The physical switches
    are the VTEPs. The L2VNI driver allocates a dynamic VLAN per overlay
    network on the baremetal-facing leaf; a localnet port is used together
    with the switch management plugin (e.g. networking-generic-switch) to
    bind through to the physical switch port, and the plugin programs a
    ``VLAN ↔ VNI`` mapping on that leaf using the ``vni`` from the port
    binding profile. The switch encapsulates the traffic, and the VXLAN
    tunnel is carried *across the fabric, between switches*, to the far VTEP
    — typically the leaf where the OVN network nodes (gateway chassis)
    attach. Each leaf maps its own local VLAN to the *same* VNI, and the
    fabric underlay stitches the two ends together. This intermediary,
    switch-to-switch VXLAN segment is the data path shown in
    `Switch-fabric VXLAN data path`_ below.

**Model 2 — OVN-native VXLAN between peers**
    Set ``create_localnet_ports = False``. OVN terminates the tunnels itself
    and moves traffic directly between its VXLAN peers, without the physical
    switches bridging ``VLAN ↔ VNI`` or carrying VXLAN. There is no localnet
    interface and no physical-port hand-off in this model — OVN simply
    handles it between peers.

.. important::
   **Current support status (as of the 2026.2 release):** Model 1 is the
   model in use. Neutron has landed *pure-IP* (routed / L3) VXLAN, but not
   the *MAC* (L2 bridging) VXLAN that Model 2 needs for OVN to bridge
   baremetal L2 endpoints directly between VXLAN peers. Until that support
   lands, OVN hands off to its local leaf and the switches themselves
   perform the ``VLAN ↔ VNI`` bridging and carry the VXLAN between one
   another.

   Plan for the switch-fabric data path (Model 1) shown below. Model 2 is
   described here so the intended end state is clear, but it is not yet a
   complete option for L2 baremetal attachment.

Switch-fabric VXLAN data path
-----------------------------

In Model 1 the VNI travels between two switches. The driver allocates one
VLAN per overlay network per physical network and reuses it for every port
on that network, so the *same* VLAN ID appears on both ends: the
baremetal-facing leaf maps that VLAN to the VNI, and the far leaf maps the
same VNI back to that same VLAN, which the OVN network node (gateway chassis)
trunks. The VXLAN tunnel between the two leaves is the intermediary segment —
neither the baremetal server nor OVN ever sees it directly.

.. code-block:: text

    ┌──────────────────┐                            ┌──────────────────┐
    │    Baremetal     │                            │   OVN network    │
    │     server       │                            │  node (gateway)  │
    └─────────┬────────┘                            └─────────┬────────┘
              │ access VLAN 100                trunk VLAN 100 │
    ┌─────────▼────────┐                            ┌─────────▼────────┐
    │ Baremetal-facing │                            │  Network-node    │
    │  leaf   (VTEP)   │       VXLAN VNI 5000       │  leaf   (VTEP)   │
    │ VLAN 100 ↔ VNI   │◀──────────────────────────▶│ VNI 5000 ↔ VLAN  │
    │      5000        │  (fabric underlay/spines)  │      100         │
    └──────────────────┘                            └──────────────────┘

The ``vni`` value that both leaves must agree on is written into the port
binding profile by the L2VNI machinery: the mechanism driver supplies it on
baremetal ports, and the trunk reconciliation agent supplies it on the
network-node trunk subports. See
:doc:`/admin/l2vni-trunk-reconciliation` for the network-node side.

Cross-connect VLAN pool
-----------------------

The dynamic VLAN segments this driver allocates are *internal
cross-connects*: they stitch the baremetal server and the OVN side to the
switch, where the ``VLAN ↔ VNI`` mapping happens. They carry no tenant
meaning of their own and must not be confused with tenant or provider VLANs.

Before enabling the driver, reserve a **dedicated VLAN range** for these
cross-connects on the physical network used for baremetal service
interactions. In the examples throughout this document that network is
``physnet1``:

.. code-block:: ini

   [ml2_type_vlan]
   # physnet1 is the physical network used for baremetal service
   # interactions. Reserve a wide, dedicated range for cross-connects.
   network_vlan_ranges = physnet1:100:1100

Planning notes:

- **Dedicate the range.** It must not overlap VLANs used for tenant provider
  networks, management, or anything else on ``physnet1``. The driver
  allocates from it dynamically.
- **Size it generously.** One VLAN is consumed per overlay network that has
  baremetal ports on the physical network, so the pool bounds how many such
  overlay networks can exist at once. A range of roughly a thousand VLANs is
  a reasonable starting point for most deployments; size as
  ``overlay_networks × physical_networks`` and see `VLAN Pool Sizing`_ for
  the full calculation.
- **One VLAN per overlay network, reused at both ends.** Within a physical
  network the driver allocates a single VLAN segment per overlay network and
  reuses it for every port on that network — the baremetal port and the OVN
  network-node trunk subport share the *same* VLAN ID, both mapped to the
  same VNI by their respective leaves.
- **The VLAN is not passed natively across the fabric.** Each cross-connect
  VLAN is *bound to the VXLAN network* at the leaf; the switch maps it to the
  VNI, and the VXLAN tunnel carries it to the far leaf. You therefore do not
  need to trunk these VLANs across the fabric underlay — they only need to be
  configured on the local leaf ports where the ``VLAN ↔ VNI`` binding occurs
  (the baremetal-facing leaf and the network-node leaf).
- **Multiple physical networks.** Networker nodes may serve several distinct
  physnets. A physnet is a human structural construct — a label used to
  model a slice of the network — so VLAN ID uniqueness is enforced by the
  physical switch, not by the physnet. Two physnets that ride the same
  underlying leaf switch must use non-overlapping VLAN ranges. Conversely,
  the same VLAN range may be reused across physnets — even mingling onto a
  common overlay network — as long as those physnets are not serviced by the
  same physical leaf switches.

.. tip::
   **Operational recommendations.**

   - Deploy multiple networker nodes for high availability and to spread the
     cross-connect traffic. See
     :doc:`/admin/l2vni-trunk-reconciliation` for the agent-side HA model.
   - Monitor **VLAN pool utilization** (segments allocated versus the size of
     the configured range) so the range can be grown before it is exhausted.
   - Monitor **traffic utilization on the physnet interfaces** of the
     networker nodes passing traffic to the underlying network. Those
     interfaces aggregate the overlay traffic for their physnet and can
     become a bottleneck as usage grows.

Architecture
============

.. note::
   The walkthrough below covers the per-port mechanics — dynamic VLAN
   allocation and the OVN localnet port — used by the switch-fabric model
   (Model 1). See `Deployment Models`_ above for how the VNI then traverses
   the fabric between switches.

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
    │  Neutron's   │
    │ VXLAN/Geneve │
    │   Overlay    │
    └──────────────┘

Switch Management Integration
=============================

The L2VNI mechanism driver works in conjunction with switch management plugins
(such as genericswitch) to provide complete end-to-end connectivity for
baremetal servers on overlay networks.

Role of Switch Management Plugins
---------------------------------

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
   - Performs the **final** hierarchical bind of the dynamic VLAN segment
   - Must be listed **after** ``baremetal-l2vni`` in mechanism_drivers

Mechanism Driver Ordering
-------------------------

The order of mechanism drivers in ``ml2_conf.ini`` is critical:

.. code-block:: ini

   [ml2]
   # CORRECT ORDER
   mechanism_drivers = ovn,baremetal-l2vni,genericswitch,baremetal

   # INCORRECT - will break port binding
   mechanism_drivers = ovn,genericswitch,baremetal-l2vni  # WRONG!

**Why order matters:**

- ``ovn`` provides the overlay network backend and must be first
- ``baremetal-l2vni`` must come **after** ``ovn`` so an overlay segment
  exists to bind against; it allocates the dynamic VLAN and creates the
  localnet port
- ``baremetal-l2vni`` must come **before** both ``baremetal`` and the switch
  management plugin, since they act on the VLAN segment it allocates
- the switch management plugin (e.g. ``genericswitch``) performs the final
  hierarchical bind of that VLAN segment; on overlay networks the
  ``baremetal`` driver defers, so it may follow the switch driver

If the switch management plugin runs before ``baremetal-l2vni``, it won't have
the VLAN segment this driver allocates, and port binding will fail. This
ordering matches the one documented for Ironic; see `See Also`_.

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

Edit ``/etc/neutron/plugins/ml2/ml2_conf.ini`` and add ``baremetal-l2vni`` to
the list of mechanism drivers:

.. code-block:: ini

   [ml2]
   mechanism_drivers = ovn,baremetal-l2vni,genericswitch,baremetal

.. important::
   **Driver order is critical:**

   - ``ovn`` must be first (provides overlay network backend)
   - ``baremetal-l2vni`` allocates VLANs and creates localnet ports, and must
     come after ``ovn`` but before ``baremetal`` and the switch driver
   - ``genericswitch`` (or other switch management) performs the final
     hierarchical bind of the VLAN segment
   - ``baremetal`` handles standard baremetal port binding; on overlay
     networks it defers to the switch driver

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

    .. note::
       See `Deployment Models`_ for how this option maps to the
       switch-fabric (``True``) and OVN-native (``False``) data paths, and
       for the current support status of each.

``default_physical_network``
    **Type**: String

    **Default**: ``None``

    **Description**: Default physical network name to use for baremetal L2VNI
    bindings when the port binding profile does not specify a
    ``physical_network``. If not set and the port lacks ``physical_network``
    in its binding profile, port binding will fail.

``l2vni_subport_anchor_network``
    **Type**: String

    **Default**: ``l2vni-subport-anchor``

    **Section**: ``[l2vni]`` (shared with the L2VNI trunk agent)

    **Description**: Name of the shared subport anchor network. The mechanism
    driver reads this value to identify the anchor network and **skip port
    binding** for ports on it — anchor-network ports are metadata only (they
    exist to be added to trunks and trigger switch management callbacks) and
    do not require hierarchical binding or actual network connectivity.

    .. note::
       This option lives in the ``[l2vni]`` section (not ``[baremetal_l2vni]``)
       because it is shared with the L2VNI trunk reconciliation agent; the
       mechanism driver and the agent must be configured with the same value.
       For the full option reference — including
       ``l2vni_subport_anchor_network_type`` and ``l2vni_auto_create_networks``
       — see :doc:`/admin/l2vni-trunk-reconciliation`.

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
   network_vlan_ranges = physnet1:100:1100

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
-----------------------------------

Edit ``/etc/neutron/plugins/ml2/ml2_conf.ini``:

.. code-block:: ini

   [ml2]
   mechanism_drivers = ovn,baremetal-l2vni,genericswitch,baremetal
   type_drivers = flat,vlan,vxlan,geneve
   project_network_types = vxlan

   [baremetal_l2vni]
   create_localnet_ports = True
   default_physical_network = physnet1

.. important::
   Ensure mechanism drivers are in the correct order: ``ovn``,
   ``baremetal-l2vni``, then the switch management plugin (e.g.
   ``genericswitch``) and ``baremetal``.

Step 2: Configure Physical Networks
------------------------------------

Ensure VLAN ranges are configured:

.. code-block:: ini

   [ml2_type_vlan]
   network_vlan_ranges = physnet1:100:1100

Step 3: Configure OVN Bridge Mappings
-------------------------------------

On each chassis that will handle baremetal traffic:

.. code-block:: bash

   ovs-vsctl set Open_vSwitch . \
     external-ids:ovn-bridge-mappings=physnet1:br-provider

Step 4: Restart Neutron Server
------------------------------

.. code-block:: bash

   systemctl restart neutron-server

Step 5: Create Overlay Network
------------------------------

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
   **Do not use provider networks** (e.g. ``--provider-physical-network``)
   with this driver. Provider networks are intended to be pre-configured
   for direct attachment, whereas this model and interaction require
   additional configuration and actions to occur.

.. note::
   Only VXLAN and Geneve network types are supported. If your default network
   type is configured to something else (e.g., VLAN or flat), then this
   plugin will not work as intended.

Step 6: Create Baremetal Port
-----------------------------

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
------------------

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
-------------------------

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
-------------------------------------

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
--------------------------

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
* Ironic VXLAN administration guide (connectivity models and mechanism
  driver ordering):
  https://docs.openstack.org/ironic/latest/admin/vxlan.html
* Ironic deploy / user guide (tenant self-service workflow):
  https://docs.openstack.org/ironic/latest/user/deploy.html
* OpenStack Neutron Documentation: https://docs.openstack.org/neutron/
* OVN Documentation: https://www.ovn.org/
