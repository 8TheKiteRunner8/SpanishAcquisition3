"""
	Device driver for the Qblox Cluster.

	Author: Kite Sauër 
"""
import logging
log = logging.getLogger(__name__)

from qblox_instruments import Cluster, ClusterType
from qcodes.instrument import find_or_create_instrument

from spacq.interface.resources import Resource
from spacq.tool.box import Synchronized

from ..abstract_device import AbstractDevice, AbstractSubdevice
from ..tools import quantity_wrapped, quantity_unwrapped, str_to_bool

MODULE_CHANNELS = {
    "QCM": {
        "outputs": ("O1", "O2", "O3", "O4"),
        "inputs": (),
    },
    "QRM": {
        "outputs": ("O1", "O2"),
        "inputs": ("I1", "I2"),
    },
    "QCMRF": {
        "outputs": ("O1", "O2"),
        "inputs": (),
    },
    "QRMRF": {
        "outputs": ("O1", "O2"),
        "inputs": ("I1", "I2"),
    },
}


class QbloxChannelResource(Resource):
    """
    A single physical input/output channel (eg. "O1") belonging to a Qblox module.
    """
    def __init__(self, qblox_module_subdevice, channel_name):
        super().__init__(qblox_module_subdevice)

        self.module = qblox_module_subdevice
        self.channel_name = channel_name
        self.output_index = (
            int(channel_name[1:]) if channel_name.startswith("O") else None
        )
        self.input_index = (
            int(channel_name[1:]) if channel_name.startswith("I") else None
        )

    def __repr__(self):
        return f"<QbloxChannelResource {self.channel_name} of {self.module.name}>"


class QbloxModule(AbstractSubdevice):
    """
    A single module (QCM/QRM/QCM-RF/QRM-RF) plugged into a Qblox cluster.

    Exposes its channels (eg. "O1", "I2") as resources, and proxies hardware
    attributes (eg. sequencer0, is_qcm_type) to the underlying qblox_instruments module.
    """
    def __init__(self, device, qblox_module, module_type):
        """
        device: The QbloxCluster to which this module belongs.
        qblox_module: The underlying qblox_instruments module object.
        module_type: One of the keys in MODULE_CHANNELS (eg. "QCM", "QRM").
        """
        self.qblox_module = qblox_module
        self.module_type = module_type
        self.slot_idx = qblox_module.slot_idx
        self.channels = {}

        super().__init__(device)

        self.name = f"module_{self.slot_idx}_{module_type}"

    def _setup(self):
        super()._setup()

        self._create_channels()

        for channel_name, channel in self.channels.items():
            self.resources[channel_name] = channel

    def _create_channels(self):
        channel_info = MODULE_CHANNELS.get(self.module_type)

        if channel_info is None:
            raise ValueError(
                f"Unsupported Qblox module type: {self.module_type}"
            )

        for channel_name in (*channel_info["outputs"], *channel_info["inputs"]):
            self.channels[channel_name] = QbloxChannelResource(self, channel_name)

    def __getattr__(self, name):
        return getattr(self.qblox_module, name)


class QbloxCluster(AbstractDevice):
    """
    Represents a Qblox cluster device and its installed modules.
    """
    def __init__(self, *args, **kwargs):
        autoconnect = kwargs.get('autoconnect', False)
        self.ip_address = kwargs.get('ip_address', None)
        if self.ip_address is None:
            raise ValueError('QbloxCluster requires an IP address')
        self.modules = {}

        self._setup()
        if autoconnect:
            self.connect()

    def connect(self):
        """
        Connect to the Qblox cluster using the specified IP address.
        This will detect connected modules and create subdevices for each module.
        """
        self.cluster = find_or_create_instrument(
            Cluster,
            name=f"Qblox_Cluster_{self.ip_address.replace('.', '_')}",
            recreate=True,
            identifier=self.ip_address,
        )

        # get connected modules
        self._get_connected_modules()
        log.info("Detected Qblox modules: %s", sorted(self.modules.keys()))

        # create module subdevices
        for slot, module in self.modules.items():
            self.subdevices[slot] = module

        self._connected()

    def _get_connected_modules(self):
        for slot, qblox_module in self.cluster.modules.items():
            if not qblox_module.present():
                continue

            module_type = self._identify_module_type(qblox_module)
            log.info("Detected slot %s: %s", slot, module_type)

            self.modules[str(slot)] = QbloxModule(self, qblox_module, module_type)

    @staticmethod
    def _identify_module_type(qblox_module):
        is_rf = getattr(qblox_module, "is_rf_type", False)

        if qblox_module.is_qcm_type:
            return "QCMRF" if is_rf else "QCM"
        if qblox_module.is_qrm_type:
            return "QRMRF" if is_rf else "QRM"

        raise ValueError(
            f"Unsupported Qblox module type in slot {qblox_module.slot_idx}."
        )

    def reset(self):
        self.cluster.reset()

    def close(self):
        self.cluster.close()


name = 'Qblox Cluster'
implementation = QbloxCluster