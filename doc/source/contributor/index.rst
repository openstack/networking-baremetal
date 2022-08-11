============
Contributing
============

This document provides some necessary points for developers to consider when
writing and reviewing networking-baremetal code.

Getting Started
===============

If you're completely new to OpenStack and want to contribute to the
networking-baremetal project, please start by familiarizing yourself with the
`Infra Team's Developer Guide
<https://docs.openstack.org/infra/manual/developers.html>`_. This will
help you get your accounts set up in Launchpad and Gerrit, familiarize you with
the workflow for the OpenStack continuous integration and testing systems, and
help you with your first commit.

LaunchPad Project
-----------------

Most of the tools used for OpenStack require a launchpad.net ID for
authentication.

.. seealso::

   * https://launchpad.net
   * https://launchpad.net/ironic

Related Projects
----------------

Networking Baremetal is tightly integrated with the ironic and neutron
projects. Ironic and its related projects are developed by the same community.

.. seealso::

   * https://launchpad.net/ironic
   * https://launchpad.net/neutron

Project Hosting Details
-----------------------

Bug tracker
    https://bugs.launchpad.net/networking-baremetal

Mailing list (prefix Subject line with ``[ironic][networking-baremetal]``)
    http://lists.openstack.org/cgi-bin/mailman/listinfo/openstack-discuss

Code Hosting
    https://opendev.org/openstack/networking-baremetal

Code Review
    https://review.opendev.org/#/q/status:open+project:openstack/networking-baremetal,n,z

Developer quick-starts
======================

These are quick walk throughs to get you started developing code for
networking-baremetal. These assume you are already familiar with submitting
code reviews to an OpenStack project.

.. toctree::
   :maxdepth: 2

   Deploying networking-baremetal with DevStack <quickstart>
   Deploying networking-baremetal and multi-tenant networking with DevStack <quickstart-multitenant>
   Virtual lab with virtual switch and netconf-openconfig Device Driver <quickstart-netconf-openconfig>

Full networking-baremetal python API reference
==============================================

* :ref:`modindex`

.. # api/modules is hidden since it's in the modindex link above.
.. toctree::
   :hidden:

   api/modules

