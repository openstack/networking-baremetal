===================================
Router HA Binding for VLAN Networks
===================================

Overview
========

The Router HA Binding feature fixes connectivity issues between baremetal nodes
and routers on VLAN networks by ensuring router interface ports are bound to
the same HA chassis group as the network's external ports.

This feature uses event-driven binding for immediate response when HA chassis
groups are created, plus periodic reconciliation to handle edge cases, enabling
proper ARP resolution and eliminating persistent connectivity failures.

Problem Statement
=================

In OpenStack deployments using OVN with baremetal nodes on VLAN networks,
baremetal nodes cannot communicate with their router gateway because the
router's internal interface port (Logical Router Port) is not bound to any
chassis. When a baremetal node tries to ARP for the router IP, no chassis
responds because the router interface port has no HA chassis group set.

This manifests as:

1. Baremetal nodes on VLAN networks cannot reach their gateway
2. ARP requests from baremetal nodes to router IP receive no reply
3. Router cannot ARP for baremetal nodes on the physical network
4. Persistent connectivity failure until HA chassis groups are manually aligned
   or randomly align through other operations

**Symptoms in tcpdump:**

.. code-block:: console

   # On network node, monitoring VLAN traffic
   $ sudo tcpdump -i br-ex -envv 'vlan 105'

   # You see ARP requests from both sides, but no replies:
   Request who-has 10.0.5.1 (router) tell 10.0.5.26 (baremetal)
   Request who-has 10.0.5.26 (baremetal) tell 10.0.5.1 (router)

This issue is tracked in Launchpad:

- **LP#2144458** (fixed by this feature): Persistent connectivity failures on VLAN networks
- **LP#1995078** (related): Original OVN HA chassis group priority mismatch

Solution
========

The ironic-neutron-agent now includes a **RouterHABindingManager** that
automatically binds router interface ports to network HA chassis groups using
a dual approach:

**Event-Driven Binding (Primary)**

The agent monitors OVN's ``HA_Chassis_Group`` table for network-level groups.
When a network HA chassis group is created or updated:

1. ``HAChassisGroupNetworkEvent`` fires immediately
2. Agent finds all router interface ports on that network
3. Binds each router port to the network's HA chassis group
4. Router can now respond to ARP requests on the physical VLAN network

This provides **immediate** connectivity with no delay.

**Periodic Reconciliation (Backup)**

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

**Unified HA Chassis Groups**

The implementation supports both:

- **Network-only HA chassis groups**: Used for networks without routers
- **Unified HA chassis groups**: Single group used for both network and router
  (has both ``neutron:network_id`` and ``neutron:router_id`` in external_ids)

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

Controls how frequently the agent performs full reconciliation. The default
of 10 minutes provides a balance between:

- Catching edge cases (routers added after the fact, missed events)
- Minimal impact on Neutron API and OVN database load

**Note:** Event-driven binding provides immediate response when HA chassis
groups are created. Periodic reconciliation is just a safety net for edge cases.

Startup Jitter
--------------

.. code-block:: ini

   [baremetal_agent]
   # Maximum random delay for initial reconciliation start
   # Default: 60 seconds
   # Minimum: 0
   router_ha_binding_startup_jitter_max = 60

Adds random delay (0 to max seconds) before first reconciliation run to prevent
thundering herd when multiple agents restart simultaneously (e.g., post-upgrade).
A value of 60 means each agent starts reconciliation within 0-60 seconds of
startup.

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
- **Full reconciliation:** No complex windowing - simple and reliable
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

4. **Test connectivity with tcpdump:**

   .. code-block:: console

      $ sudo tcpdump -i br-ex -envv 'vlan <vlan-id>'

      # You should see ARP replies, not just requests:
      Request who-has 10.0.5.1 tell 10.0.5.26
      Reply 10.0.5.1 is-at fa:16:3e:82:d4:a4  # <- Router responds!

Forcing Immediate Reconciliation
---------------------------------

To trigger reconciliation without waiting:

.. code-block:: console

   $ systemctl restart ironic-neutron-agent

   # First reconciliation runs within 60 seconds (random jitter)

Related Features
================

This feature complements the L2VNI trunk reconciliation feature:

- **L2VNI reconciliation:** Manages VLAN trunk configurations for network nodes
- **HA alignment:** Ensures consistent HA chassis group configuration

Both features can be enabled independently and run on separate schedules.

Related Features
================

This feature complements the L2VNI trunk reconciliation feature:

- **L2VNI reconciliation:** Manages VLAN trunk configurations for network nodes
- **Router HA binding:** Ensures router interface ports are bound to HA chassis groups

Both features work together to enable baremetal connectivity on VLAN networks.

Legacy HA Chassis Group Alignment
==================================

**Note:** The original ``enable_ha_chassis_group_alignment`` feature remains
enabled by default for backward compatibility but is not functional for standard
Ironic deployments. It will be deprecated in a future release.

The new ``enable_router_ha_binding`` feature provides a working implementation
of the same concept and should be used instead.

References
==========

- Launchpad Bug #2144458: https://bugs.launchpad.net/networking-baremetal/+bug/2144458
- Launchpad Bug #1995078: https://bugs.launchpad.net/neutron/+bug/1995078
- OVN HA Chassis Groups: https://www.ovn.org/support/dist-docs/ovn-nb.5.html
- ironic-neutron-agent: https://docs.openstack.org/networking-baremetal/
