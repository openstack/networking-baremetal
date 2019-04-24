Deploying networking-baremetal and multi-tenant networking with DevStack
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

DevStack may be configured to deploy networking-baremetal Networking service
plugin together with networking-generic-switch for multi-tenant networking.
It is highly recommended to deploy on an expendable virtual machine and not on
your personal work station. Deploying networking-baremetal with DevStack
requires a machine running Ubuntu 14.04 (or later) or Fedora 20 (or later).

.. seealso::

    http://docs.openstack.org/devstack/latest

Create ``devstack/local.conf`` with minimal settings required to enable
networking-baremetal with ironic and networking-generic-switch for
multi-tenant networking. Here is an example of local.conf::


    [[local|localrc]]

    # Credentials
    ADMIN_PASSWORD=password
    DATABASE_PASSWORD=password
    RABBIT_PASSWORD=password
    SERVICE_PASSWORD=password
    SERVICE_TOKEN=password
    SWIFT_HASH=password
    SWIFT_TEMPURL_KEY=password

    # Install networking-generic-switch Neutron ML2 driver that interacts with OVS
    enable_plugin networking-generic-switch https://opendev.org/openstack/networking-generic-switch

    # Enable networking-baremetal plugin
    enable_plugin networking-baremetal https://opendev.org/openstack/networking-baremetal.git
    enable_service networking_baremetal
    enable_service ir-neutronagt

    # Add link local info when registering Ironic node
    IRONIC_USE_LINK_LOCAL=True

    IRONIC_ENABLED_NETWORK_INTERFACES=flat,neutron
    IRONIC_NETWORK_INTERFACE=neutron

    #Networking configuration
    OVS_PHYSICAL_BRIDGE=brbm
    PHYSICAL_NETWORK=mynetwork
    IRONIC_PROVISION_NETWORK_NAME=ironic-provision
    IRONIC_PROVISION_PROVIDER_NETWORK_TYPE=vlan
    IRONIC_PROVISION_SUBNET_PREFIX=10.0.5.0/24
    IRONIC_PROVISION_SUBNET_GATEWAY=10.0.5.1
    Q_PLUGIN=ml2
    ENABLE_TENANT_VLANS=True
    Q_ML2_TENANT_NETWORK_TYPE=vlan
    TENANT_VLAN_RANGE=100:150
    Q_USE_PROVIDERNET_FOR_PUBLIC=False

    # Enable segments service_plugin for routed networks
    Q_SERVICE_PLUGIN_CLASSES=neutron.services.l3_router.l3_router_plugin.L3RouterPlugin,segments
    IRONIC_USE_NEUTRON_SEGMENTS=True

    # Configure ironic from ironic devstack plugin.
    enable_plugin ironic https://opendev.org/openstack/ironic

    # Enable Ironic API and Ironic Conductor
    enable_service ironic
    enable_service ir-api
    enable_service ir-cond

    # Enable Neutron which is required by Ironic and disable nova-network.
    disable_service n-net
    disable_service n-novnc
    enable_service q-svc
    enable_service q-agt
    enable_service q-dhcp
    enable_service q-l3
    enable_service q-meta
    enable_service neutron

    # Enable Swift for agent_* drivers
    enable_service s-proxy
    enable_service s-object
    enable_service s-container
    enable_service s-account

    # Disable Horizon
    disable_service horizon

    # Disable Heat
    disable_service heat h-api h-api-cfn h-api-cw h-eng

    # Disable Cinder
    disable_service cinder c-sch c-api c-vol

    # Swift temp URL's are required for agent_* drivers.
    SWIFT_ENABLE_TEMPURLS=True

    # Create 3 virtual machines to pose as Ironic's baremetal nodes.
    IRONIC_VM_COUNT=3
    IRONIC_BAREMETAL_BASIC_OPS=True
    DEFAULT_INSTANCE_TYPE=baremetal

    # Enable additional hardware types, if needed.
    #IRONIC_ENABLED_HARDWARE_TYPES=ipmi,fake-hardware
    # Don't forget that many hardware types require enabling of additional
    # interfaces, most often power and management:
    #IRONIC_ENABLED_MANAGEMENT_INTERFACES=ipmitool,fake
    #IRONIC_ENABLED_POWER_INTERFACES=ipmitool,fake
    # The 'ipmi' hardware type's default deploy interface is 'iscsi'.
    # This would change the default to 'direct':
    #IRONIC_DEFAULT_DEPLOY_INTERFACE=direct

    # Change this to alter the default driver for nodes created by devstack.
    # This driver should be in the enabled list above.
    IRONIC_DEPLOY_DRIVER=ipmi

    # The parameters below represent the minimum possible values to create
    # functional nodes.
    IRONIC_VM_SPECS_RAM=1024
    IRONIC_VM_SPECS_DISK=10

    # Size of the ephemeral partition in GB. Use 0 for no ephemeral partition.
    IRONIC_VM_EPHEMERAL_DISK=0

    # To build your own IPA ramdisk from source, set this to True
    IRONIC_BUILD_DEPLOY_RAMDISK=False

    VIRT_DRIVER=ironic

    # By default, DevStack creates a 10.0.0.0/24 network for instances.
    # If this overlaps with the hosts network, you may adjust with the
    # following.
    NETWORK_GATEWAY=10.1.0.1
    FIXED_RANGE=10.1.0.0/24
    FIXED_NETWORK_SIZE=256

    # Log all output to files
    LOGFILE=$HOME/devstack.log
    LOGDIR=$HOME/logs
    IRONIC_VM_LOG_DIR=$HOME/ironic-bm-logs
