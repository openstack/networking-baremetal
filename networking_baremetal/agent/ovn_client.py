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

"""OVN client connections for agent."""

from oslo_config import cfg
from oslo_log import log as logging
from ovs.db import idl as ovs_idl
from ovs import stream
from ovsdbapp.backend.ovs_idl import connection
from ovsdbapp.backend.ovs_idl import idlutils

CONF = cfg.CONF
LOG = logging.getLogger(__name__)

_OVN_NB_IDL = None
_OVN_SB_IDL = None


def _configure_ovn_ssl():
    """Configure SSL settings for OVN connections.

    Reads SSL certificate configuration from [ovn] section and sets
    up the OVS Stream SSL parameters. This must be called before
    creating any IDL connections when using SSL.
    """
    try:
        if not hasattr(CONF, 'ovn'):
            return

        # Set SSL CA certificate if configured
        if hasattr(CONF.ovn, 'ovn_sb_ca_cert') and CONF.ovn.ovn_sb_ca_cert:
            stream.Stream.ssl_set_ca_cert_file(CONF.ovn.ovn_sb_ca_cert)
            LOG.debug("Configured OVN SSL CA cert: %s",
                      CONF.ovn.ovn_sb_ca_cert)

        # Set SSL client certificate if configured
        if (hasattr(CONF.ovn, 'ovn_sb_certificate')
                and CONF.ovn.ovn_sb_certificate):
            stream.Stream.ssl_set_certificate_file(
                CONF.ovn.ovn_sb_certificate)
            LOG.debug("Configured OVN SSL certificate: %s",
                      CONF.ovn.ovn_sb_certificate)

        # Set SSL private key if configured
        if (hasattr(CONF.ovn, 'ovn_sb_private_key')
                and CONF.ovn.ovn_sb_private_key):
            stream.Stream.ssl_set_private_key_file(
                CONF.ovn.ovn_sb_private_key)
            LOG.debug("Configured OVN SSL private key: %s",
                      CONF.ovn.ovn_sb_private_key)

    except (cfg.NoSuchOptError, AttributeError) as e:
        LOG.debug("Could not configure OVN SSL settings: %s", e)


def _get_ovn_nb_connection():
    """Get OVN NB connection string from config with fallback.

    Priority order:
    1. [l2vni] ovn_nb_connection (explicit agent override)
    2. [ovn] ovn_nb_connection (shared Neutron ML2 config)
    3. Hardcoded default: tcp:127.0.0.1:6641

    :returns: OVN NB connection string (comma-separated if multiple)
    """
    # If explicitly set in [l2vni], use it
    if CONF.l2vni.ovn_nb_connection is not None:
        conn = CONF.l2vni.ovn_nb_connection
        # Convert list to comma-separated string for ovsdbapp
        if isinstance(conn, list):
            return ','.join(conn)
        return conn

    # Try to read from [ovn] section (Neutron ML2 config)
    try:
        if hasattr(CONF, 'ovn') and hasattr(CONF.ovn, 'ovn_nb_connection'):
            ovn_conn = CONF.ovn.ovn_nb_connection
            if ovn_conn:
                # Neutron's ovn_nb_connection is a ListOpt for HA support
                # Convert to comma-separated string if it's a list
                if isinstance(ovn_conn, list):
                    ovn_conn = ','.join(ovn_conn)
                LOG.debug("Using OVN NB connection from [ovn] section: %s",
                          ovn_conn)
                return ovn_conn
    except (cfg.NoSuchOptError, AttributeError):
        pass

    # Fallback to hardcoded default
    LOG.debug("Using default OVN NB connection: tcp:127.0.0.1:6641")
    return 'tcp:127.0.0.1:6641'


def _get_ovn_sb_connection():
    """Get OVN SB connection string from config with fallback.

    Priority order:
    1. [l2vni] ovn_sb_connection (explicit agent override)
    2. [ovn] ovn_sb_connection (shared Neutron ML2 config)
    3. Hardcoded default: tcp:127.0.0.1:6642

    :returns: OVN SB connection string (comma-separated if multiple)
    """
    # If explicitly set in [l2vni], use it
    if CONF.l2vni.ovn_sb_connection is not None:
        conn = CONF.l2vni.ovn_sb_connection
        # Convert list to comma-separated string for ovsdbapp
        if isinstance(conn, list):
            return ','.join(conn)
        return conn

    # Try to read from [ovn] section (Neutron ML2 config)
    try:
        if hasattr(CONF, 'ovn') and hasattr(CONF.ovn, 'ovn_sb_connection'):
            ovn_conn = CONF.ovn.ovn_sb_connection
            if ovn_conn:
                # Neutron's ovn_sb_connection is a ListOpt for HA support
                # Convert to comma-separated string if it's a list
                if isinstance(ovn_conn, list):
                    ovn_conn = ','.join(ovn_conn)
                LOG.debug("Using OVN SB connection from [ovn] section: %s",
                          ovn_conn)
                return ovn_conn
    except (cfg.NoSuchOptError, AttributeError):
        pass

    # Fallback to hardcoded default
    LOG.debug("Using default OVN SB connection: tcp:127.0.0.1:6642")
    return 'tcp:127.0.0.1:6642'


def _get_ovn_ovsdb_timeout():
    """Get OVN OVSDB timeout from config with fallback.

    Priority order:
    1. [l2vni] ovn_ovsdb_timeout (explicit agent override)
    2. [ovn] ovsdb_connection_timeout (shared Neutron ML2 config)
    3. Hardcoded default: 180

    :returns: OVN OVSDB timeout in seconds
    """
    # If explicitly set in [l2vni], use it
    if CONF.l2vni.ovn_ovsdb_timeout is not None:
        return CONF.l2vni.ovn_ovsdb_timeout

    # Try to read from [ovn] section (Neutron ML2 config)
    try:
        if (hasattr(CONF, 'ovn')
                and hasattr(CONF.ovn, 'ovsdb_connection_timeout')):
            ovn_timeout = CONF.ovn.ovsdb_connection_timeout
            if ovn_timeout:
                LOG.debug("Using OVN OVSDB timeout from [ovn] section: %d",
                          ovn_timeout)
                return ovn_timeout
    except (cfg.NoSuchOptError, AttributeError):
        pass

    # Fallback to hardcoded default
    LOG.debug("Using default OVN OVSDB timeout: 180")
    return 180


def get_ovn_nb_idl():
    """Get OVN Northbound IDL connection.

    :returns: OVN NB IDL instance
    """
    global _OVN_NB_IDL

    if _OVN_NB_IDL is None:
        try:
            # Get connection string from config (with fallback to [ovn])
            conn_string = _get_ovn_nb_connection()
            timeout = _get_ovn_ovsdb_timeout()
            LOG.debug("Connecting to OVN NB: %s", conn_string)

            # Configure SSL if using SSL connections
            _configure_ovn_ssl()

            # Create IDL connection
            helper = idlutils.get_schema_helper(conn_string,
                                                'OVN_Northbound')
            helper.register_all()

            # Create IDL instance from helper
            idl = ovs_idl.Idl(conn_string, helper)

            ovn_conn = connection.Connection(
                idl,
                timeout=timeout
            )
            ovn_conn.start()

            # Store the raw IDL for direct table access
            _OVN_NB_IDL = idl
            LOG.info("Connected to OVN Northbound database")

        except Exception:
            LOG.exception("Failed to connect to OVN Northbound database")
            raise

    return _OVN_NB_IDL


def get_ovn_sb_idl():
    """Get OVN Southbound IDL connection.

    :returns: OVN SB IDL instance
    """
    global _OVN_SB_IDL

    if _OVN_SB_IDL is None:
        try:
            # Get connection string from config (with fallback to [ovn])
            conn_string = _get_ovn_sb_connection()
            timeout = _get_ovn_ovsdb_timeout()
            LOG.debug("Connecting to OVN SB: %s", conn_string)

            # Configure SSL if using SSL connections
            _configure_ovn_ssl()

            # Create IDL connection
            helper = idlutils.get_schema_helper(conn_string,
                                                'OVN_Southbound')
            helper.register_all()

            # Create IDL instance from helper
            idl = ovs_idl.Idl(conn_string, helper)

            ovn_conn = connection.Connection(
                idl,
                timeout=timeout
            )
            ovn_conn.start()

            # Store the raw IDL for direct table access
            _OVN_SB_IDL = idl
            LOG.info("Connected to OVN Southbound database")

        except Exception:
            LOG.exception("Failed to connect to OVN Southbound database")
            raise

    return _OVN_SB_IDL
