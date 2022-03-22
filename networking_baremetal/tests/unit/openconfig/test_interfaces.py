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
from unittest import mock
from xml.etree import ElementTree

from oslotest import base

from networking_baremetal import constants
from networking_baremetal.openconfig.interfaces import ethernet
from networking_baremetal.openconfig.interfaces import interfaces
from networking_baremetal.openconfig.vlan import vlan


class TestInterfaces(base.BaseTestCase):

    @mock.patch.object(vlan, 'VlanSwitchedVlan', autospec=True)
    def test_interfaces_ethernet(self, mock_sw_vlan):
        mock_sw_vlan.return_value.to_xml_element.return_value = (
            ElementTree.Element('fake-switched-vlan'))
        if_eth = ethernet.InterfacesEthernet()
        mock_sw_vlan.assert_called_with()
        element = if_eth.to_xml_element()
        xml_str = ElementTree.tostring(element).decode("utf-8")
        expected = (f'<ethernet xmlns="{if_eth.NAMESPACE}">'
                    '<fake-switched-vlan />'
                    '</ethernet>')
        self.assertEqual(expected, xml_str)

    @mock.patch.object(interfaces, 'InterfaceEthernet', autospec=True)
    def test_interfaces_interfaces(self, mock_iface_eth):
        mock_iface_eth.return_value.to_xml_element.return_value = (
            ElementTree.Element('fake-ethernet'))
        ifaces = interfaces.Interfaces()
        iface = ifaces.add('eth0/1')
        mock_iface_eth.assert_called_with('eth0/1')
        self.assertEqual([iface], ifaces.interfaces)
        element = ifaces.to_xml_element()
        xml_str = ElementTree.tostring(element).decode("utf-8")
        expected = (f'<interfaces xmlns="{ifaces.NAMESPACE}">'
                    '<fake-ethernet />'
                    '</interfaces>')
        self.assertEqual(expected, xml_str)

    @mock.patch.object(ethernet, 'InterfacesEthernet', autospec=True)
    @mock.patch.object(interfaces, 'InterfaceConfig', autospec=True)
    def test_interfaces_interface_ethernet(self, mock_if_conf, mock_if_eth):
        mock_if_conf.return_value.to_xml_element.return_value = (
            ElementTree.Element('fake_config'))
        mock_if_eth.return_value.to_xml_element.return_value = (
            ElementTree.Element('fake_ethernet'))
        interface = interfaces.InterfaceEthernet('eth0/1')
        mock_if_conf.assert_called_with()
        mock_if_eth.assert_called_with()
        self.assertEqual('eth0/1', interface.name)
        self.assertEqual(mock_if_conf(), interface.config)
        self.assertEqual(mock_if_eth(), interface.ethernet)
        element = interface.to_xml_element()
        xml_str = ElementTree.tostring(element).decode("utf-8")
        expected = ('<interface>'
                    '<name>eth0/1</name>'
                    '<fake_config />'
                    '<fake_ethernet />'
                    '</interface>')
        self.assertEqual(expected, xml_str)
        not_string = 10
        self.assertRaises(TypeError,
                          interfaces.InterfaceEthernet, not_string)

    def test_interfaces_interface_config(self):
        if_conf = interfaces.InterfaceConfig()
        self.assertEqual(constants.NetconfEditConfigOperation.MERGE.value,
                         if_conf.operation)
        self.assertRaises(ValueError, interfaces.InterfaceConfig,
                          **dict(operation='invalid'))
        self.assertRaises(TypeError, interfaces.InterfaceConfig,
                          **dict(enabled='not_bool'))
        self.assertRaises(TypeError, interfaces.InterfaceConfig,
                          **dict(description=10))  # Not string
        self.assertRaises(TypeError, interfaces.InterfaceConfig,
                          **dict(mtu='not_int'))
        if_conf.name = 'test1'
        if_conf.enabled = True
        if_conf.description = 'Description'
        if_conf.mtu = 9000
        element = if_conf.to_xml_element()
        xml_str = ElementTree.tostring(element).decode("utf-8")
        expected = ('<config>'
                    '<name operation="merge">test1</name>'
                    '<description operation="merge">Description</description>'
                    '<enabled operation="merge">true</enabled>'
                    '<mtu operation="merge">9000</mtu>'
                    '</config>')
        self.assertEqual(expected, xml_str)
        del if_conf.name
        if_conf.operation = 'remove'
        if_conf.description = ''
        if_conf.mtu = 0
        if_conf.enabled = False
        element = if_conf.to_xml_element()
        xml_str = ElementTree.tostring(element).decode("utf-8")
        expected = ('<config>'
                    '<description operation="remove" />'
                    '<enabled operation="remove">false</enabled>'
                    '<mtu operation="remove">0</mtu>'
                    '</config>')
        self.assertEqual(expected, xml_str)
