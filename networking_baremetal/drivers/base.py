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
import abc


class BaseDeviceClient(object, metaclass=abc.ABCMeta):

    def __init__(self, device):
        self.device = device

    def get_client_args(self):
        """Get client connection arguments from configuration"""

    def get(self, **kwargs):
        """Get current configuration/state from device"""

    def edit_config(self, config):
        """Edit configuration on the device

        :param config: The configuration to apply to the device
        """


class BaseDeviceDriver(object, metaclass=abc.ABCMeta):

    SUPPORTED_BOND_MODES = set()

    def __init__(self, device):
        self.client = BaseDeviceClient(device)
        self.device = device

    def load_config(self):
        """Register driver specific configuration

        All drivers should register driver specific options in the
        device specific config group. This method will be called
        during mechanism driver initialization.
        """

    def validate(self):
        """Driver validation

        This method will be called during mechanism driver
        initialization. Raising any exception other than
        DriverValidationError will cause service initialization
        failure.

        :raises DriverValidationError: On validation failure.
        """

    def create_network(self, context):
        """Create network on device

        :param context: NetworkContext instance describing the new
            network.
        """

    def update_network(self, context):
        """Update network on device

        :param context: NetworkContext instance describing the new
            network.
        """

    def delete_network(self, context):
        """Delete network on device

        :param context: NetworkContext instance describing the new
            network.
        """

    def create_port(self, context, segment, links):
        """Create/Configure port on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param segment: segment dictionary describing segment to bind
        :param links: Local link information filtered for the device.
        """

    def update_port(self, context, links):
        """Update port on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param links: Local link information filtered for the device.
        """

    def delete_port(self, context, links, current=True):
        """Delete/Un-configure port on device

        :param context: PortContext instance describing the new
            state of the port, as well as the original state prior
            to the update_port call.
        :param links: Local link information filtered for the device.
        :param current: Boolean, when true use context.current, when
            false use context.original
        """
