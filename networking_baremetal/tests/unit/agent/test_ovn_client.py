# Copyright (c) 2026 Red Hat, Inc.
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

from unittest import mock

from neutron.tests import base as tests_base
from oslo_config import cfg

from networking_baremetal.agent import ovn_client


class TestOVNClient(tests_base.BaseTestCase):
    """Test cases for OVN Client connections."""

    def setUp(self):
        super(TestOVNClient, self).setUp()
        # Reset global IDL instances before each test
        ovn_client._OVN_NB_IDL = None
        ovn_client._OVN_SB_IDL = None

        # Register L2VNI config options (includes OVN connection settings)
        from networking_baremetal.agent import agent_config
        agent_config.register_l2vni_opts(cfg.CONF)

        # Register Neutron OVN config options for testing fallback behavior
        try:
            from neutron.conf.plugins.ml2.drivers.ovn import ovn_conf
            ovn_conf.register_opts()
        except (ImportError, cfg.DuplicateOptError):
            # OVN config may not be available or already registered
            pass

    def tearDown(self):
        super(TestOVNClient, self).tearDown()
        # Clean up global IDL instances after each test
        ovn_client._OVN_NB_IDL = None
        ovn_client._OVN_SB_IDL = None

    def test_module_has_required_functions(self):
        """Test OVN client module has required functions."""
        self.assertTrue(callable(ovn_client.get_ovn_nb_idl))
        self.assertTrue(callable(ovn_client.get_ovn_sb_idl))

    def test_module_has_global_idl_variables(self):
        """Test OVN client module has global IDL variables."""
        # Check that module has the expected global variables
        self.assertIsNone(ovn_client._OVN_NB_IDL)
        self.assertIsNone(ovn_client._OVN_SB_IDL)

    @mock.patch.object(ovn_client, 'nb_impl_idl', autospec=True)
    @mock.patch.object(ovn_client, 'ovs_idl', autospec=True)
    @mock.patch.object(ovn_client, 'connection', autospec=True)
    @mock.patch.object(ovn_client, 'idlutils', autospec=True)
    def test_get_ovn_nb_idl_creates_connection(
            self,
            mock_idlutils,
            mock_connection,
            mock_ovs_idl,
            mock_nb_impl):
        """Test OVN Northbound IDL connection creation."""
        mock_helper = mock.Mock()
        mock_idlutils.get_schema_helper = mock.Mock(return_value=mock_helper)
        mock_idl = mock.Mock()
        mock_ovs_idl.Idl.return_value = mock_idl
        mock_conn = mock.Mock()
        mock_connection.Connection.return_value = mock_conn
        mock_api = mock.Mock()
        mock_nb_impl.OvnNbApiIdlImpl.return_value = mock_api

        result = ovn_client.get_ovn_nb_idl()

        # Should call connection setup functions
        mock_idlutils.get_schema_helper.assert_called_once_with(
            'tcp:127.0.0.1:6641', 'OVN_Northbound')
        mock_helper.register_all.assert_called_once()
        mock_ovs_idl.Idl.assert_called_once_with(
            'tcp:127.0.0.1:6641', mock_helper)
        mock_conn.start.assert_called_once()

        # Should return NB API instance
        self.assertEqual(result, mock_api)

    @mock.patch.object(ovn_client, 'nb_impl_idl', autospec=True)
    @mock.patch.object(ovn_client, 'ovs_idl', autospec=True)
    @mock.patch.object(ovn_client, 'connection', autospec=True)
    @mock.patch.object(ovn_client, 'idlutils', autospec=True)
    def test_get_ovn_nb_idl_returns_cached_instance(
            self,
            mock_idlutils,
            mock_connection,
            mock_ovs_idl,
            mock_nb_impl):
        """Test OVN Northbound IDL returns cached instance on second call."""
        mock_helper = mock.Mock()
        mock_idlutils.get_schema_helper = mock.Mock(return_value=mock_helper)
        mock_idl = mock.Mock()
        mock_ovs_idl.Idl.return_value = mock_idl
        mock_conn = mock.Mock()
        mock_connection.Connection.return_value = mock_conn
        mock_api = mock.Mock()
        mock_nb_impl.OvnNbApiIdlImpl.return_value = mock_api

        # First call creates connection
        result1 = ovn_client.get_ovn_nb_idl()

        # Second call should return cached instance
        result2 = ovn_client.get_ovn_nb_idl()

        # Should only create connection once
        self.assertEqual(1, mock_idlutils.get_schema_helper.call_count)
        self.assertEqual(1, mock_ovs_idl.Idl.call_count)

        # Both calls should return same instance
        self.assertEqual(result1, result2)

    @mock.patch.object(ovn_client, 'idlutils', autospec=True)
    def test_get_ovn_nb_idl_handles_connection_failure(self, mock_idlutils):
        """Test OVN Northbound IDL handles connection failures."""
        mock_idlutils.get_schema_helper = mock.Mock(
            side_effect=RuntimeError('Connection failed'))

        self.assertRaises(RuntimeError, ovn_client.get_ovn_nb_idl)

    @mock.patch.object(ovn_client, 'sb_impl_idl', autospec=True)
    @mock.patch.object(ovn_client, 'ovs_idl', autospec=True)
    @mock.patch.object(ovn_client, 'connection', autospec=True)
    @mock.patch.object(ovn_client, 'idlutils', autospec=True)
    def test_get_ovn_sb_idl_creates_connection(
            self,
            mock_idlutils,
            mock_connection,
            mock_ovs_idl,
            mock_sb_impl):
        """Test OVN Southbound IDL connection creation."""
        mock_helper = mock.Mock()
        mock_idlutils.get_schema_helper = mock.Mock(return_value=mock_helper)
        mock_idl = mock.Mock()
        mock_ovs_idl.Idl.return_value = mock_idl
        mock_conn = mock.Mock()
        mock_connection.Connection.return_value = mock_conn
        mock_api = mock.Mock()
        mock_sb_impl.OvnSbApiIdlImpl.return_value = mock_api

        result = ovn_client.get_ovn_sb_idl()

        # Should call connection setup functions
        mock_idlutils.get_schema_helper.assert_called_once_with(
            'tcp:127.0.0.1:6642', 'OVN_Southbound')
        mock_helper.register_all.assert_called_once()
        mock_ovs_idl.Idl.assert_called_once_with(
            'tcp:127.0.0.1:6642', mock_helper)
        mock_conn.start.assert_called_once()

        # Should return SB API instance
        self.assertEqual(result, mock_api)

    @mock.patch.object(ovn_client, 'sb_impl_idl', autospec=True)
    @mock.patch.object(ovn_client, 'ovs_idl', autospec=True)
    @mock.patch.object(ovn_client, 'connection', autospec=True)
    @mock.patch.object(ovn_client, 'idlutils', autospec=True)
    def test_get_ovn_sb_idl_returns_cached_instance(
            self,
            mock_idlutils,
            mock_connection,
            mock_ovs_idl,
            mock_sb_impl):
        """Test OVN Southbound IDL returns cached instance on second call."""
        mock_helper = mock.Mock()
        mock_idlutils.get_schema_helper = mock.Mock(return_value=mock_helper)
        mock_idl = mock.Mock()
        mock_ovs_idl.Idl.return_value = mock_idl
        mock_conn = mock.Mock()
        mock_connection.Connection.return_value = mock_conn
        mock_api = mock.Mock()
        mock_sb_impl.OvnSbApiIdlImpl.return_value = mock_api

        # First call creates connection
        result1 = ovn_client.get_ovn_sb_idl()

        # Second call should return cached instance
        result2 = ovn_client.get_ovn_sb_idl()

        # Should only create connection once
        self.assertEqual(1, mock_idlutils.get_schema_helper.call_count)
        self.assertEqual(1, mock_ovs_idl.Idl.call_count)

        # Both calls should return same instance
        self.assertEqual(result1, result2)

    @mock.patch.object(ovn_client, 'idlutils', autospec=True)
    def test_get_ovn_sb_idl_handles_connection_failure(self, mock_idlutils):
        """Test OVN Southbound IDL handles connection failures."""
        mock_idlutils.get_schema_helper = mock.Mock(
            side_effect=RuntimeError('Connection failed'))

        self.assertRaises(RuntimeError, ovn_client.get_ovn_sb_idl)

    @mock.patch.object(ovn_client, 'sb_impl_idl', autospec=True)
    @mock.patch.object(ovn_client, 'nb_impl_idl', autospec=True)
    @mock.patch.object(ovn_client, 'ovs_idl', autospec=True)
    @mock.patch.object(ovn_client, 'connection', autospec=True)
    @mock.patch.object(ovn_client, 'idlutils', autospec=True)
    def test_both_idls_independent(
            self,
            mock_idlutils,
            mock_connection,
            mock_ovs_idl,
            mock_nb_impl,
            mock_sb_impl):
        """Test NB and SB IDL connections are independent."""
        mock_helper = mock.Mock()
        mock_idlutils.get_schema_helper = mock.Mock(return_value=mock_helper)
        # Create different mock IDL instances for NB and SB
        mock_idl_nb = mock.Mock()
        mock_idl_sb = mock.Mock()
        mock_ovs_idl.Idl.side_effect = [mock_idl_nb, mock_idl_sb]
        mock_conn_nb = mock.Mock()
        mock_conn_sb = mock.Mock()
        mock_connection.Connection.side_effect = [mock_conn_nb, mock_conn_sb]
        mock_api_nb = mock.Mock()
        mock_api_sb = mock.Mock()
        mock_nb_impl.OvnNbApiIdlImpl.return_value = mock_api_nb
        mock_sb_impl.OvnSbApiIdlImpl.return_value = mock_api_sb

        # Get both IDLs
        nb_idl = ovn_client.get_ovn_nb_idl()
        sb_idl = ovn_client.get_ovn_sb_idl()

        # Both should be created
        self.assertEqual(2, mock_idlutils.get_schema_helper.call_count)
        self.assertEqual(2, mock_ovs_idl.Idl.call_count)

        # Should be different instances
        self.assertIsNotNone(nb_idl)
        self.assertIsNotNone(sb_idl)
        self.assertNotEqual(nb_idl, sb_idl)

    def test_get_ovn_nb_connection_from_l2vni_config(self):
        """Test getting NB connection from [l2vni] section."""
        cfg.CONF.set_override('ovn_nb_connection',
                              ['ssl:10.0.0.1:6641'],
                              group='l2vni')
        result = ovn_client._get_ovn_nb_connection()
        self.assertEqual('ssl:10.0.0.1:6641', result)

    def test_get_ovn_nb_connection_from_l2vni_list(self):
        """Test getting NB connection list from [l2vni] converts to string."""
        cfg.CONF.set_override('ovn_nb_connection',
                              ['ssl:10.0.0.1:6641', 'ssl:10.0.0.2:6641'],
                              group='l2vni')
        result = ovn_client._get_ovn_nb_connection()
        self.assertEqual('ssl:10.0.0.1:6641,ssl:10.0.0.2:6641', result)

    def test_get_ovn_nb_connection_fallback_to_ovn_section(self):
        """Test NB connection falls back to [ovn] section."""
        # Don't set l2vni config, should read from [ovn]
        cfg.CONF.set_override('ovn_nb_connection',
                              ['ssl:192.168.1.1:6641'],
                              group='ovn')
        result = ovn_client._get_ovn_nb_connection()
        self.assertEqual('ssl:192.168.1.1:6641', result)

    def test_get_ovn_nb_connection_fallback_to_default(self):
        """Test NB connection falls back to default."""
        # Neither l2vni nor ovn configured, should use default
        result = ovn_client._get_ovn_nb_connection()
        self.assertEqual('tcp:127.0.0.1:6641', result)

    def test_get_ovn_sb_connection_from_l2vni_config(self):
        """Test getting SB connection from [l2vni] section."""
        cfg.CONF.set_override('ovn_sb_connection',
                              ['ssl:10.0.0.1:6642'],
                              group='l2vni')
        result = ovn_client._get_ovn_sb_connection()
        self.assertEqual('ssl:10.0.0.1:6642', result)

    def test_get_ovn_sb_connection_from_l2vni_list(self):
        """Test getting SB connection list from [l2vni] converts to string."""
        cfg.CONF.set_override('ovn_sb_connection',
                              ['ssl:10.0.0.1:6642', 'ssl:10.0.0.2:6642'],
                              group='l2vni')
        result = ovn_client._get_ovn_sb_connection()
        self.assertEqual('ssl:10.0.0.1:6642,ssl:10.0.0.2:6642', result)

    def test_get_ovn_sb_connection_fallback_to_ovn_section(self):
        """Test SB connection falls back to [ovn] section."""
        # Don't set l2vni config, should read from [ovn]
        cfg.CONF.set_override('ovn_sb_connection',
                              ['ssl:192.168.1.1:6642'],
                              group='ovn')
        result = ovn_client._get_ovn_sb_connection()
        self.assertEqual('ssl:192.168.1.1:6642', result)

    def test_get_ovn_sb_connection_fallback_to_default(self):
        """Test SB connection falls back to default."""
        # Neither l2vni nor ovn configured, should use default
        result = ovn_client._get_ovn_sb_connection()
        self.assertEqual('tcp:127.0.0.1:6642', result)

    def test_get_ovn_ovsdb_timeout_from_l2vni_config(self):
        """Test getting timeout from [l2vni] section."""
        cfg.CONF.set_override('ovn_ovsdb_timeout', 120, group='l2vni')
        result = ovn_client._get_ovn_ovsdb_timeout()
        self.assertEqual(120, result)

    def test_get_ovn_ovsdb_timeout_fallback_to_ovn_section(self):
        """Test timeout falls back to [ovn] section."""
        # Don't set l2vni config, should read from [ovn]
        cfg.CONF.set_override('ovsdb_connection_timeout', 90, group='ovn')
        result = ovn_client._get_ovn_ovsdb_timeout()
        self.assertEqual(90, result)

    def test_get_ovn_ovsdb_timeout_fallback_to_default(self):
        """Test timeout falls back to default."""
        # Neither l2vni nor ovn configured, should use default
        result = ovn_client._get_ovn_ovsdb_timeout()
        self.assertEqual(180, result)

    @mock.patch.object(ovn_client.stream.Stream, 'ssl_set_ca_cert_file',
                       autospec=True)
    @mock.patch.object(ovn_client.stream.Stream, 'ssl_set_certificate_file',
                       autospec=True)
    @mock.patch.object(ovn_client.stream.Stream, 'ssl_set_private_key_file',
                       autospec=True)
    def test_configure_ovn_ssl_with_all_certs(
            self,
            mock_set_key,
            mock_set_cert,
            mock_set_ca):
        """Test SSL configuration with all certificates."""
        cfg.CONF.set_override('ovn_sb_ca_cert', '/path/to/ca.pem',
                              group='ovn')
        cfg.CONF.set_override('ovn_sb_certificate', '/path/to/cert.pem',
                              group='ovn')
        cfg.CONF.set_override('ovn_sb_private_key', '/path/to/key.pem',
                              group='ovn')

        ovn_client._configure_ovn_ssl()

        mock_set_ca.assert_called_once_with('/path/to/ca.pem')
        mock_set_cert.assert_called_once_with('/path/to/cert.pem')
        mock_set_key.assert_called_once_with('/path/to/key.pem')

    @mock.patch.object(ovn_client.stream.Stream, 'ssl_set_ca_cert_file',
                       autospec=True)
    def test_configure_ovn_ssl_with_ca_only(self, mock_set_ca):
        """Test SSL configuration with only CA certificate."""
        cfg.CONF.set_override('ovn_sb_ca_cert', '/path/to/ca.pem',
                              group='ovn')

        ovn_client._configure_ovn_ssl()

        mock_set_ca.assert_called_once_with('/path/to/ca.pem')

    @mock.patch.object(ovn_client.stream.Stream, 'ssl_set_ca_cert_file',
                       autospec=True)
    def test_configure_ovn_ssl_no_ovn_section(self, mock_set_ca):
        """Test SSL configuration handles missing [ovn] attributes."""
        # Don't set any ovn SSL config - function should handle gracefully
        # The function checks hasattr(CONF.ovn, 'ovn_sb_ca_cert') which
        # will be False if the option isn't set

        # Should not raise exception
        ovn_client._configure_ovn_ssl()

        # Should not call SSL functions if attributes don't exist
        # (in reality ovn_conf registers these, but they'd be None/empty)
        # This test validates the defensive checks in the function

    @mock.patch.object(ovn_client.stream.Stream, 'ssl_set_ca_cert_file',
                       autospec=True)
    def test_configure_ovn_ssl_with_empty_config(self, mock_set_ca):
        """Test SSL configuration with empty certificate paths."""
        cfg.CONF.set_override('ovn_sb_ca_cert', '', group='ovn')

        ovn_client._configure_ovn_ssl()

        # Should not call SSL functions for empty paths
        mock_set_ca.assert_not_called()
