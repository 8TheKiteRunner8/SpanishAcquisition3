import logging

from ...mock.mock_abstract_device import MockAbstractDevice
from spacq.interface.resources import Resource
from ..qblox_cluster import QbloxCluster

log = logging.getLogger(__name__)

class MockSequencer:
    def __init__(self, module, index):
        self.module = module
        self.index = index
        self.sequence_data = None
        self.connected_outputs = []
        self.running = False

    def sequence(self, sequence_data):
        self.sequence_data = sequence_data

    def connect_out0(self, signal):
        self.connected_outputs.append((0, signal))

    def connect_out1(self, signal):
        self.connected_outputs.append((1, signal))

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        
class MockQCM:
    def __init__(self, slot):
        self.slot_idx = slot
        self.sequencers = [
            MockSequencer(self, index)
            for index in range(6)
        ]
        self.outputs = {
            "O1": None,
            "O2": None,
            "O3": None,
            "O4": None,
        }

    def present(self):
        return True


class MockQRM(MockQCM):
    def __init__(self, slot):
        super().__init__(slot)

        self.inputs = {
            "I1": None,
            "I2": None,
        }

class MockChannel:
    def __init__(self, name, direction):
        self.name = name
        self.direction = direction
        self.value = 0.0

    def __str__(self):
        return str(self.value)

    def write(self, value):
        self.value = value

    def read(self):
        return self.value


class MockCluster:
    def __init__(self):
        self.modules = {
            1: MockQCM(slot=1),
            2: MockQRM(slot=2),
        }
        self.started = False

    def start_sequencer(self):
        self.started = True

    def stop_sequencer(self):
        self.started = False


class MockQbloxCluster(MockAbstractDevice, QbloxCluster):
    """
    Mock Qblox cluster exposing one QCM and one QRM.
    """

    def __init__(self, *args, **kwargs):
        self.mocking = QbloxCluster
        self.cluster = MockCluster()

        MockAbstractDevice.__init__(self, *args, **kwargs)

    def _reset(self):
        self.mock_state["cluster"] = self.cluster
        self.mock_state["modules"] = self.cluster.modules
        self.mock_state["running"] = False

    def connect(self):
        self.cluster = self.mock_state["cluster"]
        self._create_resources()
        self.connected = True

    def _create_resources(self):
        if not hasattr(self, "resources"):
            self.resources = {}

        for slot, module in self.cluster.modules.items():
            module_type = (
                "qcm"
                if isinstance(module, MockQCM)
                and not isinstance(module, MockQRM)
                else "qrm"
            )

            channel_names = list(module.outputs)

            if isinstance(module, MockQRM):
                channel_names += list(module.inputs)

            for channel_name in channel_names:
                resource_name = (
                    f"{module_type}_slot{slot}_{channel_name}"
                )

                channel = MockChannel(
                    name=resource_name,
                    direction=(
                        "input"
                        if channel_name.startswith("I")
                        else "output"
                    ),
                )

                setattr(self, resource_name, channel)

                resource = Resource(
                    self,
                    resource_name,
                    resource_name,
                )

                resource.module = module
                resource.module_type = module_type.upper()
                resource.slot = slot
                resource.channel_name = channel_name
                resource.direction = channel.direction
                resource.channel = channel

                self.resources[resource_name] = resource
                
name = "Qblox Cluster"
implementation = MockQbloxCluster
