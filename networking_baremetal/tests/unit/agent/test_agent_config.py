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

from neutron.tests import base as tests_base
from oslo_config import cfg

from networking_baremetal.agent import agent_config


CONF = cfg.CONF


class TestAgentConfig(tests_base.BaseTestCase):
    """Test cases for agent configuration options."""

    def setUp(self):
        super(TestAgentConfig, self).setUp()
        # Register options for testing
        agent_config.register_agent_opts(CONF)

    def test_register_agent_opts(self):
        """Test agent options are registered correctly."""
        self.assertIn('l2vni', CONF)
        self.assertIn('baremetal_agent', CONF)

    def test_enable_l2vni_trunk_reconciliation_default(self):
        """Test enable_l2vni_trunk_reconciliation default value."""
        self.assertTrue(CONF.l2vni.enable_l2vni_trunk_reconciliation)

    def test_enable_l2vni_trunk_reconciliation_can_be_set(self):
        """Test enable_l2vni_trunk_reconciliation can be set."""
        CONF.set_override('enable_l2vni_trunk_reconciliation', True,
                          group='l2vni')
        self.assertTrue(CONF.l2vni.enable_l2vni_trunk_reconciliation)

    def test_l2vni_reconciliation_interval_default(self):
        """Test l2vni_reconciliation_interval default value."""
        self.assertEqual(180, CONF.l2vni.l2vni_reconciliation_interval)

    def test_l2vni_reconciliation_interval_can_be_set(self):
        """Test l2vni_reconciliation_interval can be set."""
        CONF.set_override('l2vni_reconciliation_interval', 600,
                          group='l2vni')
        self.assertEqual(600, CONF.l2vni.l2vni_reconciliation_interval)

    def test_l2vni_reconciliation_interval_minimum(self):
        """Test l2vni_reconciliation_interval respects minimum."""
        # Should raise error if set below minimum (min is 30)
        self.assertRaises(ValueError,
                          CONF.set_override,
                          'l2vni_reconciliation_interval', 10,
                          group='l2vni')

    def test_l2vni_network_nodes_config_default(self):
        """Test l2vni_network_nodes_config default value."""
        self.assertEqual('/etc/neutron/l2vni_network_nodes.yaml',
                         CONF.l2vni.l2vni_network_nodes_config)

    def test_l2vni_network_nodes_config_can_be_set(self):
        """Test l2vni_network_nodes_config can be set."""
        CONF.set_override('l2vni_network_nodes_config',
                          '/custom/path/config.yaml',
                          group='l2vni')
        self.assertEqual('/custom/path/config.yaml',
                         CONF.l2vni.l2vni_network_nodes_config)

    def test_l2vni_auto_create_networks_default(self):
        """Test l2vni_auto_create_networks default value."""
        self.assertTrue(CONF.l2vni.l2vni_auto_create_networks)

    def test_l2vni_auto_create_networks_can_be_set(self):
        """Test l2vni_auto_create_networks can be set."""
        CONF.set_override('l2vni_auto_create_networks', False,
                          group='l2vni')
        self.assertFalse(CONF.l2vni.l2vni_auto_create_networks)

    def test_l2vni_subport_anchor_network_default(self):
        """Test l2vni_subport_anchor_network default value."""
        self.assertEqual('l2vni-subport-anchor',
                         CONF.l2vni.l2vni_subport_anchor_network)

    def test_l2vni_subport_anchor_network_can_be_set(self):
        """Test l2vni_subport_anchor_network can be set."""
        CONF.set_override('l2vni_subport_anchor_network',
                          'custom-anchor-network',
                          group='l2vni')
        self.assertEqual('custom-anchor-network',
                         CONF.l2vni.l2vni_subport_anchor_network)

    def test_enable_ha_chassis_group_alignment_default(self):
        """Test enable_ha_chassis_group_alignment default value."""
        self.assertTrue(CONF.baremetal_agent
                        .enable_ha_chassis_group_alignment)

    def test_ha_chassis_group_alignment_interval_default(self):
        """Test ha_chassis_group_alignment_interval default value."""
        self.assertEqual(600, CONF.baremetal_agent
                         .ha_chassis_group_alignment_interval)

    def test_list_opts(self):
        """Test list_opts returns correct format."""
        opts = agent_config.list_opts()
        self.assertIsInstance(opts, list)
        self.assertEqual(2, len(opts))

        # Check L2VNI options
        l2vni_group_name, l2vni_options = opts[0]
        self.assertEqual('l2vni', l2vni_group_name)
        self.assertEqual(agent_config.L2VNI_OPTS, l2vni_options)

        # Check baremetal agent options
        bm_group_name, bm_options = opts[1]
        self.assertEqual('baremetal_agent', bm_group_name)
        self.assertEqual(agent_config.BAREMETAL_AGENT_OPTS, bm_options)

    def test_all_options_have_help_text(self):
        """Test all configuration options have help text."""
        for opt in agent_config.L2VNI_OPTS:
            self.assertIsNotNone(opt.help)
            self.assertGreater(len(opt.help), 0)
        for opt in agent_config.BAREMETAL_AGENT_OPTS:
            self.assertIsNotNone(opt.help)
            self.assertGreater(len(opt.help), 0)

    def test_boolean_options_have_defaults(self):
        """Test boolean options have explicit default values."""
        boolean_opts = [opt for opt in agent_config.L2VNI_OPTS
                        if isinstance(opt, cfg.BoolOpt)]
        boolean_opts += [opt for opt in agent_config.BAREMETAL_AGENT_OPTS
                         if isinstance(opt, cfg.BoolOpt)]
        for opt in boolean_opts:
            self.assertIsNotNone(opt.default)
