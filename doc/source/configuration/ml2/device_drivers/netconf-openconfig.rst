Device driver - netconf-openconfig
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``netconf-openconfig`` device driver uses the Network Configuration
Protocol (`NETCONF <https://datatracker.ietf.org/group/netconf/about/>`__)
and open source vendor-neutral  `OpenConfig <http://openconfig.net/>`__ YANG
models.

This driver has been tested with the following switch vendor/operating systems:

* Cisco NXOS
* Arista vEOS

**Example configuration for Cisco NXOS device**:

.. code-block:: ini

  [networking_baremetal]
  enabled_devices = nexus.example.net

  [nexus.example.net]
  driver = netconf-openconfig
  device_params = name:nexus
  switch_info = nexus
  switch_id = 00:53:00:0a:0a:0a
  host = nexus.example.net
  username = user
  key_filename = /etc/neutron/ssh_keys/nexus_sshkey

**Example configuration for Arista EOS device**:

.. code-block:: ini

  [networking_baremetal]
  enabled_devices = arista.example.net

  [arista.example.net]
  driver = netconf-openconfig
  device_params = name:default
  switch_info = arista
  switch_id = 00:53:00:0b:0b:0b
  host = arista.example.net
  username = user
  key_filename = /etc/neutron/ssh_keys/arista_sshkey

Configuration options
^^^^^^^^^^^^^^^^^^^^^

.. show-options::
   :config-file: tools/config/networking-baremetal-netconf-openconfig-driver-opts.conf

Sample Configuration File
^^^^^^^^^^^^^^^^^^^^^^^^^

The following is a sample configuration section that would be added to
``/etc/neutron/plugins/ml2/ml2_conf.ini``.

The sample configuration can also be viewed in :download:`file form
</_static/netconf_openconfig_device_driver.conf.sample>`.

.. important::

   The sample configuration file is auto-generated from networking-baremetal
   when this documentation is built. You must ensure your version of
   networking-baremetal matches the version of this documentation.

.. literalinclude:: /_static/netconf_openconfig_device_driver.conf.sample