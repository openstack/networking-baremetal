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
from xml.etree import ElementTree

from networking_baremetal.openconfig.vlan import vlan


class InterfacesEthernet:
    """Ethernet configuration and state"""
    NAMESPACE = 'http://openconfig.net/yang/interfaces/ethernet'
    PARENT = 'interface'
    TAG = 'ethernet'

    def __init__(self):
        self._switched_vlan = vlan.VlanSwitchedVlan()

    @property
    def switched_vlan(self):
        return self._switched_vlan

    @switched_vlan.setter
    def switched_vlan(self, value):
        if not isinstance(value, vlan.VlanSwitchedVlan):
            raise TypeError('switched_vlan must be VlanSwitchedVlan, got {}'
                            .format(type(value)))
        self._switched_vlan = value

    @switched_vlan.deleter
    def switched_vlan(self):
        self._switched_vlan = None

    def to_xml_element(self):
        """Create XML Element

        :return: ElementTree Element with SubElements
        """
        elem = ElementTree.Element(self.TAG)
        elem.set('xmlns', self.NAMESPACE)
        if self.switched_vlan:
            elem.append(self.switched_vlan.to_xml_element())
        return elem
