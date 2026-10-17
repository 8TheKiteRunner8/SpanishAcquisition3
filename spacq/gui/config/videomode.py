"""
This file configures the video mode GUI.
The components of the GUI are represented by the following classes
    - VideoModeFrame:   Represents the whole window
        - VideoModeDisplayPanel:    Area dedicated to showing the measurements
        - VideoMode
        - VideoModeMenu:
            - VideoModeConfig: 
"""

import wx
import ast
import csv
import json
import math
import os
import re
import time
import logging

import numpy as np
from threading import Lock, Thread
import threading
from spacq.interface.units import Quantity
import spacq.gui.objectlistview as ObjectListView
from spacq.gui.action.video_mode import VideoModeRunner
from dataclasses import dataclass, field

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as FigureCanvas

log = logging.getLogger(__name__)

@dataclass
class VariableRow:
    name: str
    var: object
    value: float
    step_size: float
    initial_value: float = 0.0
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: object = None

    @property
    def resource_name(self):
        return getattr(self.var, "resource_name", "")

class VideoVariablePanel(wx.Panel):
    """
    Extra parameters held at a value by their SpacQ resource (e.g. the spirac DC sources)
    while video mode runs. Values are only ever changed in steps of the row's step size,
    and are returned to where they started when the panel is restored.
    """
    # Seconds between steps, so the source and any filter on the line can settle.
    STEP_DELAY_S = 0.05

    def __init__(self, parent, global_store, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.global_store = global_store
        self.rows = []

        self.olv = ObjectListView.ObjectListView(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self.olv.SetColumns([
            ObjectListView.ColumnDefn("Name", "left", 140, "name", isEditable=False),
            ObjectListView.ColumnDefn("Value", "right", 100, "value"),
            ObjectListView.ColumnDefn("Step size", "right", 90, "step_size"),
            ObjectListView.ColumnDefn("Resource", "left", 120, "resource_name", isEditable=False),
        ])
        self.olv.SetMinSize((320, 200))
        self.olv.cellEditMode = self.olv.CELLEDIT_DOUBLECLICK
        self.olv.Bind(ObjectListView.EVT_CELL_EDIT_FINISHING, self.OnCellEditFinishing)

        self.add_button = wx.Button(self, label="Add parameter")
        self.add_button.Bind(wx.EVT_BUTTON, self.OnAddParameter)
        self.remove_button = wx.Button(self, label="Remove parameter")
        self.remove_button.Bind(wx.EVT_BUTTON, self.OnRemoveParameter)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(self.add_button, 1, wx.EXPAND | wx.ALL, 4)
        button_sizer.Add(self.remove_button, 1, wx.EXPAND | wx.ALL, 4)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.olv, 1, wx.EXPAND)
        sizer.Add(button_sizer, 0, wx.EXPAND)
        self.SetSizer(sizer)

        self.refresh_rows()

    def refresh_rows(self):
        self.olv.SetObjects(self.rows)

    def _resource(self, row):
        resource = self.global_store.resources.get(row.resource_name)
        if resource is None or not resource.writable:
            raise ValueError(
                f"{row.name!r} has no writable resource ({row.resource_name!r})."
            )
        return resource

    @staticmethod
    def _number_from_reading(var, reading):
        # Mirrors how a variable's constant is expressed: in the variable's own units.
        if isinstance(reading, Quantity):
            if getattr(var, "type", None) == "quantity" and getattr(var, "units", None):
                return float(reading.value / Quantity(1, var.units).value)
            return float(reading.original_value)
        return float(reading)

    def _read_value(self, var):
        try:
            resource = self.global_store.resources[var.resource_name]
            if resource.readable:
                return self._number_from_reading(var, resource.value)
        except Exception:
            log.exception("Could not read %r; using its constant instead.", var.resource_name)
        return float(getattr(var, "const", 0.0))

    def add_variable_row(self, name, var):
        if any(row.name == name for row in self.rows):
            return

        value = self._read_value(var)
        self.rows.append(
            VariableRow(
                name=name,
                var=var,
                value=value,
                step_size=0.001,
                initial_value=value,
            )
        )
        self.refresh_rows()

    def OnAddParameter(self, evt):
        existing = {r.name for r in self.rows}
        available = [
            (name, var)
            for name, var in self.global_store.variables.items()
            if name not in existing and getattr(var, "resource_name", "")
        ]

        if not available:
            wx.MessageBox(
                "No additional variables with a resource are available.",
                "Add parameter",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        names = [name for name, var in available]

        dlg = wx.SingleChoiceDialog(
            self,
            "Select a variable to add",
            "Add parameter",
            names,
        )

        try:
            if dlg.ShowModal() == wx.ID_OK:
                selected_name = dlg.GetStringSelection()

                for name, var in available:
                    if name == selected_name:
                        self.add_variable_row(name, var)
                        break
        finally:
            dlg.Destroy()

    def OnRemoveParameter(self, evt):
        selected = self.olv.GetSelectedObjects()
        if not selected:
            wx.MessageBox(
                "Select a parameter to remove.",
                "Remove parameter",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        for row in selected:
            # Removing a parameter returns its resource to the value it had when added.
            self._start_ramp(row, row.initial_value)
            self.rows.remove(row)
        self.refresh_rows()

    def OnCellEditFinishing(self, evt):
        col = evt.objectListView.columns[evt.subItemIndex]
        row = evt.rowModel

        try:
            new_value = float(evt.editor.Value)
        except (TypeError, ValueError):
            evt.Veto()
            return

        if col.valueGetter == "value":
            # The displayed value follows the ramp, so the edit itself is not applied.
            evt.Veto()
            try:
                self._start_ramp(row, new_value)
            except Exception as error:
                wx.MessageBox(str(error), "Set parameter", wx.OK | wx.ICON_ERROR)
        elif col.valueGetter == "step_size":
            if new_value <= 0:
                evt.Veto()
                wx.MessageBox("Step size must be positive.", "Set parameter", wx.OK | wx.ICON_ERROR)

    def _start_ramp(self, row, target):
        """Walk the row's resource to target in steps no larger than the step size."""
        resource = self._resource(row)

        # A new target replaces any ramp already running for this row.
        row.cancel.set()
        if row.thread is not None and row.thread.is_alive():
            row.thread.join(timeout=5)
        row.cancel = threading.Event()

        row.thread = Thread(
            target=self._ramp, args=(row, resource, target, row.cancel), daemon=True
        )
        row.thread.start()

    def _ramp(self, row, resource, target, cancel):
        try:
            start = row.value
            step = max(abs(row.step_size), 1e-12)
            num_steps = max(1, math.ceil(abs(target - start) / step - 1e-9))
            for value in np.linspace(start, target, num_steps + 1)[1:]:
                if cancel.is_set():
                    return
                resource.value = row.var.with_type(float(value))
                row.value = float(value)
                wx.CallAfter(self._refresh_row, row)
                time.sleep(self.STEP_DELAY_S)
        except Exception as error:
            log.exception("Could not step %r.", row.name)
            wx.CallAfter(wx.MessageBox, str(error), "Set parameter", wx.OK | wx.ICON_ERROR)

    def _refresh_row(self, row):
        if self and row in self.rows:
            self.olv.RefreshObject(row)

    def restore_all(self):
        """Step every parameter back to the value it had when it was added; blocks until done."""
        for row in list(self.rows):
            try:
                self._start_ramp(row, row.initial_value)
            except Exception:
                log.exception("Could not restore %r.", row.name)
        for row in list(self.rows):
            if row.thread is not None:
                row.thread.join(timeout=60)

class VideoDisplayPanel(wx.Panel):
    def __init__(self, parent, global_store, *args, **kwargs):
        wx.Panel.__init__(self, parent, *args, **kwargs)

        self.global_store = global_store
        self._Acquistition_resource_name = None

        # Defaults
        # self.plot_settings = PlotSettings()
        self.unit_conversion = 0

        self.enabled = False
        self.capturing_data = False
        self.restart_live_view = False
        self.resource_backup = None
        self.latest_frame = None
        self.image = None
        self.colorbar = None
        self.title = ""
        self.x_label = "X"
        self.y_label = "Y"

        self.running_lock = Lock()

        self.figure, self.axis = plt.subplots()
        self.canvas = FigureCanvas(self, -1, self.figure)

        display_box = wx.BoxSizer(wx.VERTICAL)
        display_box.Add(self.canvas, 1, wx.EXPAND)
        self.SetSizer(display_box)

    def set_labels(self, title, x_label, y_label):
        """Set the plot title (measurement name) and the plunger gate names for the axes."""
        self.title = title
        self.x_label = x_label
        self.y_label = y_label

    def reset(self):
        """Forget the previous run so a new configuration gets fresh axes and colorbar."""
        self.latest_frame = None
        self.image = None
        if self.colorbar is not None:
            self.colorbar.remove()
            self.colorbar = None
        self.axis.clear()
        self.canvas.draw_idle()

    def set_frame(self, frame):
        # Called via wx.CallAfter from the streaming worker thread.
        self.latest_frame = frame
        self.update_plot()

    def update_plot(self):
        """
        Redraw the plot.
        """
        if self.latest_frame is None:
            return

        values = self.latest_frame["values"]
        x_mV = self.latest_frame["x_mV"]
        y_mV = self.latest_frame["y_mV"]
        extent = (x_mV[0], x_mV[-1], y_mV[0], y_mV[-1])

        if self.image is None:
            self.image = self.axis.imshow(values.T, origin="lower", aspect="auto", extent=extent)
            self.colorbar = self.figure.colorbar(self.image, ax=self.axis)
        else:
            self.image.set_data(values.T)
            self.image.set_extent(extent)
            self.image.autoscale()

        self.axis.set_title(self.title)
        self.axis.set_xlabel(f"{self.x_label} (mV)")
        self.axis.set_ylabel(f"{self.y_label} (mV)")
        self.canvas.draw_idle()

    def capture(self, directory):
        """
        Save the latest frame as CSV and PNG in the directory.
        Both share the name measurement_xaxis_yaxis_date_time.
        Returns the (csv_path, png_path) written.
        """
        if self.latest_frame is None:
            raise RuntimeError("No frame has been acquired yet.")

        frame = self.latest_frame
        stem = "_".join(
            _safe_filename_part(part)
            for part in (self.title, self.x_label, self.y_label, time.strftime("%Y%m%d_%H%M%S"))
        )
        csv_path = os.path.join(directory, stem + ".csv")
        png_path = os.path.join(directory, stem + ".png")

        # Rows follow the acquisition bin order: X is the slow axis, Y the fast axis.
        x_grid, y_grid = np.meshgrid(frame["x_mV"], frame["y_mV"], indexing="ij")
        with open(csv_path, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow([f"{self.x_label}_mV", f"{self.y_label}_mV", frame.get("channel", "value")])
            writer.writerows(zip(x_grid.ravel(), y_grid.ravel(), frame["values"].ravel()))

        # A standalone figure keeps the saved image independent of the live view.
        figure = Figure(figsize=(7, 5.5))
        axis = figure.subplots()
        x_mV, y_mV = frame["x_mV"], frame["y_mV"]
        image = axis.imshow(
            frame["values"].T, origin="lower", aspect="auto",
            extent=(x_mV[0], x_mV[-1], y_mV[0], y_mV[-1]),
        )
        figure.colorbar(image, ax=axis)
        axis.set_title(self.title)
        axis.set_xlabel(f"{self.x_label} (mV)")
        axis.set_ylabel(f"{self.y_label} (mV)")
        figure.tight_layout()
        figure.savefig(png_path, dpi=150)

        return csv_path, png_path

def _safe_filename_part(text):
    cleaned = re.sub(r"[^\w.\-]+", "-", str(text)).strip("-")
    return cleaned or "unnamed"

# The following classes are used for the video mode configuration
class ConfigFileMixin:
    def create_file_buttons(self, parent):
        self.save_button = wx.Button(parent, label="Save")
        self.load_button = wx.Button(parent, label="Load")

        self.save_button.Bind(wx.EVT_BUTTON, self.on_save)
        self.load_button.Bind(wx.EVT_BUTTON, self.on_load)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.save_button, 0, wx.RIGHT, 5)
        buttons.Add(self.load_button, 0)

        return buttons

    def on_save(self, evt):
        dialog = wx.FileDialog(
            self,
            "Save configuration",
            wildcard="JSON files (*.json)|*.json",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )

        try:
            if dialog.ShowModal() != wx.ID_OK:
                return

            configuration = self.get_configuration()

            with open(dialog.GetPath(), "w", encoding="utf-8") as file:
                json.dump(configuration, file, indent=4)

        except (OSError, ValueError, TypeError) as error:
            wx.MessageBox(
                str(error),
                "Could not save configuration",
                wx.OK | wx.ICON_ERROR,
                parent=self,
            )
        finally:
            dialog.Destroy()

    def on_load(self, evt):
        dialog = wx.FileDialog(
            self,
            "Load configuration",
            wildcard="JSON files (*.json)|*.json",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )

        try:
            if dialog.ShowModal() != wx.ID_OK:
                return

            with open(dialog.GetPath(), "r", encoding="utf-8") as file:
                configuration = json.load(file)

            self.set_configuration(configuration)

        except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
            wx.MessageBox(
                str(error),
                "Could not load configuration",
                wx.OK | wx.ICON_ERROR,
                parent=self,
            )
        finally:
            dialog.Destroy()

class InputPanel(ConfigFileMixin, wx.Panel):
    """
    This class represents the input panel for a waveform.
    The waveform 
    """
    def __init__(self, parent, axis, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)

        self.axis = axis

        static_box = wx.StaticBox(self, label=f"{axis} input")
        self.gate_name = wx.TextCtrl(static_box)
        self.resource_label = wx.TextCtrl(static_box)

        identity_form = wx.FlexGridSizer(2, 2, 5, 5)
        identity_form.AddGrowableCol(1, 1)
        for label, control in (
            ("Gate name:", self.gate_name),
            ("Resource Label:", self.resource_label),
        ):
            identity_form.Add(wx.StaticText(static_box, label=label))
            identity_form.Add(control, 1, wx.EXPAND)

        # Only shown in "waveform" mode; hidden when using sequencer options.
        self.waveform_panel = wx.Panel(static_box)
        self.waveform = wx.TextCtrl(self.waveform_panel, value="[0, 0, 0]")
        self.maximum_voltage = wx.TextCtrl(self.waveform_panel, value="0")
        self.time_step = wx.TextCtrl(self.waveform_panel, value="1e-9")

        waveform_form = wx.FlexGridSizer(3, 2, 5, 5)
        waveform_form.AddGrowableCol(1, 1)
        for label, control in (
            ("Waveform:", self.waveform),
            ("Maximum voltage:", self.maximum_voltage),
            ("Time step:", self.time_step),
        ):
            waveform_form.Add(wx.StaticText(self.waveform_panel, label=label))
            waveform_form.Add(control, 1, wx.EXPAND)
        self.waveform_panel.SetSizer(waveform_form)

        box = wx.StaticBoxSizer(static_box, wx.VERTICAL)
        box.Add(identity_form, 0, wx.EXPAND | wx.ALL, 8)
        box.Add(self.waveform_panel, 0, wx.EXPAND | wx.ALL, 8)

        self.SetSizer(box)

    def set_mode(self, mode):
        self.waveform_panel.Show(mode == "waveform")
        self.Layout()

    def _read_waveform(self):
        try:
            waveform = ast.literal_eval(self.waveform.GetValue())
        except (SyntaxError, ValueError) as error:
            raise ValueError(
                f"{self.axis} waveform must be a list."
            ) from error

        if not isinstance(waveform, list) or not waveform:
            raise ValueError(
                f"{self.axis} waveform must be a non-empty list."
            )

        if not all(
            isinstance(value, (int, float))
            and math.isfinite(float(value))
            for value in waveform
        ):
            raise ValueError(
                f"{self.axis} waveform must contain finite numbers."
            )

        if any(abs(float(value)) > 1.0 for value in waveform):
            raise ValueError(
                f"{self.axis} waveform samples must be between -1 and 1."
            )

        return waveform

    def get_configuration(self):
        configuration = {
            "axis": self.axis,
            "gate_name": self.gate_name.GetValue().strip(),
            "resource_label": self.resource_label.GetValue().strip(),
        }

        if not self.waveform_panel.IsShown():
            return configuration

        waveform = self._read_waveform()

        try:
            maximum_voltage = float(self.maximum_voltage.GetValue())
            time_step = float(self.time_step.GetValue())
        except ValueError as error:
            raise ValueError(
                f"{self.axis} voltage and time step must be numbers."
            ) from error

        if not math.isfinite(maximum_voltage) or maximum_voltage <= 0:
            raise ValueError(
                f"{self.axis} maximum voltage must be positive."
            )

        if not math.isfinite(time_step) or time_step <= 0:
            raise ValueError(
                f"{self.axis} time step must be positive."
            )

        configuration.update({
            "waveform": waveform,
            "maximum_voltage": maximum_voltage,
            "time_step": time_step,
        })
        return configuration

    def set_configuration(self, configuration):
        if not isinstance(configuration, dict):
            raise ValueError("Output configuration must be an object.")

        self.gate_name.SetValue(
            str(configuration.get("gate_name", ""))
        )
        self.resource_label.SetValue(
            str(configuration.get("resource_label", ""))
        )
        self.waveform.SetValue(
            repr(configuration.get("waveform", []))
        )
        self.maximum_voltage.SetValue(
            str(configuration.get("maximum_voltage", 1.0))
        )
        self.time_step.SetValue(
            str(configuration.get("time_step", 1e-9))
        )

class AcquistitionPanel(ConfigFileMixin, wx.Panel):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)

        static_box = wx.StaticBox(self, label="Acquistition")
        self.measurement_name = wx.TextCtrl(static_box)
        self.resource_label = wx.TextCtrl(static_box)
        self.set_readout = wx.CheckBox(static_box, label="Enable probe tone (set readout)")
        self.set_readout.Bind(wx.EVT_CHECKBOX, self.OnReadoutModeChanged)

        identity_form = wx.FlexGridSizer(3, 2, 5, 5)
        identity_form.AddGrowableCol(1, 1)
        for label, control in (
            ("Name:", self.measurement_name),
            ("Resource label:", self.resource_label),
        ):
            identity_form.Add(wx.StaticText(static_box, label=label))
            identity_form.Add(control, 1, wx.EXPAND)
        identity_form.Add((0, 0))
        identity_form.Add(self.set_readout, 0, wx.EXPAND)

        # Only shown in "waveform" mode; hidden when using sequencer options.
        self.waveform_panel = wx.Panel(static_box)
        self.samples = wx.TextCtrl(self.waveform_panel, value="[0]")
        self.time_step = wx.TextCtrl(self.waveform_panel, value="1e-9")

        waveform_form = wx.FlexGridSizer(2, 2, 5, 5)
        waveform_form.AddGrowableCol(1, 1)
        for label, control in (
            ("Samples:", self.samples),
            ("Time step:", self.time_step),
        ):
            waveform_form.Add(wx.StaticText(self.waveform_panel, label=label))
            waveform_form.Add(control, 1, wx.EXPAND)
        self.waveform_panel.SetSizer(waveform_form)

        self.readout_panel = wx.Panel(static_box)
        self.readout_resource_label = wx.TextCtrl(self.readout_panel)
        self.readout_frequency_Hz = wx.TextCtrl(self.readout_panel, value="100000000")
        self.readout_amplitude_mV = wx.TextCtrl(self.readout_panel, value="100")

        readout_form = wx.FlexGridSizer(3, 2, 5, 5)
        readout_form.AddGrowableCol(1, 1)
        for label, control in (
            ("Readout output resource:", self.readout_resource_label),
            ("Probe frequency (Hz):", self.readout_frequency_Hz),
            ("Probe amplitude (mV peak):", self.readout_amplitude_mV),
        ):
            readout_form.Add(wx.StaticText(self.readout_panel, label=label))
            readout_form.Add(control, 1, wx.EXPAND)
        self.readout_panel.SetSizer(readout_form)

        box = wx.StaticBoxSizer(static_box, wx.VERTICAL)
        box.Add(identity_form, 0, wx.EXPAND | wx.ALL, 8)
        box.Add(self.waveform_panel, 0, wx.EXPAND | wx.ALL, 8)
        box.Add(self.readout_panel, 0, wx.EXPAND | wx.ALL, 8)
        self._update_readout_visibility()

        self.SetSizer(box)

    def OnReadoutModeChanged(self, evt):
        self._update_readout_visibility()
        self.Layout()
        parent = self.GetParent()
        parent.Layout()
        parent.Fit()

    def _update_readout_visibility(self):
        self.readout_panel.Show(self.set_readout.GetValue())

    def set_mode(self, mode):
        self.waveform_panel.Show(mode == "waveform")
        self.Layout()

    def get_configuration(self):
        configuration = {
            "measurement_name": self.measurement_name.GetValue().strip(),
            "resource_label": self.resource_label.GetValue().strip(),
        }

        if self.set_readout.GetValue():
            readout_resource_label = self.readout_resource_label.GetValue().strip()
            if not readout_resource_label:
                raise ValueError(
                    "A readout output resource is required when set readout is enabled."
                )
            try:
                frequency_Hz = float(self.readout_frequency_Hz.GetValue())
                amplitude_mV = float(self.readout_amplitude_mV.GetValue())
            except ValueError as error:
                raise ValueError(
                    "Readout frequency and amplitude must be numbers."
                ) from error
            if not math.isfinite(frequency_Hz):
                raise ValueError("Readout frequency must be finite.")
            if not math.isfinite(amplitude_mV) or amplitude_mV <= 0:
                raise ValueError("Readout amplitude must be a positive finite value.")

            configuration.update({
                "mode": "set_readout",
                "readout_resource_label": readout_resource_label,
                "readout_options": {
                    "frequency_Hz": frequency_Hz,
                    "amplitude_mV": amplitude_mV,
                },
            })
        else:
            configuration["mode"] = "direct"

        if not self.waveform_panel.IsShown():
            return configuration

        try:
            samples = ast.literal_eval(self.samples.GetValue())
        except (SyntaxError, ValueError) as error:
            raise ValueError(
                "Acquistition samples must be a valid list."
            ) from error

        if not isinstance(samples, list):
            raise ValueError("Acquistition samples must be a list.")

        configuration["samples"] = samples
        return configuration

    def set_configuration(self, configuration):
        if not isinstance(configuration, dict):
            raise ValueError("Acquistition configuration must be an object.")

        samples = configuration.get("samples", [])

        if not isinstance(samples, list):
            raise ValueError("Acquistition samples must be a list.")

        self.measurement_name.SetValue(
            str(configuration.get("measurement_name", ""))
        )
        self.resource_label.SetValue(
            str(configuration.get("resource_label", ""))
        )
        self.samples.SetValue(repr(samples))
        readout_mode = configuration.get("mode", "direct") == "set_readout"
        self.set_readout.SetValue(readout_mode)
        self._update_readout_visibility()
        self.readout_resource_label.SetValue(
            str(configuration.get("readout_resource_label", ""))
        )
        readout_options = configuration.get("readout_options", {})
        if not isinstance(readout_options, dict):
            raise ValueError("Readout options configuration must be an object.")
        self.readout_frequency_Hz.SetValue(
            str(readout_options.get("frequency_Hz", 100000000))
        )
        self.readout_amplitude_mV.SetValue(
            str(readout_options.get("amplitude_mV", 100))
        )

class SequencerOptionsPanel(ConfigFileMixin, wx.Panel):
    """Shared 2D-sweep parameters used to build the nested-loop Q1ASM program."""
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)

        static_box = wx.StaticBox(self, label="Sequencer options")
        self.x_start_mV = wx.TextCtrl(static_box, value="-50")
        self.x_end_mV = wx.TextCtrl(static_box, value="50")
        self.y_start_mV = wx.TextCtrl(static_box, value="-50")
        self.y_end_mV = wx.TextCtrl(static_box, value="50")
        self.x_num_steps = wx.TextCtrl(static_box, value="8")
        self.y_num_steps = wx.TextCtrl(static_box, value="8")
        self.integration_time_ns = wx.TextCtrl(static_box, value="1000")
        self.settle_time_ns = wx.TextCtrl(static_box, value="1000")

        form = wx.FlexGridSizer(8, 2, 5, 5)
        form.AddGrowableCol(1, 1)

        fields = [
            ("X start (mV):", self.x_start_mV),
            ("X end (mV):", self.x_end_mV),
            ("Y start (mV):", self.y_start_mV),
            ("Y end (mV):", self.y_end_mV),
            ("X steps:", self.x_num_steps),
            ("Y steps:", self.y_num_steps),
            ("Integration time (ns):", self.integration_time_ns),
            ("Settle time (ns):", self.settle_time_ns),
        ]

        for label, control in fields:
            form.Add(wx.StaticText(static_box, label=label))
            form.Add(control, 1, wx.EXPAND)

        box = wx.StaticBoxSizer(static_box, wx.VERTICAL)
        box.Add(form, 1, wx.EXPAND | wx.ALL, 8)

        self.SetSizer(box)

    def get_configuration(self):
        try:
            x_window_mV = (float(self.x_start_mV.GetValue()), float(self.x_end_mV.GetValue()))
            y_window_mV = (float(self.y_start_mV.GetValue()), float(self.y_end_mV.GetValue()))
        except ValueError as error:
            raise ValueError("Sweep windows must be numbers.") from error

        try:
            x_num_steps = int(self.x_num_steps.GetValue())
            y_num_steps = int(self.y_num_steps.GetValue())
            integration_time_ns = int(self.integration_time_ns.GetValue())
            settle_time_ns = int(self.settle_time_ns.GetValue())
        except ValueError as error:
            raise ValueError("Step counts and timings must be integers.") from error

        return {
            "x_window_mV": x_window_mV,
            "y_window_mV": y_window_mV,
            "x_num_steps": x_num_steps,
            "y_num_steps": y_num_steps,
            "integration_time_ns": integration_time_ns,
            "settle_time_ns": settle_time_ns,
        }

    def set_configuration(self, configuration):
        if not isinstance(configuration, dict):
            raise ValueError("Sequencer options configuration must be an object.")

        x_window_mV = configuration.get("x_window_mV", (-50.0, 50.0))
        y_window_mV = configuration.get("y_window_mV", (-50.0, 50.0))

        self.x_start_mV.SetValue(str(x_window_mV[0]))
        self.x_end_mV.SetValue(str(x_window_mV[1]))
        self.y_start_mV.SetValue(str(y_window_mV[0]))
        self.y_end_mV.SetValue(str(y_window_mV[1]))
        self.x_num_steps.SetValue(str(configuration.get("x_num_steps", 8)))
        self.y_num_steps.SetValue(str(configuration.get("y_num_steps", 8)))
        self.integration_time_ns.SetValue(str(configuration.get("integration_time_ns", 1000)))
        self.settle_time_ns.SetValue(str(configuration.get("settle_time_ns", 1000)))

class VideoModeConfigDialog(ConfigFileMixin, wx.Dialog):
    def __init__(self, parent, global_store, configuration = None, *args, **kwargs):
        super().__init__(
            parent,
            title="Video Mode Configuration",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            *args,
            **kwargs,
        )

        self.global_store = global_store
        self.config = None

        # Configure x, y - input and acuisition panels
        self.x_panel = InputPanel(self, "X")
        self.y_panel = InputPanel(self, "Y")
        self.acquisition_panel = AcquistitionPanel(self)
        self.sequencer_options_panel = SequencerOptionsPanel(self)

        # "Waveform" and "Sequencer options" are mutually exclusive input modes.
        self.waveform_radio = wx.RadioButton(self, label="Waveform", style=wx.RB_GROUP)
        self.sequencer_options_radio = wx.RadioButton(self, label="Sequencer options")
        self.waveform_radio.Bind(wx.EVT_RADIOBUTTON, self.OnModeChanged)
        self.sequencer_options_radio.Bind(wx.EVT_RADIOBUTTON, self.OnModeChanged)

        mode_bar = wx.BoxSizer(wx.HORIZONTAL)
        mode_bar.Add(self.waveform_radio, 0, wx.RIGHT, 10)
        mode_bar.Add(self.sequencer_options_radio, 0)

        self.preview_button = wx.Button(self,label="View waveforms")
        self.preview_button.Bind(wx.EVT_BUTTON, self.on_view_waveforms)

        if configuration is not None:
            self.set_configuration(configuration)
        else:
            self._apply_mode()

        # add panels to dialog; proportion 0 keeps each panel at its natural
        # height so a hidden panel doesn't leave the others stretched and empty.
        panels = wx.BoxSizer(wx.VERTICAL)
        panels.Add(self.x_panel, 0, wx.EXPAND | wx.ALL, 5)
        panels.Add(self.y_panel, 0, wx.EXPAND | wx.ALL, 5)
        panels.Add(self.acquisition_panel, 0, wx.EXPAND | wx.ALL, 5)
        panels.Add(self.sequencer_options_panel, 0, wx.EXPAND | wx.ALL, 5)

        file_buttons = self.create_file_buttons(self)
        dialog_buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.preview_button, 0, wx.RIGHT, 10)
        buttons.Add(file_buttons,0, wx.RIGHT,10)
        buttons.Add(dialog_buttons,1,wx.EXPAND)

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(mode_bar, 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(panels, 1, wx.EXPAND)
        main_sizer.Add(buttons,0,wx.EXPAND | wx.ALL, 5)

        self.SetSizerAndFit(main_sizer)
        self.CentreOnParent()

        self.Bind(wx.EVT_BUTTON, self.OnOk, id=wx.ID_OK)

    def OnModeChanged(self, evt):
        self._apply_mode()

    def _get_mode(self):
        return "sequencer_options" if self.sequencer_options_radio.GetValue() else "waveform"

    def _apply_mode(self):
        mode = self._get_mode()
        self.x_panel.set_mode(mode)
        self.y_panel.set_mode(mode)
        self.acquisition_panel.set_mode(mode)
        self.sequencer_options_panel.Show(mode == "sequencer_options")
        self.preview_button.Enable(mode == "waveform")
        self.Layout()

    def on_view_waveforms(self, evt):
        try:
            configuration = self.get_configuration()
        except ValueError as error:
            wx.MessageBox(
                str(error),
                "Invalid waveform configuration",
                wx.OK | wx.ICON_ERROR,
                parent=self,
            )
            return

        dialog = WaveformPreviewDialog(
            self,
            configuration,
        )

        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()
    
    def get_configuration(self):
        sweep_mode = self._get_mode()
        acquisition = self.acquisition_panel.get_configuration()
        configuration = {
            "mode": acquisition["mode"],
            "sweep_mode": sweep_mode,
            "x": self.x_panel.get_configuration(),
            'y': self.y_panel.get_configuration(),
            'acquisition': acquisition,
        }

        if sweep_mode == "sequencer_options":
            configuration["sequence_options"] = self.sequencer_options_panel.get_configuration()

        return configuration

    def set_configuration(self, configuration):
        if not isinstance(configuration, dict):
            raise ValueError("Video-mode configuration must be an object")

        mode = configuration.get("sweep_mode")
        if mode is None:
            legacy_mode = configuration.get("mode", "waveform")
            mode = legacy_mode if legacy_mode in ("waveform", "sequencer_options") else "waveform"
        self.sequencer_options_radio.SetValue(mode == "sequencer_options")
        self.waveform_radio.SetValue(mode != "sequencer_options")

        self.x_panel.set_configuration(configuration.get("x", {}))
        self.y_panel.set_configuration(configuration.get("y", {}))
        self.acquisition_panel.set_configuration(configuration.get("acquisition", {}))
        self.sequencer_options_panel.set_configuration(configuration.get("sequence_options", {}))

        self._apply_mode()

    def OnOk(self, evt):
        try:
            self.config = self.get_configuration()
        except ValueError as error:
            wx.MessageBox(
                str(error),
                "Invalid configuration",
                wx.OK | wx.ICON_ERROR,
                parent=self,
            )
            return

        self.EndModal(wx.ID_OK)

# The following class is used to preview the loaded waveforms
class WaveformPreviewDialog(wx.Dialog):
    def __init__(self, parent, configuration):
        super().__init__(
            parent,
            title="Waveform preview",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )

        self.figure, axes = plt.subplots(3, 1, sharex=False, figsize=(7, 6))

        self.canvas = FigureCanvas(self, -1, self.figure)

        self._plot_output(axes[0],configuration["x"],"X output")
        self._plot_output(axes[1],configuration["y"],"Y output")
        self._plot_measurement(axes[2],configuration["acquisition"])

        self.figure.tight_layout()

        close_button = wx.Button(self, wx.ID_CLOSE, "Close")
        close_button.Bind(wx.EVT_BUTTON,lambda event: self.EndModal(wx.ID_CLOSE))

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.canvas, 1, wx.EXPAND | wx.ALL, 5)
        sizer.Add(close_button, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        self.SetSizerAndFit(sizer)
        self.SetMinSize((600, 500))
        self.CentreOnParent()

    @staticmethod
    def _plot_output(axis, configuration, title):
        waveform = configuration["waveform"]
        maximum_voltage = configuration["maximum_voltage"]
        time_step = configuration["time_step"]

        times = [
            index * time_step
            for index in range(len(waveform))
        ]
        voltages = [
            sample * maximum_voltage
            for sample in waveform
        ]

        axis.step(times, voltages, where = 'post', marker="o")
        axis.set_title(title)
        axis.set_xlabel('Time (s)')
        axis.set_ylabel("Voltage (V)")
        axis.grid(True)

    @staticmethod
    def _plot_measurement(axis, configuration):
        samples = configuration["samples"]
        times = list(range(len(samples)))

        axis.plot(times, samples, marker="o")
        axis.set_title("Acquisition")
        axis.set_xlabel("Sample")
        axis.set_ylabel("Value")
        axis.grid(True)

# The following class is used as the main frame for the videomode program
class VideoModeFrame(wx.Frame):
    def __init__(self, parent, global_store, close_callback, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)

        self.global_store = global_store
        self.close_callback = close_callback
        self.video_mode_config = None
        self.runner = None
        self.stream_thread = None
        self.main = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.splitter = wx.SplitterWindow(self.main)
        # Menu
        menuBar = wx.MenuBar()
        menu = wx.Menu()
        menuBar.Append(menu, "&Video Mode")
        
        # VideoMode Configuration
        item = menu.Append(wx.ID_ANY, "Configure")
        self.Bind(wx.EVT_MENU, self.OnConfigureVideoMode, item)

        # Variable Control
        self.video_variable_panel = VideoVariablePanel(self.splitter, 
                                                       global_store)

        # Video Panel
        self.video_display_panel = VideoDisplayPanel(self.splitter,
                                                     global_store)

        self.splitter.SplitVertically(self.video_variable_panel, self.video_display_panel, sashPosition=420)
        main_sizer.Add(self.splitter, 1, wx.EXPAND | wx.ALL, 4)

        # Acquistition Controls
        button_bar = wx.BoxSizer(wx.HORIZONTAL)
        self.start_button = wx.Button(self.main, label="Start")
        self.start_button.Bind(wx.EVT_BUTTON, self.OnRun)

        self.capture_button = wx.Button(self.main, label="Capture frame")
        self.capture_button.Bind(wx.EVT_BUTTON, self.OnCapture)

        self.stop_button = wx.Button(self.main, label="Stop")
        self.stop_button.Bind(wx.EVT_BUTTON, self.OnStop)
        
        button_bar.Add(self.start_button, 0, wx.ALL, 4)
        button_bar.Add(self.capture_button, 0, wx.ALL, 4)
        button_bar.Add(self.stop_button, 0, wx.ALL, 4)

        main_sizer.Add(button_bar, 0, wx.EXPAND | wx.ALL, 4)

        self.SetMenuBar(menuBar)
        self.main.SetSizer(main_sizer)
        self.SetClientSize((900, 500))
        self.Bind(wx.EVT_CLOSE, self.OnClose)

    def OnRun(self, evt):
        if self.video_mode_config is None:
            wx.MessageBox(
                "Configure video mode before starting.",
                "Video Mode",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        self._stop_runner()

        try:
            self.runner = VideoModeRunner(self.global_store, self.video_mode_config)
            self.runner.run()
        except Exception as error:
            wx.MessageBox(str(error), "Video Mode", wx.OK | wx.ICON_ERROR)
            self.runner = None
            return

        config = self.video_mode_config
        self.video_display_panel.reset()
        self.video_display_panel.set_labels(
            config.get("acquisition", {}).get("measurement_name") or "Measurement",
            config["x"].get("gate_name") or "X",
            config["y"].get("gate_name") or "Y",
        )

        self.stream_thread = Thread(target=self._stream_frames, daemon=True)
        self.stream_thread.start()

    def _stream_frames(self):
        # Runs on a worker thread; marshals each frame to the GUI thread.
        try:
            for frame in self.runner.stream():
                wx.CallAfter(self.video_display_panel.set_frame, frame)
        except Exception:
            log.exception("Video mode streaming stopped unexpectedly.")

    def OnConfigureVideoMode(self, evt):
        dialog = VideoModeConfigDialog(
            self,
            self.global_store,
            configuration=self.video_mode_config,
        )

        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.video_mode_config = dialog.config
        finally:
            dialog.Destroy()
        
    def OnCapture(self, evt):
        data_capture_panel = getattr(self.GetParent(), "data_capture_panel", None)
        directory = ""
        if data_capture_panel is not None:
            directory = data_capture_panel.directory_browse_button.GetValue()

        if not directory or not os.path.isdir(directory):
            wx.MessageBox(
                "Select a valid export directory in the sweep parameters window first.",
                "Capture frame",
                wx.OK | wx.ICON_ERROR,
            )
            return

        try:
            csv_path, png_path = self.video_display_panel.capture(directory)
        except Exception as error:
            wx.MessageBox(str(error), "Capture frame", wx.OK | wx.ICON_ERROR)
            return

        wx.MessageBox(
            f"Saved:\n{csv_path}\n{png_path}",
            "Capture frame",
            wx.OK | wx.ICON_INFORMATION,
        )

    def _stop_runner(self):
        # The stream thread must be gone before anything else talks to the cluster.
        if self.runner is not None:
            self.runner.stop()
        if self.stream_thread is not None and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=5)

    def OnStop(self,evt):
        self._stop_runner()

    def OnClose(self, evt):
        self._stop_runner()
        if self.runner is not None:
            try:
                self.runner.reset_cluster()
            except Exception:
                log.exception("Could not reset the Qblox cluster on close.")
        # The Qblox no longer adds anything, so the spirac values can now be walked back.
        self.video_variable_panel.restore_all()
        self.close_callback()
        evt.Skip()
        self.Destroy()