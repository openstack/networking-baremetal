===================================
HA Chassis Group Alignment
===================================

Overview
========

The HA Chassis Group Alignment feature addresses connectivity issues that can
occur in OVN deployments with baremetal nodes when router gateway ports and
baremetal external ports have mismatched HA chassis group priorities.

This feature implements automatic reconciliation to ensure router ports use
the same HA chassis group configuration as baremetal external ports on the
same network, eliminating intermittent connectivity failures.

Problem Statement
=================

In OpenStack deployments using OVN with baremetal nodes, external connectivity
can fail intermittently due to a configuration mismatch. This occurs when:

1. A baremetal node has an external port (``device_owner=baremetal:none``) on
   a provider network
2. A router is attached to the same network via a router interface port
3. OVN assigns different HA chassis groups with different priorities to the
   baremetal external port and the router gateway port
4. The active chassis for the baremetal port differs from the active chassis
   for the router port

When this mismatch occurs, traffic routing becomes inconsistent, causing
baremetal nodes to lose external connectivity intermittently as different
chassis believe they are the active gateway.

This issue is tracked in Launchpad as bug #1995078:
https://bugs.launchpad.net/neutron/+bug/1995078

Solution
========

The ironic-neutron-agent now includes a periodic reconciliation loop that:

1. Discovers all networks with baremetal external ports
2. Identifies the HA chassis group used by baremetal ports on each network
3. Finds router interface ports on the same networks
4. Updates router ports to use the same HA chassis group as baremetal ports
5. Only processes networks managed by the agent instance (via hash ring)

This ensures consistent HA chassis group configuration across all ports on
networks with baremetal nodes, preventing the priority mismatch that causes
connectivity failures.

Configuration
=============

The feature is controlled by options in the ``[baremetal_agent]`` section of
the agent configuration file (typically ``/etc/neutron/ironic_neutron_agent.ini``).

Enable/Disable
--------------

.. code-block:: ini

   [baremetal_agent]
   # Enable HA chassis group alignment reconciliation
   # Default: True
   enable_ha_chassis_group_alignment = True

Set to ``False`` to disable the feature if you are not experiencing the
connectivity issue or if you have resolved it through other means.

Reconciliation Interval
-----------------------

.. code-block:: ini

   [baremetal_agent]
   # Interval in seconds between alignment reconciliation runs
   # Default: 600 (10 minutes)
   # Minimum: 60
   ha_chassis_group_alignment_interval = 600

Controls how frequently the agent checks for and fixes HA chassis group
mismatches. The default of 10 minutes provides a balance between:

- Timely detection and correction of mismatches
- Minimal impact on Neutron API and OVN database load

For deployments with frequent network topology changes, you may want to reduce
this interval. For stable deployments, you can increase it to reduce overhead.

Time Window Filtering
---------------------

.. code-block:: ini

   [baremetal_agent]
   # Only check recently created/updated resources
   # Default: True
   limit_ha_chassis_group_alignment_to_recent_changes_only = True

   # Time window in seconds for "recent" resources
   # Default: 1200 (20 minutes, 2x the alignment interval)
   # Minimum: 0
   ha_chassis_group_alignment_window = 1200

When enabled, reconciliation only examines ports that have been created or
updated within the specified time window. This significantly reduces API and
database load in large deployments by focusing on resources most likely to
have mismatches (newly created ports).

**When to disable:** Set ``limit_ha_chassis_group_alignment_to_recent_changes_only = False``
if you:

- Want to perform full reconciliation on every run
- Are recovering from a period where the agent was disabled
- Suspect existing ports have mismatches that need correction

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

The agent logs alignment activities at the INFO level:

.. code-block:: text

   INFO ... Started HA chassis group alignment reconciliation loop
            (interval: 600s, first run in 42s)
   INFO ... Updating router port <uuid> HA chassis group from <old> to <new>
            (network <uuid>)
   INFO ... Successfully updated router port <uuid> HA chassis group

Failed updates are logged at ERROR level with full exception details.

Performance Impact
------------------

The reconciliation loop has minimal performance impact:

- **Default configuration:** Queries Neutron for baremetal ports every 10 minutes
- **With windowing enabled (default):** Only checks recently updated ports
- **Uses existing OVN connections:** Reuses connections from L2VNI trunk manager
  if available
- **Distributed load:** Multiple agents split work via hash ring

In a deployment with 1000 baremetal nodes and default settings:

- First Neutron query returns ~1000 ports
- With 20-minute window, ~50 ports processed per reconciliation (assuming 5%
  churn rate)
- Per-network processing: 1-2 additional Neutron queries, 2-3 OVN queries
- Total: ~100-150 API calls every 10 minutes across all agents

Troubleshooting
===============

Verifying the Feature is Running
---------------------------------

Check agent logs for startup message:

.. code-block:: console

   $ grep "HA chassis group alignment" /var/log/neutron/ironic-neutron-agent.log
   INFO ... HA chassis group alignment reconciliation enabled
   INFO ... Started HA chassis group alignment reconciliation loop
            (interval: 600s, first run in 42s)

Checking for Mismatches
-----------------------

If you suspect an alignment issue:

1. Identify the affected network and baremetal ports
2. Check OVN for the HA chassis group on baremetal ports:

   .. code-block:: console

      $ ovn-nbctl lsp-get-ha-chassis-group <port-uuid>

3. Check router ports on the same network:

   .. code-block:: console

      $ ovn-nbctl lrp-get-ha-chassis-group lrp-<router-port-uuid>

4. If different, the next reconciliation cycle will align them (check logs)

Forcing Immediate Reconciliation
---------------------------------

To trigger reconciliation without waiting for the interval:

1. Restart the ironic-neutron-agent
2. The first reconciliation runs within 60 seconds (with random jitter)

Alternatively, temporarily reduce the interval:

.. code-block:: console

   $ openstack-config --set /etc/neutron/ironic_neutron_agent.ini \
       baremetal_agent ha_chassis_group_alignment_interval 60
   $ systemctl restart ironic-neutron-agent

Related Features
================

This feature complements the L2VNI trunk reconciliation feature:

- **L2VNI reconciliation:** Manages VLAN trunk configurations for network nodes
- **HA alignment:** Ensures consistent HA chassis group configuration

Both features can be enabled independently and run on separate schedules.

References
==========

- Launchpad Bug #1995078: https://bugs.launchpad.net/neutron/+bug/1995078
- OVN HA Chassis Groups: https://www.ovn.org/support/dist-docs/ovn-nb.5.html
- ironic-neutron-agent: https://docs.openstack.org/networking-baremetal/
