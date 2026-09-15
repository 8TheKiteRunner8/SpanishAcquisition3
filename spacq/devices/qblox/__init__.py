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


name = 'Qblox'

from . import spi_rack
from . import qblox_cluster

models = [spi_rack, qblox_cluster]
log.debug('Found models for "{0}": {1}'.format(name, ''.join(str(x) for x in models)))

from .mock import mock_spi_rack
from .mock import mock_qblox_cluster
mock_models = [mock_spi_rack, mock_qblox_cluster]
log.debug('Found mock models for "{0}": {1}'.format(name, ''.join(str(x) for x in mock_models)))
