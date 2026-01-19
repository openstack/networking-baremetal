networking-baremetal plugin
---------------------------

This project's goal is to provide deep integration between the Networking
service and the Bare Metal service and advanced networking features like
notifications of port status changes and routed networks support in clouds
with Bare Metal service.

Features
--------

* **L2VNI Mechanism Driver**: Enables baremetal servers to connect to VXLAN
  and Geneve overlay networks by dynamically allocating VLAN segments and
  creating OVN localnet ports. See documentation for configuration details.
* **Port Status Notifications**: Real-time notifications of port status
  changes from the Bare Metal service to the Networking service.
* **Multi-tenant Network Support**: Advanced networking features for
  baremetal deployments with tenant isolation.

* Free software: Apache license
* Documentation: http://docs.openstack.org/networking-baremetal/latest
* Source: http://opendev.org/openstack/networking-baremetal
* Bugs: https://bugs.launchpad.net/networking-baremetal
* Release notes: https://docs.openstack.org/releasenotes/networking-baremetal/
