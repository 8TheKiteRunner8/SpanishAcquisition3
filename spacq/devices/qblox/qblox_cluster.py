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

# Entering this IP in the device config connects to a simulated cluster instead of hardware.
DUMMY_IP_ADDRESS = "0.0.0.0"
DUMMY_CLUSTER_CONFIG = {
    2: ClusterType.CLUSTER_QCM,
    4: ClusterType.CLUSTER_QRM,
}

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
            int(channel_name[1:])-1 if channel_name.startswith("O") else None
        )
        self.input_index = (
            int(channel_name[1:])-1 if channel_name.startswith("I") else None
        )

    def __getattr__(self, name):
        module = self.__dict__.get("module")
        if module is None:
            raise AttributeError(name)
        return getattr(module, name)    

    def __repr__(self):
        return f"<QbloxChannelResource {self.channel_name} of {self.module.name}>"

    def connect_output(self, sequencer, path: str = "I"):
        """
        Connect this channel as an output to the specified path on the sequencer.

        Parameters:
        ----------
        sequencer : object
            The sequencer object to which the output should be connected.
        path : str, optional
            The input path on the sequencer to connect to (default is "I").
        """
        getattr(sequencer, f"connect_out{self.output_index}")(path)

    def connect_input(self, sequencer, path: str = "I"):
        """
        Connect this channel as an input to the specified path on the sequencer.

        Parameters:
        ----------
        sequencer : object
            The sequencer object to which the input should be connected.
        path : str, optional
            The output path on the sequencer to connect to (default is "I").
        """
        getattr(sequencer, f"connect_acq_{path}")(f"in{self.input_index}")


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
        self.module = qblox_module
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
        """
        Create channels, based on MODULE_CHANNELS for the module type. 
        This is not automatically configured.
        """
        channel_info = MODULE_CHANNELS.get(self.module_type)

        if channel_info is None:
            raise ValueError(
                f"Unsupported Qblox module type: {self.module_type}"
            )

        for channel_name in (*channel_info["outputs"], *channel_info["inputs"]):
            self.channels[channel_name] = QbloxChannelResource(self, channel_name)

    def __getattr__(self, name):
        return getattr(self.module, name)


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
        if self.ip_address == DUMMY_IP_ADDRESS:
            log.warning("Using a dummy Qblox cluster; no hardware is connected.")
            connection = {"dummy_cfg": DUMMY_CLUSTER_CONFIG}
        else:
            connection = {"identifier": self.ip_address}

        self.cluster = find_or_create_instrument(
            Cluster,
            name=f"Qblox_Cluster_{self.ip_address.replace('.', '_')}",
            recreate=True,
            **connection,
        )

        # get connected modules
        self._get_connected_modules()
        log.info("Detected Qblox modules: %s", sorted(self.modules.keys()))

        # create module subdevices
        for slot, module in self.modules.items():
            self.subdevices[slot] = module

        self._connected()

    def _get_connected_modules(self):
        for slot, qblox_module in self.cluster.get_connected_modules().items():
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