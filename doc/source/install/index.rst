============
Installation
============

This section describes how to install and configure the
``networking-baremetal`` plugin.

Install the networking-baremetal plugin
---------------------------------------

At the command line:

.. code-block:: shell

    $ pip install networking-baremetal

Or, if you have neutron installed in a virtualenv,
install the ``networking-baremetal`` plugin to the same virtualenv:

.. code-block:: shell

    $ source <path-to-neutron-venv>/bin/activate
    $ pip install networking-baremetal

Enable baremetal mechanism driver in the Networking service
-----------------------------------------------------------

To enable mechanism drivers in the ML2 plug-in, edit the
``/etc/neutron/plugins/ml2/ml2_conf.ini`` file on the neutron server.
For example, this enables the ovs and baremetal mechanism drivers:

.. code-block:: ini

  [ml2]
  mechanism_drivers = ovs,baremetal
