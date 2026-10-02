import predistort

import json
import datetime

from qblox_instruments import Cluster
from qblox_instruments.types import InstrumentType

class InstrumentQblox(predistort.Instrument):
	_QBLOX_MAX_WAVEFORM_TIME = 16384

	def __init__(self, cluster:Cluster, moduleType:InstrumentType,
			  	samplingRate:float, acquisitionAvgCount:int, ioPorts:str = "io0_1"):
		self.cluster = cluster
		self.moduleType = moduleType
		self.outSamplingRate = samplingRate
		self.acqSamplingRate = samplingRate
		self.acqAvgCount = acquisitionAvgCount
		self.ioPorts = ioPorts
		self.numSweeps = 1
		self.triggerLevel = 0

		# Current sequence dictionary
		self.currentSequence = {}

		#Set module
		self.module = None
		modules = [mod for mod in cluster.modules if mod.present()]
		for mod in modules:
			if mod.module_type == moduleType:
				self.module = mod
		if self.module == None:
			print("Error: Module of type " + str(self.moduleType) + " not found.")
			quit()
		
		self.cluster.reset()
		self.cluster.get_system_state()

		#Configure squencers for this experiment
		self.module.scope_acq_avg_mode_en_path0(True)
		self.module.scope_acq_sequencer_select(0)
		self.module.scope_acq_trigger_mode_path0("sequencer")
		self.module.scope_acq_trigger_mode_path1("sequencer")
		self.module.disconnect_inputs()
		self.module.disconnect_outputs()
		self.module.sequencer0.connect_sequencer(self.ioPorts)

	def loadSquarePulse(self, pulseLength: int) -> None:

		self.triggerLevel = 0.1

		# Make the sequence dictionary
		waveforms = {
			"pulse": {
				"data": [1.0 for i in range(16000)], 
				"index": 0,
			},
		}
		seq_prog = ""

		while pulseLength > 16000:
			seq_prog += f"""
			play			0,0,16000
			"""
			pulseLength -= 16000

		seq_prog += f"""
			play			0,0,{pulseLength}
			stop									# Stop sequence
			"""
		sequence = {
			"waveforms": waveforms,
			"weights": {},
			"acquisitions": {},
			"program": seq_prog,
		}

		self.currentSequence = sequence
		
		# Load sequence onto module	
		self.module.sequencer0.sequence(self.currentSequence)
		return

	def loadPulse(self, waveform: list[float]) -> None:
		
		# Limit waveform length
		if len(waveform) > InstrumentQblox._QBLOX_MAX_WAVEFORM_TIME:
			print("Unique waveform length too long")
			return

		self.triggerLevel = max(waveform)/10

		# Make the sequence dictionary
		waveforms = {
			"pulse": {
				"data": waveform, 
				"index": 0,
			},
		}
		seq_prog = f"""
			play    0,0,{len(waveform)}     #Play waveforms and wait 4ns.
			stop              #Stop.
			"""
		sequence = {
			"waveforms": waveforms,
			"weights": {},
			"acquisitions": {},
			"program": seq_prog,
		}

		self.currentSequence = sequence
		
		# Load sequence onto module	
		self.module.sequencer0.sequence(self.currentSequence)
		return

	def initializeAcquisition(self) -> None:
		seq_prog = f"""
			play    0,0,4     #Play waveforms and wait 4ns.
			acquire	0,0,{len(self.currentSequence["waveforms"]["pulse"]["data"])}
			stop              #Stop.
			"""
		acquisitions = {
			"response": {
				"num_bins": 1,
				"index": 0
			}
		}	

		self.currentSequence["program"] = seq_prog
		self.currentSequence["acquisitions"] = acquisitions
		
		# Load sequence onto module	
		self.module.sequencer0.sequence(self.currentSequence)
		return

	def arm(self) -> None:
		return
	
	def wait(self) -> None:
		return

	def playPulse(self) -> None:
		# Arm and start the sequencer
		self.module.arm_sequencer(0)
		self.module.start_sequencer()
		return

	def saveWaveform(self) -> list:
		self.module.get_sequencer_state(0, timeout=1)
		self.module.get_acquisition_state(0, 1)
		self.module.store_scope_acquisition(0, "response")
		acqData = self.module.get_acquisitions(0)
		acqData = acqData["response"]["acquisition"]["scope"]["path0"]["data"]
		self.module.delete_acquisition_data(0, all=True)
		return acqData