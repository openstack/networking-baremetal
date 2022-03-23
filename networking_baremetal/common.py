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

from oslo_config import cfg
from oslo_log import log as logging
import stevedore

from networking_baremetal import constants
from networking_baremetal import exceptions

DRIVER_NAMESPACE = 'networking_baremetal.drivers'

CONF = cfg.CONF
LOG = logging.getLogger(__name__)


def txt_subelement(parent, tag, text, *args, **kwargs):
    element = ElementTree.SubElement(parent, tag, *args, **kwargs)
    element.text = text
    return element


def config_to_xml(config):
    element = ElementTree.Element(constants.CFG_ELEMENT)
    for conf in config:
        element.append(conf.to_xml_element())
    return ElementTree.tostring(element).decode("utf-8")


def driver_mgr(device_id):
    driver = CONF[device_id].driver
    try:
        mgr = stevedore.driver.DriverManager(
            namespace=DRIVER_NAMESPACE,
            name=driver,
            invoke_on_load=True,
            invoke_args=(device_id,),
            on_load_failure_callback=_load_failure_hook
        )
    except stevedore.exception.NoUniqueMatch as exc:
        raise exceptions.DriverEntrypointLoadError(
            entry_point=f'{DRIVER_NAMESPACE}.{driver}',
            err=exc)

    return mgr.driver


def _load_failure_hook(manager, entrypoint, exception):
    LOG.error("Driver manager %(manager)s failed to load device plugin "
              "%(entrypoint)s: %(exp)s",
              {'manager': manager, 'entrypoint': entrypoint, 'exp': exception})
    raise exceptions.DriverEntrypointLoadError(entry_point=entrypoint,
                                               err=exception)
