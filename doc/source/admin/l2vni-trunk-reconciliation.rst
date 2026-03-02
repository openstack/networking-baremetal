==========================================
L2VNI Trunk Reconciliation Configuration
==========================================

Overview
========

The L2VNI trunk reconciliation feature enables automatic management of trunk
ports on network nodes to bridge OVN overlay networks to physical network
infrastructure. This feature is essential for deployments where baremetal nodes
need to connect to overlay networks through network nodes acting as gateways.

What Problem Does This Solve?
------------------------------

In deployments using the L2VNI mechanism driver, baremetal nodes connect to
VXLAN/Geneve overlay networks via VLAN segments on physical networks. For this
to work, network nodes must:

1. Have trunk ports configured with the correct VLAN subports
2. Keep those VLANs synchronized with active overlay networks
3. Clean up VLANs when overlay networks are removed
4. Handle changes in ha_chassis_group membership

Manual management of these trunk ports becomes impractical at scale, especially
when:

- Multiple network nodes form ha_chassis_groups for high availability
- Overlay networks are frequently created and deleted
- Router migrations cause chassis membership changes
- Network nodes are added or removed from the infrastructure

The trunk reconciliation feature automates this entire process.

Architecture
============

How It Works
------------

The ironic-neutron-agent runs a periodic reconciliation loop that:

1. **Discovers Network Nodes**: Identifies chassis that are members of OVN
   ha_chassis_groups by querying the OVN Northbound database.

2. **Creates Trunk Infrastructure**: For each (chassis, physical_network)
   combination, ensures:

   - An anchor port exists on the ha_chassis_group network
   - A trunk port is created using that anchor port
   - The trunk is properly named for tracking

3. **Calculates Required VLANs**: Analyzes OVN state to determine which VLANs
   are needed on each trunk:

   - Queries logical router ports for gateway chassis assignments
   - Identifies overlay networks attached to those routers
   - Finds the dynamic VLAN segment allocated for each overlay network

4. **Reconciles Subports**: Ensures each trunk has exactly the right set of
   VLAN subports:

   - Adds missing subports for new overlay networks
   - Removes subports for deleted overlay networks
   - Updates subport binding profiles with switch connection information

5. **Cleans Up Orphans**: Removes infrastructure for deleted network nodes:

   - Deletes trunks for chassis no longer in ha_chassis_groups
   - Removes anchor ports and subports
   - Cleans up networks for deleted ha_chassis_groups

Components and Data Flow
-------------------------

.. code-block:: text

    ┌─────────────────────────────────────────────────────────┐
    │                  OVN Northbound DB                      │
    │  - HA Chassis Groups                                    │
    │  - Logical Router Ports                                 │
    │  - Gateway Chassis Assignments                          │
    └────────────────┬────────────────────────────────────────┘
                     │
                     │ Queries
                     │
    ┌────────────────▼────────────────────────────────────────┐
    │           ironic-neutron-agent                          │
    │                                                         │
    │  ┌─────────────────────────────────────────┐            │
    │  │   L2VNI Trunk Manager                   │            │
    │  │                                         │            │
    │  │  1. Discover chassis in ha_groups       │            │
    │  │  2. Calculate required VLANs            │            │
    │  │  3. Reconcile trunk subports            │            │
    │  │  4. Cleanup orphaned resources          │            │
    │  └────┬────────────────────────────────────┘            │
    │       │                                                 │
    └───────┼─────────────────────────────────────────────────┘
            │
            │ Neutron API calls
            │
    ┌───────▼───────────────────────────────────────────────────┐
    │                    Neutron Server                         │
    │  - Creates/deletes trunk ports                            │
    │  - Manages subports                                       │
    │  - Coordinates with ML2 plugin                            │
    └────────────────┬──────────────────────────────────────────┘
                     │
                     │ ML2 mechanism drivers
                     │
    ┌────────────────▼───────────────────────────────────────────┐
    │              Physical Switch Plugins                       │
    │  (e.g., genericswitch)                                     │
    │  - Configures trunk ports on physical switches             │
    │  - Maps VLANs to network node ports                        │
    └────────────────────────────────────────────────────────────┘

Infrastructure Objects
----------------------

**HA Chassis Group Networks**

For each ha_chassis_group in OVN, the reconciliation process creates a Neutron
network named ``l2vni-ha-group-{group_uuid}``. These networks are used to host
anchor ports and provide network context for trunk ports.

**Anchor Ports**

Each (chassis, physical_network) combination gets an anchor port named
``l2vni-anchor-{system_id}-{physnet}``. The anchor port:

- Is created on the ha_chassis_group network
- Has device_owner ``baremetal:l2vni_anchor``
- Contains binding profile with system_id and physical_network
- Serves as the parent port for the trunk

**Trunk Ports**

Trunks are named ``l2vni-trunk-{system_id}-{physnet}`` and use the anchor port
as their parent. The trunk carries multiple VLAN-tagged subports.

**Subports**

Each subport represents one overlay network's VLAN segment:

- Named ``l2vni-subport-{system_id}-{physnet}-vlan{vlan_id}``
- Created on the subport anchor network (``l2vni-subport-anchor`` by default)
- Has device_owner ``baremetal:l2vni_subport``
- Includes binding profile with local_link_connection information
- Segmentation type is always ``vlan``

**Subport Anchor Network**

A shared network (default name: ``l2vni-subport-anchor``) hosts all subports
across all trunks. This network is used to signal VLAN bindings to ML2 switch
plugins and does not pass actual traffic.

Switch Connection Information
------------------------------

The reconciliation process attempts to discover switch connection information
for each trunk from multiple sources, in priority order:

1. **OVN LLDP Data**: Extracted from OVN Southbound Port table external_ids
   (``lldp_chassis_id``, ``lldp_port_id``, ``lldp_system_name``)

2. **Ironic Node Data**: Retrieved from Ironic port local_link_connection for
   nodes matching the chassis system-id. **Cached per system_id** with
   configurable TTL to reduce API load.

   Queries use field filtering (only fetching uuid, properties,
   physical_network, and local_link_connection) for optimal performance.

   Optional filtering by conductor_group and shard reduces query scope in
   large deployments.

3. **YAML Configuration File**: Fallback configuration from
   ``l2vni_network_nodes_config`` file

**Performance Note:**

Ironic data is cached per system_id with individual expiration times (TTL +
jitter). In a deployment with 1000 nodes, this reduces API calls from ~16,000
per reconciliation to ~2 (when cached), while still refreshing stale data
automatically.

This information is included in subport binding profiles to enable switch
management plugins (e.g., genericswitch) to configure the physical switch
ports correctly.

.. warning::
   Where the cache may be problematic is if you are re-cabling networker
   nodes on a fairly regular basis. While fundimentally such an action
   *is* a breaking change in itself in any operating environment, the
   cache will retain details for up to an hour and may also reflect
   incorrect details if the data sources (Ironic, or the YAML configuration)
   are not updated. Neutron guidance around changing configuration
   in such cases is also to change the agents which will reset the cache.

Prerequisites
=============

Required Components
-------------------

- **OpenStack Neutron** with ML2 plugin
- **OVN Backend** (Open Virtual Network) - required for ha_chassis_groups
- **L2VNI Mechanism Driver** - must be enabled and configured
- **Physical Network Switches** - configured for VLAN trunking
- **Switch Management Plugin** (e.g., genericswitch) - for VNI↔VLAN mapping
- **Network Nodes** - chassis that are members of OVN ha_chassis_groups

Network Architecture Requirements
----------------------------------

Your deployment must use:

- OVN as the Neutron backend (ML2/OVN)
- HA chassis groups for gateway routers
- Network nodes with bridge-mappings to physical networks
- VLAN-capable physical switches connecting network nodes

Service Dependencies
--------------------

The ironic-neutron-agent requires:

- Connectivity to Neutron API
- Connectivity to OVN Northbound database
- Connectivity to OVN Southbound database
- (Optional) Connectivity to Ironic API for enhanced switch information

Configuration
=============

Enabling Trunk Reconciliation
------------------------------

Edit ``/etc/neutron/neutron.conf`` (or a separate config file in
``/etc/neutron/neutron.conf.d/``) and add the ``[l2vni]`` section:

.. code-block:: ini

   [l2vni]
   # Enable L2VNI trunk reconciliation
   enable_l2vni_trunk_reconciliation = True

   # Baseline reconciliation interval (seconds)
   l2vni_reconciliation_interval = 300

   # Network node configuration file (fallback for switch info)
   l2vni_network_nodes_config = /etc/neutron/l2vni_network_nodes.yaml

   # Auto-create infrastructure networks
   l2vni_auto_create_networks = True

   # Subport anchor network name
   l2vni_subport_anchor_network = l2vni-subport-anchor

   # Network type for infrastructure networks (geneve or vxlan)
   l2vni_subport_anchor_network_type = geneve

   # Startup jitter to prevent thundering herd (seconds)
   l2vni_startup_jitter_max = 60

   # Ironic caching (if using Ironic for switch info)
   ironic_cache_ttl = 3600

Configuration Options Reference
--------------------------------

``enable_l2vni_trunk_reconciliation``
    **Type**: Boolean

    **Default**: ``True``

    **Description**: Switch to enable L2VNI trunk port reconciliation.
    When enabled, the agent will automatically manage trunk subports for
    network nodes based on OVN ha_chassis_group membership.

    Set this to ``False`` to disable the feature.

``l2vni_reconciliation_interval``
    **Type**: Integer (seconds)

    **Default**: ``300`` (5 minutes)

    **Minimum**: ``30``

    **Description**: Baseline interval between reconciliation runs.
    This is the steady-state reconciliation frequency.

    **Tuning guidance:**

    - Smaller values (60-120s) provide faster convergence but increase API load
    - Larger values (300-600s) reduce overhead but slow convergence
    - For small deployments: 60-120 seconds
    - For large deployments (100+ network nodes): 300-600 seconds

``l2vni_network_nodes_config``
    **Type**: String (file path)

    **Default**: ``/etc/neutron/l2vni_network_nodes.yaml``

    **Description**: Path to YAML configuration file containing network node
    trunk port configuration. This file serves as a fallback source for
    local_link_connection information when LLDP data is not available from OVN
    and Ironic.

    See `Network Node Configuration File`_ for file format details.

``l2vni_auto_create_networks``
    **Type**: Boolean

    **Default**: ``True``

    **Description**: Automatically create Neutron networks for ha_chassis_groups
    and the subport anchor network if they do not exist.

    **When True (recommended):**

    - Networks are created automatically on first reconciliation
    - Simplifies deployment and upgrades
    - Networks are cleaned up when no longer needed

    **When False:**

    - Networks must be pre-created manually
    - Network names must match expected patterns exactly
    - Useful for environments with strict network creation policies

``l2vni_subport_anchor_network``
    **Type**: String

    **Default**: ``l2vni-subport-anchor``

    **Description**: Name of the shared network used for all trunk subports
    across all network nodes. This network is used to signal VLAN bindings to
    ML2 switch plugins and does not pass actual traffic.

    All subports are created on this network regardless of which ha_chassis_group
    or overlay network they represent.

    .. note::
       If you change this value, ensure the network exists or set
       ``l2vni_auto_create_networks = True``.

``l2vni_subport_anchor_network_type``
    **Type**: String (enum)

    **Default**: ``geneve``

    **Choices**: ``geneve``, ``vxlan``

    **Description**: Network type to use for L2VNI infrastructure networks
    (both subport anchor and ha_chassis_group networks). These networks are
    used for metadata and modeling only, not for passing traffic.

    Must match the overlay network type configured in your environment
    (ml2_type_drivers). If the specified type is not available, network
    creation will fail with an explicit error rather than falling back to
    an alternative type.

    .. warning::
       Ensure the selected network type is enabled in Neutron's
       ml2_type_drivers configuration before enabling trunk reconciliation.

``ironic_cache_ttl``
    **Type**: Integer (seconds)

    **Default**: ``3600`` (1 hour)

    **Minimum**: ``300`` (5 minutes)

    **Description**: Time-to-live for cached Ironic node and port data.
    Each system_id entry is cached independently and expires after this
    duration from when it was fetched.

    A small amount of jitter (10-20%) is automatically added to spread
    cache refresh times across multiple agents, avoiding thundering herd
    issues when cache entries expire.

    **Tuning guidance:**

    - Smaller values (300-900s): More frequent Ironic API calls, faster
      detection of changes to node/port configurations
    - Larger values (3600-7200s): Reduced API load, suitable for stable
      deployments where node configurations rarely change
    - For deployments with frequent node changes: 300-600 seconds
    - For stable deployments (1000+ nodes): 3600-7200 seconds

    **Performance impact:**

    In deployments with 1000 nodes × 8 ports each, efficient caching
    reduces per-reconciliation API calls from ~16,000 to ~2 (when cached).

``ironic_conductor_group``
    **Type**: String

    **Default**: ``None`` (no filtering)

    **Description**: Ironic conductor group to filter nodes when querying
    for local_link_connection data. This allows the agent to only query
    nodes managed by a specific conductor group, significantly reducing
    API load in large deployments with conductor group partitioning.

    If not specified, all nodes are queried (subject to shard filtering).

    **Example use case:**

    In a deployment with separate conductor groups for different
    availability zones, set this to match the zone where trunk
    reconciliation is needed.

``ironic_shard``
    **Type**: String

    **Default**: ``None`` (no filtering)

    **Description**: Ironic shard to filter nodes when querying for
    local_link_connection data. This allows the agent to only query nodes
    in a specific shard, significantly reducing API load in large sharded
    deployments.

    If not specified, all nodes are queried (subject to conductor group
    filtering).

    **Example use case:**

    In a deployment sharded by region (shard-us-west, shard-us-east),
    set this to match the region where trunk reconciliation is needed.

``l2vni_startup_jitter_max``
    **Type**: Integer (seconds)

    **Default**: ``60``

    **Minimum**: ``0``

    **Description**: Maximum random delay added to initial reconciliation start
    time after agent startup. This prevents thundering herd issues when multiple
    agents restart simultaneously (e.g., after a rolling upgrade).

    Each agent will start reconciliation within 0 to ``l2vni_startup_jitter_max``
    seconds of startup, spreading the initial load.

    **Recommended values:**

    - Single agent: ``0`` (no jitter needed)
    - 2-5 agents: ``30-60`` seconds
    - 6+ agents: ``60-120`` seconds

Network Node Configuration File
================================

The network node configuration file provides fallback local_link_connection
information when LLDP and Ironic data are not available.

File Format
-----------

The file is in YAML format at the path specified by
``l2vni_network_nodes_config``:

.. code-block:: yaml

   network_nodes:
     - system_id: compute-1
       trunks:
         - physical_network: physnet1
           local_link_connection:
             switch_id: "00:11:22:33:44:55"
             port_id: "Ethernet1/1"
             switch_info: "tor-switch-1"
         - physical_network: physnet2
           local_link_connection:
             switch_id: "aa:bb:cc:dd:ee:ff"
             port_id: "GigabitEthernet1/0/1"
             switch_info: "tor-switch-2"

     - system_id: compute-2
       trunks:
         - physical_network: physnet1
           local_link_connection:
             switch_id: "00:11:22:33:44:55"
             port_id: "Ethernet1/2"
             switch_info: "tor-switch-1"

Field Descriptions
------------------

**network_nodes** (required)
    List of network node configurations

**system_id** (required)
    The OVN chassis system-id (from ``external-ids:system-id`` in OVS).
    This must match the system-id in OVN Southbound Chassis table.

    You can find system-ids with:

    .. code-block:: bash

       ovn-sbctl list Chassis | grep system-id

**trunks** (required)
    List of trunk configurations for this network node

**physical_network** (required)
    The physical network name (must match ``network_vlan_ranges`` and
    ``ovn-bridge-mappings`` configuration)

**local_link_connection** (required)
    Dictionary containing switch connection information:

    - **switch_id**: Switch MAC address or identifier
    - **port_id**: Switch port name/identifier
    - **switch_info**: Optional switch hostname or description

This information is passed to switch management plugins in the binding profile
to enable automatic switch port configuration.

Multi-Agent Deployment
=======================

Hash Ring Distribution
-----------------------

When multiple ironic-neutron-agents are deployed, they use a hash ring to
distribute work. Each chassis is hashed to a specific agent, and only that
agent manages trunks for that chassis.

**Benefits:**

- Load distribution across agents
- Reduced API call volume
- Parallel processing of reconciliation tasks

**How It Works:**

1. Agents register with Tooz coordinator (typically backed by etcd or Redis)
2. A consistent hash ring is built from agent memberships
3. Each chassis system-id is hashed to determine the owning agent
4. During reconciliation, agents skip chassis not in their hash ring segment

Example: 3 agents managing 10 chassis:

- Agent A manages: chassis-1, chassis-4, chassis-7, chassis-10
- Agent B manages: chassis-2, chassis-5, chassis-8
- Agent C manages: chassis-3, chassis-6, chassis-9

Cleanup Considerations
----------------------

**Important**: Cleanup operations do **not** use hash ring filtering.

When orphaned trunks are detected (chassis removed from ha_chassis_group or
deleted entirely), all agents will attempt cleanup. This is intentional:

**Scenario**: Agent A managed chassis-5, then Agent A crashes. Chassis-5 is
deleted from OVN. Agents B and C both detect the orphaned trunk and attempt
cleanup. The first agent to run cleanup succeeds; the other gets a "not found"
error (harmless).

This approach provides:

- **Resilience**: Cleanup happens even if the original managing agent is down
- **Simplicity**: No need to track previous ownership
- **Correctness**: No orphaned resources due to agent failures

The cost is minimal: redundant API calls that return 404, logged as warnings.

High Availability
-----------------

For production deployments, run at least 3 ironic-neutron-agents:

.. code-block:: bash

   # On controller-1
   systemctl start ironic-neutron-agent

   # On controller-2
   systemctl start ironic-neutron-agent

   # On controller-3
   systemctl start ironic-neutron-agent

If an agent fails:

1. Tooz coordinator detects the failure
2. Hash ring is recalculated
3. Remaining agents automatically take over the failed agent's chassis
4. Reconciliation continues without interruption

Deployment Guide
================

Step 1: Enable the L2VNI Mechanism Driver
------------------------------------------

Ensure the L2VNI mechanism driver is enabled and configured. See
:doc:`/configuration/ml2/l2vni-mechanism-driver` for details.

Step 2: Configure Trunk Reconciliation
---------------------------------------

Edit ``/etc/neutron/neutron.conf``:

.. code-block:: ini

   [l2vni]
   enable_l2vni_trunk_reconciliation = True
   l2vni_reconciliation_interval = 300
   l2vni_auto_create_networks = True
   l2vni_subport_anchor_network_type = geneve
   l2vni_startup_jitter_max = 60

Step 3: (Optional) Create Network Node Config
----------------------------------------------

If LLDP data is not available in OVN, create
``/etc/neutron/l2vni_network_nodes.yaml``:

.. code-block:: yaml

   network_nodes:
     - system_id: network-node-1
       trunks:
         - physical_network: physnet1
           local_link_connection:
             switch_id: "00:11:22:33:44:55"
             port_id: "Ethernet1/1"
             switch_info: "tor-switch-1"

Step 4: Restart ironic-neutron-agent
-------------------------------------

.. code-block:: bash

   systemctl restart ironic-neutron-agent

Step 5: Verify Operation
-------------------------

Check logs for successful reconciliation:

.. code-block:: bash

   journalctl -u ironic-neutron-agent -f | grep L2VNI

You should see:

.. code-block:: text

   Started L2VNI trunk reconciliation loop (interval: 300s, initial delay: 345s with 45s jitter)
   Starting L2VNI trunk reconciliation
   Discovered trunk l2vni-trunk-network-node-1-physnet1
   Added subport port-uuid (VLAN 100) to trunk trunk-uuid
   L2VNI trunk reconciliation completed successfully

Step 6: Create Test Overlay Network
------------------------------------

Create a test setup to verify trunk reconciliation:

.. code-block:: bash

   # Create overlay network
   openstack network create test-overlay

   # Create subnet
   openstack subnet create --network test-overlay \
     --subnet-range 192.168.100.0/24 test-subnet

   # Create router with external gateway
   openstack router create test-router
   openstack router set --external-gateway public test-router

   # Add overlay network to router
   openstack router add subnet test-router test-subnet

   # Create baremetal port
   openstack port create \
     --network test-overlay \
     --vnic-type baremetal \
     --binding-profile physical_network=physnet1 \
     test-bm-port

After the next reconciliation cycle, verify trunk subports:

.. code-block:: bash

   openstack network trunk list
   openstack network trunk show <trunk-id>

You should see a subport with the VLAN allocated for test-overlay.

Monitoring and Operations
==========================

Log Messages
------------

**Normal Operation:**

.. code-block:: text

   Started L2VNI trunk reconciliation loop
   Starting L2VNI trunk reconciliation
   Discovered trunk l2vni-trunk-system-1-physnet1
   Added subport port-123 (VLAN 100) to trunk trunk-456
   L2VNI trunk reconciliation completed successfully

**Event-Driven Fast Mode:**

.. code-block:: text

   Router notification received, triggering fast reconciliation
   Switched to fast reconciliation mode (interval: 90s, duration: 600s)
   Exiting fast reconciliation mode, returning to baseline interval

**Cleanup Operations:**

.. code-block:: text

   Cleaning up orphaned trunk trunk-789 for chassis deleted-system physnet1
   Deleted orphaned trunk trunk-789
   Deleted orphaned anchor port port-999
   Cleaning up orphaned ha_chassis_group network net-111 for group deleted-group

**Warnings (Expected During Cleanup):**

.. code-block:: text

   Failed to delete subport subport-222
   Failed to delete trunk trunk-333

These warnings are normal when multiple agents attempt cleanup simultaneously.
The first agent succeeds; others log warnings.

Metrics and Health Indicators
------------------------------

Monitor these indicators for healthy operation:

1. **Reconciliation Completion**: Should see "completed successfully" messages
   at configured intervals

2. **API Error Rate**: Occasional 404 errors during cleanup are normal;
   frequent 500 errors indicate problems

3. **Reconciliation Duration**: Should complete in seconds; if taking minutes,
   check for API performance issues

4. **Orphaned Resource Count**: Should be zero or near-zero in steady state

Troubleshooting
===============

Reconciliation Not Running
---------------------------

**Symptom**: No L2VNI reconciliation log messages.

**Possible Causes:**

1. **Feature disabled**: ``enable_l2vni_trunk_reconciliation = False``

   **Solution**: Set to ``True`` in config and restart agent.

2. **OVN connection failure**: Agent cannot connect to OVN NB/SB databases.

   **Solution**: Check OVN connection settings in config. Verify OVN services
   are running and accessible.

3. **No ha_chassis_groups**: No network nodes are members of ha_chassis_groups.

   **Solution**: This is normal if you have no routers with gateway ports.
   Create a router with external gateway to trigger ha_chassis_group creation.

Trunks Not Created
------------------

**Symptom**: Reconciliation runs but no trunks appear.

**Possible Causes:**

1. **No chassis in ha_chassis_groups**: Chassis exists in OVN but not assigned
   to any ha_chassis_group.

   **Solution**: Create a router with external gateway. OVN will automatically
   assign gateway chassis.

2. **Missing bridge-mappings**: Chassis lacks ``ovn-bridge-mappings`` in
   external_ids.

   **Solution**: Configure bridge-mappings on the chassis:

   .. code-block:: bash

      ovs-vsctl set Open_vSwitch . \
        external-ids:ovn-bridge-mappings=physnet1:br-provider

3. **Network creation disabled**: ``l2vni_auto_create_networks = False`` but
   ha_chassis_group network doesn't exist.

   **Solution**: Either enable auto-creation or manually create network:

   .. code-block:: bash

      openstack network create l2vni-ha-group-{group-uuid}

Subports Not Added
------------------

**Symptom**: Trunks exist but have no subports.

**Possible Causes:**

1. **No overlay networks**: No VXLAN/Geneve networks are attached to routers.

   **Solution**: This is normal. Create overlay networks and attach to routers.

2. **No VLAN segments allocated**: L2VNI mechanism driver didn't allocate VLAN
   segments.

   **Solution**: Check that baremetal ports exist on overlay networks. Verify
   L2VNI mechanism driver is enabled and working.

3. **Subport anchor network missing**: Subport creation fails because anchor
   network doesn't exist.

   **Solution**: Enable auto-creation or manually create:

   .. code-block:: bash

      openstack network create l2vni-subport-anchor

Missing Switch Information
---------------------------

**Symptom**: Subports created but binding profile lacks local_link_connection.

**Possible Causes:**

1. **No LLDP data in OVN**: OVN doesn't have LLDP information for the chassis.

   **Solution**: Ensure switches are sending LLDP and OVN is configured to
   collect it, or provide fallback config file.

2. **Ironic not available**: Agent cannot query Ironic API.

   **Solution**: Check Ironic connectivity or provide fallback config file.

3. **Config file missing/incorrect**: Fallback config file doesn't exist or
   has wrong format.

   **Solution**: Create config file following the format in
   `Network Node Configuration File`_.

Reconciliation Taking Too Long
-------------------------------

**Symptom**: Reconciliation runs for minutes instead of seconds.

**Possible Causes:**

1. **Large number of network nodes**: Many chassis with many physical networks.

   **Solution**: Increase ``l2vni_reconciliation_interval`` to reduce frequency.
   Consider deploying more agents for load distribution.

2. **Neutron API slow**: API calls taking long time.

   **Solution**: Investigate Neutron API performance. Check database load.

3. **OVN database queries slow**: Queries to OVN NB/SB taking long time.

   **Solution**: Check OVN database performance. Ensure indexes are healthy.

4. **Ironic queries slow**: Querying thousands of Ironic nodes/ports per run.

   **Solution**: Enable Ironic caching and use conductor_group/shard filtering:

   .. code-block:: ini

      [l2vni]
      ironic_cache_ttl = 3600
      ironic_conductor_group = network-nodes
      ironic_shard = region-1

Infrastructure Network Creation Fails
--------------------------------------

**Symptom**: Reconciliation fails with errors about network creation.

**Example Error:**

.. code-block:: text

   ERROR Failed to create L2VNI network 'l2vni-subport-anchor' with type
   'geneve'. This indicates a misconfiguration - the requested network type
   is not available in your environment.

**Possible Causes:**

1. **Network type not enabled**: Configured network type (geneve or vxlan)
   is not enabled in Neutron's ml2_type_drivers.

   **Solution**: Add the network type to ml2_type_drivers in neutron.conf:

   .. code-block:: ini

      [ml2]
      type_drivers = flat,vlan,geneve,vxlan

   Then restart neutron-server.

2. **Wrong network type configured**: ``l2vni_subport_anchor_network_type``
   doesn't match your deployment's overlay network type.

   **Solution**: Set to match your environment:

   .. code-block:: ini

      [l2vni]
      # For VXLAN-based deployments
      l2vni_subport_anchor_network_type = vxlan

      # For Geneve-based deployments (default)
      l2vni_subport_anchor_network_type = geneve

   Then restart ironic-neutron-agent.

Agent Crashes During Reconciliation
------------------------------------

**Symptom**: ironic-neutron-agent crashes or restarts during reconciliation.

**Check Logs:**

.. code-block:: bash

   journalctl -u ironic-neutron-agent --since "1 hour ago"

**Possible Causes:**

1. **Uncaught exception**: Bug in reconciliation code.

   **Solution**: Report the bug with full traceback. As a workaround, disable
   trunk reconciliation until fixed.

2. **Memory exhaustion**: Agent runs out of memory in large deployments.

   **Solution**: Increase agent memory limits. Consider deploying more agents
   to distribute load.

3. **Deadlock or timeout**: Operation hangs waiting for response.

   **Solution**: Check network connectivity to Neutron/OVN. Review timeout
   settings.

Upgrade Considerations
======================

Upgrading from Previous Versions
---------------------------------

When upgrading to a version with trunk reconciliation:

1. **First upgrade**: Feature is disabled by default. No impact.

2. **Enabling the feature**:

   - Add ``[l2vni]`` configuration to neutron.conf
   - Set ``enable_l2vni_trunk_reconciliation = True``
   - Restart ironic-neutron-agent

3. **First reconciliation**: Agent will:

   - Create infrastructure networks
   - Create anchor ports and trunks for existing network nodes
   - Add subports for existing overlay networks

   This initial reconciliation may take longer than usual. Watch logs for
   progress.

Rolling Upgrades
----------------

The startup jitter feature (``l2vni_startup_jitter_max``) is specifically
designed to handle rolling upgrades gracefully:

**Scenario**: 3 agents running, performing rolling upgrade:

1. Stop agent-1, upgrade, restart → starts reconciliation after random delay
   (0-60s)
2. Stop agent-2, upgrade, restart → starts reconciliation after different delay
   (0-60s)
3. Stop agent-3, upgrade, restart → starts reconciliation after different delay
   (0-60s)

This prevents all agents from hitting Neutron API simultaneously after restart.

**Best Practices:**

- Use default ``l2vni_startup_jitter_max = 60`` or higher
- Upgrade one agent at a time
- Wait for agent to complete first reconciliation before upgrading next
- Monitor API load during upgrade

Compatibility
-------------

**Required OpenStack Release:**

- Queens or later (requires OVN backend and ML2/OVN)

**L2VNI Mechanism Driver:**

- Must be version 7.1.0 or later for full compatibility
- Trunk reconciliation works independently but is designed to complement the
  mechanism driver

**Ironic Integration:**

- Optional: Ironic API is used for enhanced switch information if available
- Works without Ironic using LLDP or config file fallback

Disabling the Feature
---------------------

To disable trunk reconciliation:

1. Set ``enable_l2vni_trunk_reconciliation = False`` in config
2. Restart ironic-neutron-agent
3. Reconciliation stops; existing trunks remain

To fully clean up:

.. code-block:: bash

   # List L2VNI trunks
   openstack network trunk list | grep l2vni-trunk

   # Delete each trunk (subports are automatically removed)
   openstack network trunk delete <trunk-id>

   # Delete infrastructure networks
   openstack network list | grep l2vni-
   openstack network delete <network-id>

Performance Tuning
==================

Small Deployments (< 10 network nodes)
---------------------------------------

Optimize for fast convergence:

.. code-block:: ini

   [l2vni]
   l2vni_reconciliation_interval = 60
   l2vni_startup_jitter_max = 10

Medium Deployments (10-50 network nodes)
-----------------------------------------

Balance convergence and overhead:

.. code-block:: ini

   [l2vni]
   l2vni_reconciliation_interval = 180
   l2vni_startup_jitter_max = 30

Large Deployments (50+ network nodes)
--------------------------------------

Optimize for reduced API load:

.. code-block:: ini

   [l2vni]
   l2vni_reconciliation_interval = 600
   l2vni_startup_jitter_max = 120

Ironic Integration Performance
-------------------------------

For deployments using Ironic for switch connection information:

**Small Ironic Deployments (< 100 nodes)**

Fast cache refresh:

.. code-block:: ini

   [l2vni]
   ironic_cache_ttl = 600

**Medium Ironic Deployments (100-500 nodes)**

Balanced refresh:

.. code-block:: ini

   [l2vni]
   ironic_cache_ttl = 1800

**Large Ironic Deployments (500+ nodes)**

Optimize for API load reduction:

.. code-block:: ini

   [l2vni]
   ironic_cache_ttl = 7200
   # Optional: Filter to reduce query scope
   ironic_conductor_group = network-nodes
   ironic_shard = region-west

**Sharded Deployments**

Use shard filtering to query only relevant nodes:

.. code-block:: ini

   [l2vni]
   ironic_shard = shard-region-1
   ironic_cache_ttl = 3600

See Also
========

* :doc:`/configuration/ml2/l2vni-mechanism-driver` - L2VNI Mechanism Driver
* :doc:`/configuration/ironic-neutron-agent/index` - Agent Configuration
* :doc:`/contributor/index` - Contributing Guide
* OpenStack Neutron Documentation: https://docs.openstack.org/neutron/
* OVN Documentation: https://www.ovn.org/
