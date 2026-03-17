# Copyright (c) 2026 Red Hat, Inc.
# All Rights Reserved
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Test package initialization.

This module is imported before any test modules, allowing us to set up
test-wide configuration and monkey-patches.
"""

from oslo_config import cfg

# Store original register_opts methods
_original_register_opts = cfg.ConfigOpts.register_opts
_original_register_opt = cfg.ConfigOpts.register_opt

# Options that are legitimately shared between agent and ML2 plugin
# In production they run in separate processes, but in tests they share
# cfg.CONF
SHARED_OPTIONS = {
    ('l2vni', 'l2vni_subport_anchor_network'),
}


def _register_opts_ignore_known_duplicates(self, opts, group=None):
    """Wrapper that registers options individually, ignoring known duplicates.

    In production, agent and ML2 run in separate processes with separate
    config registries. In tests, they share cfg.CONF, causing DuplicateOptError
    for legitimately shared options.

    This wrapper only ignores duplicates for options in SHARED_OPTIONS.
    Any other DuplicateOptError will be raised to catch test bugs.
    """
    for opt in opts:
        try:
            _original_register_opt(self, opt, group=group)
        except cfg.DuplicateOptError as e:
            # Only ignore if this is a known shared option
            if (group, opt.name) not in SHARED_OPTIONS:
                # This is an unexpected duplicate - raise it to catch bugs
                raise e
            # Known shared option, skip it


def _register_opt_ignore_known_duplicates(self, opt, group=None, cli=False,
                                          **kwargs):
    """Wrapper that ignores duplicate registration for known shared options.

    In production, agent and ML2 run in separate processes with separate
    config registries. In tests, they share cfg.CONF, causing DuplicateOptError
    for legitimately shared options.

    This wrapper only ignores duplicates for options in SHARED_OPTIONS.
    Any other DuplicateOptError will be raised to catch test bugs.
    """
    try:
        return _original_register_opt(self, opt, group=group, cli=cli,
                                      **kwargs)
    except cfg.DuplicateOptError as e:
        # Only ignore if this is a known shared option
        if (group, opt.name) not in SHARED_OPTIONS:
            # This is an unexpected duplicate - raise it to catch bugs
            raise e
        # Known shared option, that's fine
        return False


# Monkey-patch the registration methods to handle known shared options in tests
cfg.ConfigOpts.register_opts = _register_opts_ignore_known_duplicates
cfg.ConfigOpts.register_opt = _register_opt_ignore_known_duplicates
