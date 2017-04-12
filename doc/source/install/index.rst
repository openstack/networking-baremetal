============
Installation
============

This section describes how to install and configure the
``networking-baremetal`` plugin and ``ironic-neutron-agent``.

The ``ironic-neutron-agent`` is a neutron agent that populates the host to
physical network mapping for baremetal nodes in neutron. Neutron uses this to
calculate the segment to host mapping information.

Install the networking-baremetal plugin and agent
-------------------------------------------------

At the command line:

.. code-block:: shell

    $ pip install networking-baremetal

Or, if you have neutron installed in a virtualenv,
install the ``networking-baremetal`` plugin to the same virtualenv:

.. code-block:: shell

    $ . <path-to-neutron-venv>/bin/activate
    $ pip install networking-baremetal

Enable baremetal mechanism driver in the Networking service
-----------------------------------------------------------

To enable mechanism drivers in the ML2 plug-in, edit the
``/etc/neutron/plugins/ml2/ml2_conf.ini`` file on the neutron server.
For example, this enables the ovs and baremetal mechanism drivers:

.. code-block:: ini

  [ml2]
  mechanism_drivers = ovs,baremetal

Add configuration for the ironic-neutron-agent and start it
-----------------------------------------------------------

To configure the baremetal neutron agent, edit the neutron configuration
``/etc/neutron/neutron.conf`` file. Add an ``[ironic]`` section. For example:

.. code-block:: ini

  [ironic]
  project_domain_name = Default
  project_name = service
  user_domain_name = Default
  password = password
  username = ironic
  auth_url = http://identity-server.example.com/identity
  auth_type = password
  region_name = RegionOne

Additionally the ``transport_url`` in the ``[DEFAULT]`` section should be
set. For example, this would set the rpc transport to use a rabbit service.

.. code-block:: ini

  [DEFAULT]
  transport_url = rabbit://username:password@<hostname>:5672/

To start the agent either run it from the command line like in the example
below or add it to the init system.

.. code-block:: shell

   $ ironic-neutron-agent

If you want to use a separate config file; instead of the default
``neutron.conf``; add the ``--config-file`` argument to the command line. For
example:

.. code-block:: shell

   $ ironic-neutron-agent --config-file /etc/ironic/ironic-neutron-agent.conf
