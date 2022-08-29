=======================
Configuration Reference
=======================

The following pages describe configuration options that can be used to adjust
the neutron ML2 configuration and the baremetal ML2 plug-in and device drivers
to your particular situation.

To enable mechanism drivers in the ML2 plug-in, edit the
``/etc/neutron/plugins/ml2/ml2_conf.ini`` configuration file. For example, this
enables the ``openvswitch`` and ``baremetal`` mechanism drivers:

.. code-block:: ini

  [ml2]
  mechanism_drivers = openvswitch,baremetal

To add a device to manage, edit the ``/etc/neutron/plugins/ml2/ml2_conf.ini``
configuration file. The example below enables devices: ``device_a.example.net``
and ``device_b.example.net``. For each device a separate section in the same
configuration file defines the device and driver specific configuration. Please
refer to :doc:`device_drivers/index` for details.

.. code-block:: ini

  [networking_baremetal]
  enabled_device = device_a.example.net,device_b.example.net

.. toctree::
   :maxdepth: 4

   Device Drivers <device_drivers/index>