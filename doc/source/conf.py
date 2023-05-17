# -*- coding: utf-8 -*-
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os
import sys


# NOTE(amotoki): In case of oslo_config.sphinxext is enabled,
# when resolving automodule neutron.tests.functional.db.test_migrations,
# sphinx accesses tests/functional/__init__.py is processed,
# eventlet.monkey_patch() is called and monkey_patch() tries to access
# pyroute2.common.__class__ attribute. It raises pyroute2 warning and
# it causes sphinx build failure due to warning-is-error = 1.
# To pass sphinx build, ignore pyroute2 warning explicitly.
logging.getLogger('pyroute2').setLevel(logging.ERROR)

sys.path.insert(0, os.path.abspath('../..'))
# -- General configuration ----------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom ones.
extensions = [
    'sphinxcontrib.apidoc',
    'oslo_config.sphinxext',
    'oslo_config.sphinxconfiggen',
    'openstackdocstheme',
]

# autodoc generation is a bit aggressive and a nuisance when doing heavy
# text edit cycles.
# execute "export SPHINX_DEBUG=1" in your terminal to disable

# The suffix of source filenames.
source_suffix = '.rst'

# The master toctree document.
master_doc = 'index'

# General information about the project.
copyright = 'OpenStack Foundation'

config_generator_config_file = [
    ('../../tools/config/networking-baremetal-ironic-neutron-agent.conf',
     '_static/ironic_neutron_agent.ini'),
    ('../../tools/config/networking-baremetal-common-device-driver-opts.conf',
     '_static/common_device_driver_opts'),
    ('../../tools/config/networking-baremetal-netconf-openconfig-driver-opts.conf',
     '_static/netconf_openconfig_device_driver')
]
# sample_config_basename = '_static/ironic_neutron_agent.ini'

# A list of ignored prefixes for module index sorting.
modindex_common_prefix = ['networking_baremetal.']

# If true, '()' will be appended to :func: etc. cross-reference text.
add_function_parentheses = True

# If true, the current module name will be prepended to all description
# unit titles (such as .. function::).
add_module_names = True

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = 'native'

# If true, '()' will be appended to :func: etc. cross-reference text.
add_function_parentheses = True

# -- Options for HTML output --------------------------------------------------

# The theme to use for HTML and HTML Help pages.  Major themes that come with
# Sphinx are currently 'default' and 'sphinxdoc'.
html_theme = 'openstackdocs'

# openstackdocstheme options
openstackdocs_repo_name = 'openstack/networking-baremetal'
openstackdocs_pdf_link = True
openstackdocs_use_storyboard = False

# Output file base name for HTML help builder.
htmlhelp_basename = 'networking-baremetaldoc'

latex_use_xindy = False

# Grouping the document tree into LaTeX files. List of tuples
# (source start file, target name, title, author, documentclass
# [howto/manual]).
latex_documents = [
    ('index',
     'doc-networking-baremetal.tex',
     'Networking Baremetal Documentation',
     'OpenStack Foundation', 'manual'),
]

# Example configuration for intersphinx: refer to the Python standard library.
#intersphinx_mapping = {'http://docs.python.org/': None}

# -- sphinxcontrib.apidoc configuration --------------------------------------

apidoc_module_dir = '../../networking_baremetal'
apidoc_output_dir = 'contributor/api'
apidoc_excluded_paths = [
    'tests',
]
