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

from ..abstract_device import AbstractDevice, AbstractSubdevice
from ..tools import quantity_wrapped, quantity_unwrapped, str_to_bool

class QbloxCluster(AbstractDevice):
	"""
	Represents a Qblox cluster device.
	"""

	def __init__(self, ip_adress: str):
		self.host = ip_adress
		self.modules = {}
		self._setup()

	def _setup(self):
		super()._setup()

		self.cluster = find_or_create_instrument(
			Cluster,
			recreate = True,
			name = f"{self.name}_cluster",
			identifier = self.host,
		)

		self.cluster.led_brightness('medium')
		self.cluster.reset()

		# dynamic slot detection:
		for mod in self.cluster.modules:
			if mod.present():
				self.modules[mod.slot_idx] = mod

		log.info(f"Detected Qblox modules: {sorted(self.modules.keys())}")


	def upload_waveform(self, waveform, module_slot, output_idx):
		pass

	def _reset():
		self.cluster.reset()


name = 'Qblox Cluster'
implementation = QbloxCluster