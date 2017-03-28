=====================
Developer quick-start
=====================

This is a quick walk through to get you started developing code for
networking-baremetal. This assumes you are already familiar with
submitting code reviews to an OpenStack project.

Deploying networking-baremetal with DevStack
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

DevStack may be configured to deploy networking-baremetal Networking service
plugin. It is highly recommended to deploy on an expendable virtual machine
and not on your personal work station. Deploying networking-baremetal with
DevStack requires a machine running Ubuntu 14.04 (or later) or
Fedora 20 (or later).

.. seealso::

    http://docs.openstack.org/devstack/latest

Create ``devstack/local.conf`` with minimal settings required to enable
networking-baremetal with ironic. Here is an example of local.conf::


    cd devstack
    cat >local.conf <<END
    [[local|localrc]]
    # Credentials
    ADMIN_PASSWORD=password
    DATABASE_PASSWORD=password
    RABBIT_PASSWORD=password
    SERVICE_PASSWORD=password
    SERVICE_TOKEN=password
    SWIFT_HASH=password
    SWIFT_TEMPURL_KEY=password

    # Enable networking-baremetal plugin
    enable_plugin networking-baremetal git://git.openstack.org/openstack/networking-baremetal.git

    # Enable ironic plugin
    enable_plugin ironic git://git.openstack.org/openstack/ironic
    enable_service networking_baremetal

    # Enable neutron which is required by ironic and disable nova-network.
    disable_service n-net
    disable_service n-novnc
    enable_service q-svc
    enable_service q-agt
    enable_service q-dhcp
    enable_service q-l3
    enable_service q-meta
    enable_service neutron

    # Enable swift for agent_* drivers
    enable_service s-proxy
    enable_service s-object
    enable_service s-container
    enable_service s-account

    # Disable horizon
    disable_service horizon

    # Disable heat
    disable_service heat h-api h-api-cfn h-api-cw h-eng

    # Disable cinder
    disable_service cinder c-sch c-api c-vol

    # Swift temp URL's are required for agent_* drivers.
    SWIFT_ENABLE_TEMPURLS=True

    # Create 3 virtual machines to pose as ironic's baremetal nodes.
    IRONIC_VM_COUNT=3
    IRONIC_VM_SSH_PORT=22
    IRONIC_BAREMETAL_BASIC_OPS=True
    DEFAULT_INSTANCE_TYPE=baremetal

    # Enable ironic drivers.
    IRONIC_ENABLED_DRIVERS=fake,agent_ipmitool,pxe_ipmitool

    # Change this to alter the default driver for nodes created by devstack.
    # This driver should be in the enabled list above.
    IRONIC_DEPLOY_DRIVER=agent_ipmitool

    # The parameters below represent the minimum possible values to create
    # functional nodes.
    IRONIC_VM_SPECS_RAM=1280
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

    END
