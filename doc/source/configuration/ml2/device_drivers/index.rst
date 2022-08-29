==============
Device drivers
==============

The baremetal mechanism ML2 plug-in provides a device driver plug-in interface,
this interface can be used to add device (switch) configuration capabilities.
The interface uses `stevedore <https://opendev.org/openstack/stevedore/>`__ for
dynamic loading.

Individual drivers may have independent configuration requirements depending on
the implementation. :ref:`Driver specific options <device_drivers>` are
documented separately.

.. toctree::
   :maxdepth: 2

   Common configuration options <common_config>


.. _device_drivers:

Available device drivers
~~~~~~~~~~~~~~~~~~~~~~~~

.. toctree::
   :maxdepth: 3

   netconf-openconfig <netconf-openconfig>
