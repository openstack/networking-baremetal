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

from oslo_config import cfg
from oslo_log import log as logging

from networking_baremetal import exceptions


CONF = cfg.CONF
LOG = logging.getLogger(__name__)

_opts = [
    cfg.ListOpt('enabled_devices',
                default=[],
                sample_default=['common-example',
                                'netconf-openconfig-example'],
                help=('Enabled devices for which the plugin should manage'
                      'configuration. Driver specific configuration for each '
                      'device must be added in separate sections.')),
]

_device_opts = [
    cfg.StrOpt('driver',
               help='The driver to use when configuring the device'),
    cfg.StrOpt('switch_id',
               help='The switch ID, MAC address of the device.'),
    cfg.StrOpt('switch_info',
               help=('Optional string field to be used to store any '
                     'vendor-specific information.')),
    cfg.ListOpt('physical_networks',
                default=[],
                help='A list of physical networks mapped to this device.'),
    cfg.BoolOpt('manage_vlans',
                default=True,
                help=('Set this to False for the device if VLANs should not '
                      'be create and deleted on the device.')),
    ]

_conductor_groups_opts = [
    cfg.ListOpt('conductor_groups',
                default=[],
                help=('List of conductor groups this networking-baremetal '
                      'instance should manage. If empty, all ports will be '
                      'queried.')),
]

networking_baremetal_group = cfg.OptGroup(
    name='networking_baremetal',
    title='ML2 networking-baremetal options')
CONF.register_group(networking_baremetal_group)
CONF.register_opts(_opts, group=networking_baremetal_group)

conductor_groups_opt_group = cfg.OptGroup(
    name='conductor_groups',
    title='ML2 networking-baremetal conductor groups filtering options')
CONF.register_group(conductor_groups_opt_group)
CONF.register_opts(_conductor_groups_opts, group=conductor_groups_opt_group)

for device in CONF.networking_baremetal.enabled_devices:
    group = cfg.OptGroup(
        name=device,
        title=f'{device} Device options')
    CONF.register_group(group)
    CONF.register_opts(_device_opts, group=group)


def list_opts():
    return [('networking_baremetal', _opts),
            ('conductor_groups', _conductor_groups_opts)]


def list_common_device_driver_opts():
    return [('networking_baremetal', _opts),
            ('common-example', _device_opts),
            ('conductor_groups', _conductor_groups_opts)]


def get_devices():
    """Get enabled network devices from configuration

    This is called during driver initialization, during initialization
    additional driver specific configuration is loaded and the drivers
    validation method is called.
    """
    devices = dict()
    for dev in CONF.networking_baremetal.enabled_devices:
        if not CONF[dev].driver:
            LOG.error('IGNORING invalid device %s, driver not specified.', dev)
            continue
        if not CONF[dev].switch_id and not CONF[dev].switch_info:
            LOG.error('IGNORING invalid device %s, switch_id and/or '
                      'switch_info is required', dev)
            continue

        if CONF[dev].switch_id:
            devices[CONF[dev].switch_id] = dev
        if CONF[dev].switch_info:
            devices[CONF[dev].switch_info] = dev

    return devices


def validate_device_options(device):
    """Check every configured option for a device.

    oslo.config converts and validates an option value when it is first
    read, not when the configuration is parsed. Without this, a malformed
    value goes unnoticed until the driver happens to use that option, which
    for most device options is while binding a port -- long after the
    service started. Reading every registered option here moves that to
    startup, and reports all of a device's problems at once instead of
    stopping at the first.

    Must be called after the driver's load_config(), so that driver
    specific options are registered.

    :param device: name of the device configuration group.
    :raises DriverValidationError: if any option has an invalid value.
    """
    errors = []
    for name in sorted(CONF[device]):
        try:
            CONF[device][name]
        except cfg.Error as e:
            errors.append(str(e))

    if errors:
        raise exceptions.DriverValidationError(device=device,
                                               err='; '.join(errors))
