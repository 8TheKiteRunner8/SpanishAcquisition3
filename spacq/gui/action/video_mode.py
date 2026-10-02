import threading
import logging
import numpy as np

from dataclasses import dataclass, field

from spacq.devices.qblox.qblox_cluster import QbloxCluster
from spacq.gui.action.qblox_video_backend import QbloxVideoBackend

log = logging.getLogger(__name__)

class VideoModeRunner:
    """
    The VideoModeRunner 

    Supported AWG and acquisition devices:
    - qblox cluster
    """
    def __init__(self, global_store, video_mode_config, *args, **kwargs):
        self.global_store = global_store
        self.config = video_mode_config
        self.buffer = []
        self.run_result = None
        self._stop_event = threading.Event()

        self.x_resource = self._resolve_resource(self.config["x"]["resource_label"])
        self.y_resource = self._resolve_resource(self.config["y"]["resource_label"])
        self.acquisition_resource = self._resolve_resource(self.config["acquisition"]["resource_label"])

        self.pulse_device = self._get_device(self.x_resource)
        self.acquisition_device = self._get_device(self.acquisition_resource)

        self.execution_mode = self._select_execution_mode()
        self.backend = self._build_backend()

    def _resolve_resource(self, label):
        resource = self.global_store.resources.get(label)

        if resource is None:
            raise KeyError(
                f"Resource {label!r} aws not found."
            )
        
        return resource

    @staticmethod
    def _get_device(resource):
        # A Qblox channel resource's device is its owning module's cluster;
        # other resource types may expose the device directly.
        module = getattr(resource, "module", None)
        device = getattr(module, "device", None) if module is not None else getattr(resource, "device", None)

        if device is None:
            raise ValueError(
                f"Resource {resource!r} has no associated device."
            )
        
        return device

    @staticmethod
    def _same_device_id(a, b):
        if a is b:
            return True

        a_id = getattr(a, "device_id", None)
        b_id = getattr(b, "device_id", None)

        if a_id is not None and b_id is not None:
            return a_id == b_id

        a_addr = getattr(a, "connection_resource", None)
        b_addr = getattr(b, "connection_resource", None)

        if a_addr is not None and b_addr is not None:
            return a_addr == b_addr

        return False
    
    def run(self):
        # Arms/starts the hardware once; does not loop. Call stream() to read frames.
        config = dict(self.config)
        config["x_channel"] = self.x_resource
        config["y_channel"] = self.y_resource
        config["acq_channel"] = self.acquisition_resource

        self.run_result = self.backend.prepare_and_run(config)
        return self.run_result

    def stream(self):
        # Yields frames from the already-armed hardware until stop() is called.
        if self.run_result is None:
            raise RuntimeError("Call run() before stream().")

        voltage_window = self.config.get("sequence_options")
        if voltage_window is None:
            raise ValueError(
                "Video-mode streaming requires the 'sequencer options' configuration mode."
            )

        for frame in self.backend.stream_frames(self.run_result, voltage_window):
            if self._stop_event.is_set():
                break
            yield frame

    def stop(self):
        self._stop_event.set()

    def _select_execution_mode(self):
        """
        This method provides the logic to select the appropriate execution mode based on the devices involved.
        """
        # Same device: simplest and probably the only supported case for now
        if self.pulse_device is self.acquisition_device and self._same_device_id(self.pulse_device, self.acquisition_device):
            return "single_device"

        # Different devices: require explicit sync capability
        if (getattr(self.pulse_device, "supports_external_sync", False)
            and getattr(self.acquisition_device, "supports_external_sync", False)
        ):
            return "multi_device"

        raise ValueError(
            "Pulse and acquisition devices are different and do not "
            "support a common synchronization mode."
        )

    def _build_backend(self):
        """
        Builds and returns the appropriate backend instance based on the execution mode.
        """
        if self.execution_mode == "single_device":
            if isinstance(self.pulse_device, QbloxCluster):
                from .qblox_video_backend import QbloxVideoBackend
                return QbloxVideoBackend(self.pulse_device)

            raise TypeError(
                f"Unsupported single-device backend: "
                f"{type(self.pulse_device).__name__}"
            )

        if self.execution_mode == "multi_device":
            # This is only a skeleton.
            # It would eventually use a trigger/clock-sharing backend.
            # return MultiDeviceVideoBackend(
            #     pulse_device=self.pulse_device,
            #     acquisition_device=self.acquisition_device,
            # )
            return None

        raise RuntimeError(
            f"Unknown execution mode: {self.execution_mode}"
        )