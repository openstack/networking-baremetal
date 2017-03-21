#!/usr/bin/env bash
# plugin.sh - DevStack plugin.sh dispatch script template

NETWORKING_BAREMETAL_DIR=${NETWORKING_BAREMETAL_DIR:-$DEST/networking-baremetal}
NETWORKING_BAREMETAL_DATA_DIR=""$DATA_DIR/networking-baremetal""

function install_networking_baremetal {
    setup_develop $NETWORKING_BAREMETAL_DIR
}


function configure_networking_baremetal {
    if [[ -z "$Q_ML2_PLUGIN_MECHANISM_DRIVERS" ]]; then
        Q_ML2_PLUGIN_MECHANISM_DRIVERS='baremetal'
    else
        if [[ ! $Q_ML2_PLUGIN_MECHANISM_DRIVERS =~ $(echo '\<baremetal\>') ]]; then
            Q_ML2_PLUGIN_MECHANISM_DRIVERS+=',baremetal'
        fi
    fi
    populate_ml2_config /$Q_PLUGIN_CONF_FILE ml2 mechanism_drivers=$Q_ML2_PLUGIN_MECHANISM_DRIVERS
}

function cleanup_networking_baremetal {
    rm -rf $NETWORKING_BAREMETAL_DATA_DIR
}

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
    fi

    if [[ "$1" == "unstack" ]]; then
        echo_summary "Cleaning Networking Baremetal Ml2"
        cleanup_networking_baremetal
    fi
fi
