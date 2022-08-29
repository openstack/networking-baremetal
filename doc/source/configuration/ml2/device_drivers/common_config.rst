===================================================
Common configuration options for all device drivers
===================================================

This page describes configuration options that is common to all networking-
baremetal device drivers. Individual drivers may have independent configuration
requirements depending on the implementation, refer to the device driver
specific documentation.

Configuration options
^^^^^^^^^^^^^^^^^^^^^

.. show-options::
   :config-file: tools/config/networking-baremetal-common-device-driver-opts.conf

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

.. literalinclude:: /_static/common_device_driver_opts.conf.sample
