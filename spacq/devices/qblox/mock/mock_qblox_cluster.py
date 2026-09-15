"""
	Device driver for the Qblox SPI Rack with 2 D5a modules installed.

	Note:
		This driver likely contains some redundant code that is a carry over
		from other working Spanish Acquisition device drivers. Since the implementation interface
		is not clear, this extra code has been left in to ensure the device still works properly.

		The mock device and tests for this device are untested.

	Authors: Luke Dyer, Noah Stieler
"""
import logging
log = logging.getLogger(__name__)

from qblox_instruments import Cluster
from qcodes.instrument import find_or_create_instrument

from spacq.interface.resources import Resource
from spacq.tool.box import Synchronized

from ...mock.mock_abstract_device import MockAbstractDevice
from ..qblox_cluster import QbloxCluster

class MockQbloxCluster(MockAbstractDevice, QbloxCluster):
	"""
	Mock interface for the Sample ABC1234.
	"""

	def __init__(self, *args, **kwargs):
		self.mocking = QbloxCluster

		MockAbstractDevice.__init__(self, *args, **kwargs)

	def _reset(self):
		self.mock_state['setting'] = 'default value'

name = 'Qblox Cluster'
implementation = MockQbloxCluster