===================================
Router HA Binding for VLAN Networks
===================================

Overview
========

The Router HA Binding feature ensures router interface ports are bound to
the same HA chassis group as the network's external ports, enabling proper
connectivity between baremetal nodes and routers on VLAN networks.

This feature uses event-driven binding when HA chassis groups are created,
plus periodic reconciliation.

Without this feature, baremetal nodes on VLAN networks cannot communicate with
their router gateway because the router's internal interface port (Logical
Router Port) is not bound to any chassis. When a baremetal node tries to ARP
for the router IP, no chassis responds because the router interface port has
no HA chassis group set.

Implementation
==============

The ironic-neutron-agent now includes a **RouterHABindingManager** that
automatically binds router interface ports to network HA chassis groups using
a dual approach:

**Event-Driven Binding**

The agent monitors OVN's ``HA_Chassis_Group`` table for network-level groups.
When a network HA chassis group is created or updated:

1. ``HAChassisGroupNetworkEvent`` fires immediately
2. Agent finds all router interface ports on that network
3. Binds each router port to the network's HA chassis group
4. Router can now respond to ARP requests on the physical VLAN network

This provides **immediate** connectivity with no delay.

**Periodic Reconciliation**

A periodic reconciliation loop (default: 10 minutes) ensures eventual
consistency by:

1. Discovering all networks with HA chassis groups
2. Finding router interface ports on those networks
3. Binding any unbound or incorrectly bound router ports
4. Only processing networks managed by this agent (via hash ring)

This catches edge cases such as:

- Routers added to existing networks (no event fires)
- Missed events (agent down during HA chassis group creation)
- Manual changes to router port configuration
- Race conditions or out-of-order event processing

Configuration
=============

The feature is controlled by options in the ``[baremetal_agent]`` section of
the agent configuration file (typically ``/etc/neutron/ironic_neutron_agent.ini``).

Enable/Disable
--------------

.. code-block:: ini

   [baremetal_agent]
   # Enable router HA binding for VLAN networks
   # Default: True
   enable_router_ha_binding = True

Set to ``False`` to disable the feature if you are not using baremetal nodes
on VLAN networks with routers.

Event-Driven Binding
--------------------

.. code-block:: ini

   [baremetal_agent]
   # Enable event-driven router HA binding
   # Default: True
   enable_router_ha_binding_events = True

When enabled, the agent responds immediately to HA chassis group creation
events by binding router interface ports on the affected network. This provides
instant connectivity when networks are created.

Set to ``False`` to disable event-driven binding and rely only on periodic
reconciliation. This may result in connectivity delays until the next
reconciliation cycle (default: 10 minutes).

**Note:** Requires ``enable_router_ha_binding = True`` to have any effect.

Reconciliation Interval
-----------------------

.. code-block:: ini

   [baremetal_agent]
   # Interval in seconds between periodic reconciliation runs
   # Default: 600 (10 minutes)
   # Minimum: 60
   router_ha_binding_interval = 600

Controls how frequently the agent performs full reconciliation.

Startup Jitter
--------------

.. code-block:: ini

   [baremetal_agent]
   # Maximum random delay for initial reconciliation start
   # Default: 60 seconds
   # Minimum: 0
   router_ha_binding_startup_jitter_max = 60

Adds random delay (0 to max seconds) before first reconciliation run to prevent
thundering herd when multiple agents restart simultaneously. A value of 60 means
each agent starts reconciliation within 0-60 seconds of startup.

Operational Considerations
==========================

Multi-Agent Deployments
-----------------------

In deployments with multiple ironic-neutron-agent instances:

- Each agent uses a distributed hash ring to determine which networks it manages
- Only the responsible agent will reconcile a given network
- This prevents duplicate work and API contention
- If an agent fails, other agents will automatically take over its networks

Monitoring
----------

The agent logs binding activities at the INFO level:

.. code-block:: text

   INFO ... Router HA binding enabled, initializing manager
   INFO ... Started router HA binding reconciliation loop
            (interval: 600s, first run in 42s)
   INFO ... Registered OVN event handler for HA chassis group network events
   INFO ... Network HA chassis group ... created/updated for network ...,
            triggering router interface binding
   INFO ... Updated router port <uuid> HA chassis group from <old> to <new>
            (network <uuid>)
   INFO ... Router HA binding reconciliation complete: processed N networks,
            updated M router ports

Failed updates are logged at ERROR level with full exception details.

Performance Impact
------------------

The feature has minimal performance impact:

- **Event-driven binding:** Immediate response with no periodic overhead
- **Periodic reconciliation:** Runs every 10 minutes (configurable)
- **Idempotent operations:** Most checks are "already correct" (cheap)
- **Uses existing OVN connections:** Reuses connections from L2VNI trunk manager
- **Distributed load:** Multiple agents split work via hash ring

In a deployment with 100 networks with HA chassis groups:

- Event-driven: 1-2 Neutron queries, 1-2 OVN updates per HA chassis group creation
- Periodic: Scans all HA chassis groups, queries router ports per network
- Per-network processing: 1 Neutron query, 1-2 OVN operations
- Total periodic: ~100-200 operations every 10 minutes across all agents

**Since events handle 99% of cases immediately, periodic reconciliation overhead
is minimal.**

Troubleshooting
===============

Verifying the Feature is Running
---------------------------------

Check agent logs for startup messages:

.. code-block:: console

   $ grep "router HA binding" /var/log/neutron/ironic-neutron-agent.log
   INFO ... Router HA binding enabled, initializing manager
   INFO ... Started router HA binding reconciliation loop
            (interval: 600s, first run in 42s)
   INFO ... Registered OVN event handler for HA chassis group network events

Checking Router Interface Binding
----------------------------------

If baremetal nodes cannot reach their gateway:

1. **Verify the network has an HA chassis group:**

   .. code-block:: console

      $ sudo ovn-nbctl ha-chassis-group-list | grep neutron-<network-id>
      c18ab533-... (neutron-a72fd10e-...)
          5668117a-... (3773bfbe-...) priority 1

2. **Check if router interface port is bound:**

   .. code-block:: console

      $ ROUTER_PORT_ID=$(openstack port list --network <network-id> \
          --device-owner network:router_interface -c ID -f value)
      $ sudo ovn-nbctl get Logical_Router_Port lrp-$ROUTER_PORT_ID ha_chassis_group

      # Should show UUID, not []
      c18ab533-09b9-48fc-8acd-9407bd3f25d2

3. **If router port shows []:** Wait for next reconciliation or check logs for errors

Forcing Immediate Reconciliation
--------------------------------

To trigger reconciliation without waiting:

.. code-block:: console

   $ systemctl restart ironic-neutron-agent

   # First reconciliation runs within 60 seconds (random jitter)

See Also
========

* :doc:`l2vni-trunk-reconciliation` - L2VNI Trunk Reconciliation Configuration
* :doc:`/configuration/ironic-neutron-agent/index` - Agent Configuration Reference
