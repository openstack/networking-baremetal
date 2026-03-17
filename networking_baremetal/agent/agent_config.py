# Copyright (c) 2026 Red Hat, Inc.
# All Rights Reserved
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

"""Configuration options for ironic-neutron-agent."""

from oslo_config import cfg

# L2VNI trunk reconciliation options
L2VNI_OPTS = [
    cfg.BoolOpt(
        'enable_l2vni_trunk_reconciliation',
        # TODO(TheJulia): We might want this to be False by default.
        default=True,
        help='Enable L2VNI trunk port reconciliation based on OVN '
             'ha_chassis_group membership. When enabled, the agent will '
             'automatically manage trunk subports for network nodes to '
             'ensure only required VLANs are trunked to each chassis. '
             'This feature creates anchor ports and trunk configurations '
             'to bridge overlay networks to physical network infrastructure.'),
    cfg.IntOpt(
        'l2vni_reconciliation_interval',
        default=180,
        min=30,
        help='Interval in seconds between L2VNI trunk reconciliation runs. '
             'Default is 180 seconds (3 minutes).'),
    cfg.StrOpt(
        'l2vni_network_nodes_config',
        default='/etc/neutron/l2vni_network_nodes.yaml',
        help='Path to YAML file containing network node trunk port '
             'configuration. Used as fallback when trunk configuration is '
             'not available from OVN LLDP data or Ironic. The file should '
             'define system_id or hostname, physical_network, and '
             'local_link_information for each network node. '
             'Network nodes can be identified by either system_id (OVN '
             'chassis UUID) or hostname (OVN chassis hostname) for easier '
             'configuration.'),
    cfg.BoolOpt(
        'l2vni_auto_create_networks',
        default=True,
        help='Automatically create Neutron networks for ha_chassis_groups '
             'and subport anchors if they do not exist. These networks are '
             'used for metadata and modeling, not for passing traffic. If '
             'disabled, networks must be pre-created with names matching '
             'the expected patterns.'),
    cfg.StrOpt(
        'l2vni_subport_anchor_network',
        default='l2vni-subport-anchor',
        help='Name of the shared network used for all trunk subports. This '
             'network is used to signal VLAN bindings to ML2 switch plugins '
             'and does not pass actual traffic. Will be auto-created if '
             'l2vni_auto_create_networks is enabled.'),
    cfg.StrOpt(
        'l2vni_subport_anchor_network_type',
        default='geneve',
        choices=['geneve', 'vxlan'],
        help='Network type to use for L2VNI anchor networks (both subport '
             'anchor and ha_chassis_group networks). These networks are used '
             'for metadata and modeling only, not for passing traffic. Must '
             'match the overlay network type configured in your environment. '
             'If the specified type is not available, network creation will '
             'fail with an error rather than falling back to an alternative '
             'type.'),
    cfg.IntOpt(
        'l2vni_startup_jitter_max',
        default=60,
        min=0,
        help='Maximum random delay in seconds to add to initial '
             'reconciliation start time. This prevents thundering herd '
             'issues when multiple agents restart simultaneously (e.g., '
             'post-upgrade). A value of 60 means each agent will start '
             'reconciliation within 0-60 seconds of startup.'),
    cfg.BoolOpt(
        'enable_l2vni_trunk_reconciliation_events',
        default=True,
        help='Enable event-driven L2VNI trunk reconciliation. When enabled, '
             'the agent watches OVN Northbound database for localnet port '
             'creation and deletion events and triggers immediate '
             'reconciliation. This eliminates the stale IDL cache issue and '
             'provides sub-second reconciliation latency. Periodic '
             'reconciliation still runs as a safety net. Requires '
             'enable_l2vni_trunk_reconciliation to be enabled. If disabled, '
             'only periodic reconciliation will be used.'),
    cfg.ListOpt(
        'ovn_nb_connection',
        default=None,
        help='OVN Northbound database connection string(s). For HA '
             'deployments, specify multiple comma-separated connection '
             'strings. Used to query ha_chassis_groups, logical switches, '
             'and router ports for L2VNI trunk reconciliation. If not '
             'specified, reads from [ovn] ovn_nb_connection (shared with '
             'Neutron ML2). Defaults to tcp:127.0.0.1:6641 if neither is '
             'configured.'),
    cfg.ListOpt(
        'ovn_sb_connection',
        default=None,
        help='OVN Southbound database connection string(s). For HA '
             'deployments, specify multiple comma-separated connection '
             'strings. Used to query chassis information and LLDP data for '
             'L2VNI trunk reconciliation. If not specified, reads from '
             '[ovn] ovn_sb_connection (shared with Neutron ML2). '
             'Defaults to tcp:127.0.0.1:6642 if neither is configured.'),
    cfg.IntOpt(
        'ovn_ovsdb_timeout',
        default=None,
        help='Timeout in seconds for OVN OVSDB connections. If not '
             'specified, reads from [ovn] ovsdb_connection_timeout '
             '(shared with Neutron ML2). Defaults to 180 if neither '
             'is configured.'),
    cfg.IntOpt(
        'ironic_cache_ttl',
        default=3600,
        min=300,
        help='Time-to-live in seconds for cached Ironic node and port data. '
             'Each system_id entry is cached independently and expires after '
             'this duration from when it was fetched. This avoids thundering '
             'herd issues when multiple agents are running. A small amount of '
             'jitter (10-20%%) is automatically added to spread cache refresh '
             'times. Default is 3600 seconds (1 hour). Minimum is 300 seconds '
             '(5 minutes) to avoid excessive API load.'),
    cfg.StrOpt(
        'ironic_conductor_group',
        default=None,
        help='Ironic conductor group to filter nodes when querying for '
             'local_link_information data. This allows the agent to only '
             'query nodes managed by a specific conductor group, reducing API '
             'load in large deployments. If not specified, all nodes are '
             'queried.'),
    cfg.StrOpt(
        'ironic_shard',
        default=None,
        help='Ironic shard to filter nodes when querying for '
             'local_link_information data. This allows the agent to only '
             'query nodes in a specific shard, reducing API load in large '
             'sharded deployments. If not specified, all nodes are queried.'),
]

# HA chassis group alignment options
BAREMETAL_AGENT_OPTS = [
    cfg.BoolOpt(
        'enable_ha_chassis_group_alignment',
        default=True,
        help='Enable HA chassis group alignment reconciliation for router '
             'ports on networks with baremetal external ports. This fixes '
             'Launchpad bug #1995078 where mismatched HA chassis group '
             'priorities between router gateway ports and baremetal external '
             'ports cause intermittent connectivity issues. When enabled, the '
             'agent ensures router ports use the same ha_chassis_group as '
             'baremetal external ports on the same network.'),
    cfg.IntOpt(
        'ha_chassis_group_alignment_interval',
        default=600,
        min=60,
        help='Interval in seconds between HA chassis group alignment '
             'reconciliation runs. This controls how frequently the agent '
             'checks for and fixes mismatched HA chassis groups. Default is '
             '600 seconds (10 minutes). Minimum is 60 seconds to avoid '
             'excessive API load.'),
    cfg.BoolOpt(
        'limit_ha_chassis_group_alignment_to_recent_changes_only',
        default=True,
        help='When enabled, HA chassis group alignment only checks resources '
             'created or updated within the time window specified by '
             'ha_chassis_group_alignment_window. This reduces reconciliation '
             'overhead by focusing on recently created resources that may '
             'have mismatched HA chassis groups. When disabled, performs full '
             'reconciliation of all resources on each run, which is more '
             'thorough but has higher API and database load.'),
    cfg.IntOpt(
        'ha_chassis_group_alignment_window',
        default=1200,
        min=0,
        help='Time window in seconds for checking recent resources when '
             'limit_ha_chassis_group_alignment_to_recent_changes_only is '
             'enabled. Default is 1200 seconds (20 minutes), which is 2x the '
             'default alignment interval. Resources created or updated within '
             'this window will be checked for HA chassis group alignment. '
             'Setting to 0 effectively disables windowing even if the limit '
             'flag is enabled.'),
]


def register_agent_opts(conf):
    """Register all agent configuration options.

    :param conf: oslo_config.cfg.ConfigOpts instance
    """
    conf.register_opts(L2VNI_OPTS, group='l2vni')
    conf.register_opts(BAREMETAL_AGENT_OPTS, group='baremetal_agent')


# Legacy function names for backwards compatibility
def register_l2vni_opts(conf):
    """Register L2VNI configuration options (deprecated).

    Prefer register_agent_opts() instead.

    :param conf: oslo_config.cfg.ConfigOpts instance
    """
    conf.register_opts(L2VNI_OPTS, group='l2vni')


def register_baremetal_agent_opts(conf):
    """Register baremetal agent configuration options (deprecated).

    Prefer register_agent_opts() instead.

    :param conf: oslo_config.cfg.ConfigOpts instance
    """
    conf.register_opts(BAREMETAL_AGENT_OPTS, group='baremetal_agent')


def list_opts():
    """Return a list of oslo_config options for config generation.

    :returns: list of (group_name, options) tuples
    """
    return [
        ('l2vni', L2VNI_OPTS),
        ('baremetal_agent', BAREMETAL_AGENT_OPTS)
    ]
