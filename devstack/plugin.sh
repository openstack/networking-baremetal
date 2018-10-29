#!/usr/bin/env bash
# plugin.sh - DevStack plugin.sh dispatch script template

echo_summary "networking-baremetal devstack plugin.sh called: $1/$2"
source $DEST/networking-baremetal/devstack/lib/networking-baremetal

enable_python3_package neworking-baremetal

# check for service enabled
if is_service_enabled networking_baremetal; then

    if [[ "$1" == "stack" && "$2" == "install" ]]; then
        # Perform installation of service source
        echo_summary "Installing Networking Baremetal ML2"
        install_networking_baremetal

    elif [[ "$1" == "stack" && "$2" == "post-config" ]]; then
        # Configure after the other layer 1 and 2 services have been configured
        echo_summary "Configuring Networking Baremetal Ml2"
        configure_networking_baremetal
        echo_summary "Configuring Networking Baremetal Neutron Agent"
        configure_networking_baremetal_neutron_agent
        echo_summary "Starting Networking Baremetal Neutron Agent"
        start_networking_baremetal_neutron_agent
    fi

    if [[ "$1" == "unstack" ]]; then
        echo_summary "Cleaning Networking Baremetal Ml2"
        cleanup_networking_baremetal
        echo_summary "Cleaning Networking Baremtal Neutron Agent"
        stop_networking_baremetal_neutron_agent
    fi
fi
