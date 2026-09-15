import wx
import spacq.gui.objectlistview as ObjectListView
from dataclasses import dataclass

from dataclasses import dataclass

@dataclass
class VariableRow:
    name: str
    value: float
    step_size: float
    resource: object = None
    action: str = "+ / -"

    def plus(self):
        self.value += self.step_size
        if self.resource is not None:
            self.resource.value = self.value

    def minus(self):
        self.value -= self.step_size
        if self.resource is not None:
            self.resource.value = self.value

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
        self.add_button.Bind(wx.EVT_BUTTON, self.on_add_parameter)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.olv, 1, wx.EXPAND)
        sizer.Add(self.add_button, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(sizer)

        self.refresh_rows()

    def refresh_rows(self):
        self.olv.SetObjects(self.rows)

    def add_variable_row(self, var):
        name = getattr(var, "name", None)
        if name is None:
            return
        if any(r.name == name for r in self.rows):
            return

        value = getattr(var, "value", 0.0)
        step = getattr(var, "step_size", 1.0)

        row = VariableRow(
            name=name,
            value=float(value),
            step_size=float(step),
            resource=getattr(var, "resource", None),
        )
        self.rows.append(row)
        self.refresh_rows()

    def on_add_parameter(self, evt):
        available = []
        existing = {r.name for r in self.rows}

        for var in getattr(self.global_store, "variables", []):
            if var.name not in existing:
                available.append(var.name)
        if not available:
            return

        dlg = wx.SingleChoiceDialog(
            self,
            "Select a variable to add",
            "Add parameter",
            available,
        )

        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.GetStringSelection()
            for var in self.global_store.variables:
                if var.name == name:
                    self.add_variable_row(var)
                    break

        dlg.Destroy()

class VideoDisplayPanel(wx.Panel):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.SetBackgroundColour(wx.Colour(235, 235, 235))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="Video display"), 0, wx.ALL | wx.ALIGN_CENTER, 10)
        self.SetSizer(sizer)

class VideoModeConfigPanel(wx.Panel):
    def __init__(self):
        pass

@dataclass
class VideoModeConfg:
    name: str = "video_mode"
    x_var: str = ""
    y_var: str = ""
    x_start: float = 0.0
    x_stop: float = 1.0
    x_steps: int = 100
    y_start: float = 0.0
    y_stop: float = 1.0
    y_steps: int = 100
    dwell_time: float = 0.01
    step_size: float = 0.01
    interleaving: int = 1
    measurement_backend: str = "agilent"
    acquisition_device: str = "scope"

class VideoModeFrame(wx.Frame):
    def __init__(self, parent, global_store, close_callback, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)

        self.global_store = global_store
        self.close_callback = close_callback

        main = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        splitter = wx.SplitterWindow(main)
        # Menu
        menuBar = wx.MenuBar()

        
        # VideoMode Configuration
        configure_video_mode = wx.Menu()
        menuBar.Append(configure_video_mode, "Configure")
        main_sizer.Add(menuBar)

        # Variable Control
        video_variable_panel = VideoVariablePanel(splitter, global_store)

        # Video Panel
        video_display_panel = VideoDisplayPanel(splitter)

        splitter.SplitVertically(video_variable_panel, video_display_panel, sashPosition=420)
        main_sizer.Add(splitter, 1, wx.EXPAND | wx.ALL, 4)

        # Measurement Controls
        button_bar = wx.BoxSizer(wx.HORIZONTAL)
        button_bar.Add(wx.Button(main, label="Start video mode"), 0, wx.ALL, 4)
        button_bar.Add(wx.Button(main, label="Capture frame"), 0, wx.ALL, 4)
        button_bar.Add(wx.Button(main, label="Stop"), 0, wx.ALL, 4)

        main_sizer.Add(button_bar, 0, wx.EXPAND | wx.ALL, 4)

        main.SetSizer(main_sizer)
        self.SetClientSize((900, 500))
        self.Bind(wx.EVT_CLOSE, self.OnClose)

    def OnClose(self, evt):
        self.close_callback()
        evt.Skip()
        self.Destroy()