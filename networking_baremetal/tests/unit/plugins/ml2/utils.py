# Copyright (c) 2017 Mirantis, Inc.
# All Rights Reserved.
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


from oslo_utils import uuidutils


def get_test_network(**kw):
    """Return a network object with appropriate attributes."""

    result = {
        "provider:physical_network": kw.get("physical_network", "mynetwork"),
        "ipv6_address_scope": kw.get("ipv6_address_scope", None),
        "revision_number": kw.get("revision_number", 7),
        "port_security_enabled": kw.get("port_security_enabled", True),
        "mtu": kw.get("mtu", 1500),
        "id": kw.get("id", uuidutils.generate_uuid()),
        "router:external": kw.get("router:external", False),
        "availability_zone_hints": kw.get("availability_zone_hints", []),
        "availability_zones": kw.get("availability_zones", ["nova"]),
        "ipv4_address_scope": kw.get("ipv4_address_scope", None),
        "shared": kw.get("shared", False),
        "project_id": kw.get("project_id", uuidutils.generate_uuid()),
        "status": kw.get("status", "ACTIVE"),
        "subnets": kw.get("subnets", []),
        "description": kw.get("description", ""),
        "tags": kw.get("tags", []),
        "provider:segmentation_id": kw.get("segmentation_id", 113),
        "name": kw.get("name", "private"),
        "admin_state_up": kw.get("admin_state_up", True),
        "tenant_id": kw.get("tenant_id", uuidutils.generate_uuid()),
        "provider:network_type": kw.get("network_type", "flat")
    }
    return result


def get_test_port(network_id, **kw):
    """Return a port object with appropriate attributes."""

    result = {
        "status": kw.get("status", "DOWN"),
        "binding:host_id": kw.get("host_id", "aaa.host"),
        "description": kw.get("description", ""),
        "allowed_address_pairs": kw.get("allowed_address_pairs", []),
        "tags": kw.get("tags", []),
        "extra_dhcp_opts": kw.get("extra_dhcp_opts", []),
        "device_owner": kw.get("device_owner", "baremetal:host"),
        "revision_number": kw.get("revision_number", 7),
        "port_security_enabled": kw.get("port_security_enabled", False),
        "binding:profile": kw.get("binding_profile",
                                  {'local_link_information': [
                                      {'switch_info': 'foo',
                                       'port_id': 'Gig0/1',
                                       'switch_id': 'aa:bb:cc:dd:ee:ff'}]}),
        "fixed_ips": kw.get("fixed_ips", []),
        "id": kw.get("id", uuidutils.generate_uuid()),
        "security_groups": kw.get("security_groups", []),
        "device_id": kw.get("device_id", ""),
        "name": kw.get("name", "Port1"),
        "admin_state_up": kw.get("admin_state_up", True),
        "network_id": network_id,
        "tenant_id": kw.get("tenant_id", uuidutils.generate_uuid()),
        "binding:vif_details": kw.get("vif_details", {}),
        "binding:vnic_type": kw.get("vnic_type", "baremetal"),
        "binding:vif_type": kw.get("vif_type", "unbound"),
        "mac_address": kw.get("mac_address", "fa:16:3e:c2:2a:8f"),
        "project_id": kw.get("project_id", uuidutils.generate_uuid())
    }
    return result


def get_test_subnet(network_id, **kw):
    """Return a subnet object with appropriate attributes."""

    result = {
        "service_types": kw.get("service_types", []),
        "description": kw.get("description", ""),
        "enable_dhcp": kw.get("enable_dhcp", True),
        "tags": kw.get("tags", []),
        "network_id": network_id,
        "tenant_id": kw.get("tenant_id", uuidutils.generate_uuid()),
        "dns_nameservers": kw.get("dns_nameservers", []),
        "gateway_ip": kw.get("gateway_ip", "10.1.0.1"),
        "ipv6_ra_mode": kw.get("ipv6_ra_mode", None),
        "allocation_pools": kw.get("allocation_pools", [{"start": "10.1.0.2",
                                                         "end": "10.1.0.62"}]),
        "host_routes": kw.get("host_routes", []),
        "revision_number": kw.get("revision_number", 7),
        "ip_version": kw.get("ip_version", 4),
        "ipv6_address_mode": kw.get("ipv6_address_mode", None),
        "cidr": kw.get("cidr", "10.1.0.0/26"),
        "project_id": kw.get("project_id", uuidutils.generate_uuid()),
        "id": kw.get("id", uuidutils.generate_uuid()),
        "subnetpool_id": kw.get("subnetpool_id", uuidutils.generate_uuid()),
        "name": kw.get("name", "subnet0")
    }
    return result


def get_test_segment(**kw):
    result = {
        'segmentation_id': kw.get('segmentation_id', '123'),
        'network_type': kw.get('network_type', 'flat'),
        'id': uuidutils.generate_uuid()
    }
    return result
