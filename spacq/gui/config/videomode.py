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
import json
import math
import logging

from threading import Lock, Thread
import spacq.gui.objectlistview as ObjectListView
from spacq.gui.action.video_mode import VideoModeRunner
from dataclasses import dataclass, field

import matplotlib.pyplot as plt
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as FigureCanvas

log = logging.getLogger(__name__)

@dataclass
class VariableRow:
    name: str
    var: object
    value: float
    step_size: float

class VideoVariablePanel(wx.Panel):
    def __init__(self, parent, global_store, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.global_store = global_store
        self.rows = []

        self.olv = ObjectListView.ObjectListView(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self.olv.SetColumns([
            ObjectListView.ColumnDefn("Name", "left", 140, "name"),
            ObjectListView.ColumnDefn("Value", "right", 100, "value"),
            ObjectListView.ColumnDefn("Step size", "right", 90, "step_size"),
            ObjectListView.ColumnDefn("Action", "left", 80, "action"),
        ])
        self.olv.SetMinSize((320, 200))

        self.add_button = wx.Button(self, label="Add parameter")
        self.add_button.Bind(wx.EVT_BUTTON, self.OnAddParameter)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.olv, 1, wx.EXPAND)
        sizer.Add(self.add_button, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(sizer)

        self.refresh_rows()

    def refresh_rows(self):
        self.olv.SetObjects(self.rows)

    def add_variable_row(self, name, var):
        if any(row.name == name for row in self.rows):
            return

        self.rows.append(
            VariableRow(
                name=name,
                var=var,
                value=float(getattr(var, "const", 0.0)),
                step_size=0.001,
            )
        )
        self.refresh_rows()

    def OnAddParameter(self, evt):
        existing = {r.name for r in self.rows}
        available = [
            (name, var)
            for name, var in self.global_store.variables.items() if name not in existing
        ]

        if not available:
            wx.MessageBox(
                "No additional variables are available.",
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
        existing = {r.name for r in self.rows}
        dlg = wx.SingleChoiceDialog(
            self,
            "Select a variable to add",
            "Add parameter",
            existing,
        )
        try:
            if dlg.ShowModal() == wx.ID_OK:
                selected_name = dlg.GetStringSelection()
                for var in existing:
                    if var.name == selected_name:
                        self.add_variable_row(var)
                        break
        finally:
            dlg.Destroy()
        # self.rows.pop(dlg)

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

        self.running_lock = Lock()

        self.figure, self.axis = plt.subplots()
        self.canvas = FigureCanvas(self, -1, self.figure)

        display_box = wx.BoxSizer(wx.VERTICAL)
        display_box.Add(self.canvas, 1, wx.EXPAND)
        self.SetSizer(display_box)

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
            self.axis.set_xlabel("X (mV)")
            self.axis.set_ylabel("Y (mV)")
            self.figure.colorbar(self.image, ax=self.axis)
        else:
            self.image.set_data(values.T)
            self.image.set_extent(extent)
            self.image.autoscale()

        self.canvas.draw_idle()

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

        self.gate_name = wx.TextCtrl(self)
        self.resource_label = wx.TextCtrl(self)

        identity_form = wx.FlexGridSizer(2, 2, 5, 5)
        identity_form.AddGrowableCol(1, 1)
        for label, control in (
            ("Gate name:", self.gate_name),
            ("Resource Label:", self.resource_label),
        ):
            identity_form.Add(wx.StaticText(self, label=label))
            identity_form.Add(control, 1, wx.EXPAND)

        # Only shown in "waveform" mode; hidden when using sequencer options.
        self.waveform_panel = wx.Panel(self)
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

        box = wx.StaticBoxSizer(
            wx.StaticBox(self, label=f"{axis} input"),
            wx.VERTICAL,
        )
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

        self.measurement_name = wx.TextCtrl(self)
        self.resource_label = wx.TextCtrl(self)

        identity_form = wx.FlexGridSizer(2, 2, 5, 5)
        identity_form.AddGrowableCol(1, 1)
        for label, control in (
            ("Name:", self.measurement_name),
            ("Resource label:", self.resource_label),
        ):
            identity_form.Add(wx.StaticText(self, label=label))
            identity_form.Add(control, 1, wx.EXPAND)

        # Only shown in "waveform" mode; hidden when using sequencer options.
        self.waveform_panel = wx.Panel(self)
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

        box = wx.StaticBoxSizer(
            wx.StaticBox(self, label="Acquistition"),
            wx.VERTICAL,
        )
        box.Add(identity_form, 0, wx.EXPAND | wx.ALL, 8)
        box.Add(self.waveform_panel, 0, wx.EXPAND | wx.ALL, 8)

        self.SetSizer(box)

    def set_mode(self, mode):
        self.waveform_panel.Show(mode == "waveform")
        self.Layout()

    def get_configuration(self):
        configuration = {
            "resource_label": self.resource_label.GetValue().strip(),
        }

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

        self.resource_label.SetValue(
            str(configuration.get("resource_label", ""))
        )
        self.samples.SetValue(repr(samples))

class SequencerOptionsPanel(ConfigFileMixin, wx.Panel):
    """Shared 2D-sweep parameters used to build the nested-loop Q1ASM program."""
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)

        self.x_start_mV = wx.TextCtrl(self, value="-50")
        self.x_end_mV = wx.TextCtrl(self, value="50")
        self.y_start_mV = wx.TextCtrl(self, value="-50")
        self.y_end_mV = wx.TextCtrl(self, value="50")
        self.x_num_steps = wx.TextCtrl(self, value="8")
        self.y_num_steps = wx.TextCtrl(self, value="8")
        self.integration_time_ns = wx.TextCtrl(self, value="1000")
        self.settle_time_ns = wx.TextCtrl(self, value="1000")

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
            form.Add(wx.StaticText(self, label=label))
            form.Add(control, 1, wx.EXPAND)

        box = wx.StaticBoxSizer(
            wx.StaticBox(self, label="Sequencer options"),
            wx.VERTICAL,
        )
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
        mode = self._get_mode()
        configuration = {
            "mode": mode,
            "x": self.x_panel.get_configuration(),
            'y': self.y_panel.get_configuration(),
            'acquisition': self.acquisition_panel.get_configuration(),
        }

        if mode == "sequencer_options":
            configuration["sequence_options"] = self.sequencer_options_panel.get_configuration()

        return configuration

    def set_configuration(self, configuration):
        if not isinstance(configuration, dict):
            raise ValueError("Video-mode configuration must be an object")

        mode = configuration.get("mode", "waveform")
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

        self.runner = VideoModeRunner(self.global_store, self.video_mode_config)

        try:
            self.runner.run()
        except Exception as error:
            wx.MessageBox(str(error), "Video Mode", wx.OK | wx.ICON_ERROR)
            self.runner = None
            return

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
        #TODO
        pass

    def OnStop(self,evt):
        if self.runner is not None:
            self.runner.stop()

    def OnClose(self, evt):
        if self.runner is not None:
            self.runner.stop()
        self.close_callback()
        evt.Skip()
        self.Destroy()