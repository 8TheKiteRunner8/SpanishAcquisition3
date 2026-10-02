"""
benchmarking.py V 3.0

This library will be updated periodically when benchmarking experiments with Qblox are developed. These methods will
be designed to streamline the process of preparing a Quantum Dot device for experiments.

You may also use this file in tandem with the benchmarking.ipynb to get a good understanding of how this library works.

Created:		Apr 27, 2026
Last Updated:	Apr 27, 2026
Tested:         Apr 27, 2026
				On firmware:			2.0.0 
				On qblox-instruments:	1.2.1	

Author: Ben Van Osch

Edits: Made by Dhruv Shah detailed below

1. Updated functions to have generalized functionality on all instruments for both control and readout.

"""

# Imports
from __future__ import annotations 
from datetime import datetime
import matplotlib.pyplot as plt
import pyvisa
import os
import re
import pandas as pd
import numpy as np
from typing import TYPE_CHECKING, Callable
from qcodes.instrument import find_or_create_instrument
from qblox_instruments import Cluster, SpiRack, ClusterType
from zhinst.toolkit import Session
import sys
sys.path.append(r"C:\\Users\\coher\\Documents\\GitHub\\QBlox-CSG\\Libraries\\Qblox Sequence Helpers")
import sequence_helperV3 as sh
import yaml
import benchmarkingV3 as bm
import time
import h5py

# TODO: Add data logging for spirack_2D_sweep
# TODO: clean up data logs for run_1D_trace: fix data file headers, add timestamp to the data 
# TODO: Add signal output path for all the sweeps
# TODO: Add aquisition path for all the sweeps
# TODO: Function for frequency sweeps 
# TODO: Fix sequencer assignments

def get_connected_modules(cluster: Cluster, filter_fn: Callable | None = None):
			def checked_filter_fn(mod: ClusterType) -> bool:
				if filter_fn is not None:
					return filter_fn(mod)
				return True
			return {
				mod.slot_idx: mod for mod in cluster.modules if mod.present() and checked_filter_fn(mod)
			}

class QbloxExperiment:

	def __init__(self, 
				 config_file, 
				 save_path:str
				 ):
		
		self.save_path = save_path
		with open(config_file, 'r') as file:
			self.config_data = yaml.safe_load(file)

		# Connect Qblox Cluster

		if self.config_data['cluster']['connected']:
			self.cluster_ip = self.config_data['cluster']['cluster_ip']
			self.cluster_name = "cluster"
			self.cluster = find_or_create_instrument(
				Cluster,
				recreate=True,
				name=self.cluster_name,
				identifier=self.cluster_ip,
				dummy_cfg=(
					{
						2: ClusterType.CLUSTER_QCM,
						4: ClusterType.CLUSTER_QRM,
						6: ClusterType.CLUSTER_QCM_RF,
					}
					if self.cluster_ip is None
					else None
				),
			)

			self.cluster.led_brightness('medium') # Sets LED brightness on the modules. Options are 'low', 'medium' and 'high'
			# Get modules, and connect to the QRM
			
			modules = get_connected_modules(self.cluster)
			self.module = list(modules.values())[0]
			self.cluster.reset()

			print(modules)
			print(self.cluster.get_system_status())
			# self.dataI = None
			# self.dataQ = None
			self.qcm_module = modules[self.config_data['cluster']['qcm']['chassis_slot']]
			self.qrm_module = modules[self.config_data['cluster']['qrm']['chassis_slot']]
			self.rf_module = modules[self.config_data['cluster']['qcm_rf']['chassis_slot']]

		# Connect SPI Rack

		if self.config_data['spirack']['connected']:

			self.spirack = SpiRack("spirack", self.config_data['spirack']['port'])
			
			self.dac_name_Mods_and_Dacs = {}

			for module_no, module in enumerate(list(self.config_data['spirack']['modules'].keys())): 
				
				self.spirack.add_spi_module(self.config_data['spirack']['modules'][module]['address'], "D5a", f"module{module_no}")
				module_str = f"module{module_no}"
				curr_module = getattr(self.spirack, module_str)

				for dac in list(self.config_data['spirack']['modules'][module]['dacs'].keys()):
					
					dac_name = re.sub(r'(dac)(\d+)$', lambda m: f"{m.group(1)}{int(m.group(2)) - 1}", dac)
					curr_dac = getattr(curr_module, dac_name)
					
					span_val = self.config_data['spirack']['modules'][module]['dacs'][dac]['span']
					ramping_enable = self.config_data['spirack']['modules'][module]['dacs'][dac]['ramping_enabled']

					curr_dac.span(span_val)
					curr_dac.ramp_rate(self.config_data['spirack']['modules'][module]['dacs'][dac]['ramp_rate'])
					curr_dac.ramp_max_step(self.config_data['spirack']['modules'][module]['dacs'][dac]['ramp_max_step'])
					curr_dac.ramping_enabled(ramping_enable)

					# eval(f"self.spirack.{module_str}.{dac_name}.span('{span_val}')")
					# eval(f"self.spirack.{module_str}.{dac_name}.ramp_rate({self.config_data['spirack'
					# 														 ]['modules'][module]['dacs'][dac]['ramp_rate']})")
					# eval(f"self.spirack.{module_str}.{dac_name}.ramp_max_step({self.config_data['spirack'
					# 															 ]['modules'][module]['dacs'][dac]['ramp_max_step']})")
					# eval(f"self.spirack.{module_str}.{dac_name}.ramping_enabled({ramping_enable})")
					
					dac_config_name = self.config_data['spirack']['modules'][module]['dacs'][dac]['name']
					self.dac_name_Mods_and_Dacs[dac_config_name] = (module_str, dac_name)
					
		# Connect Zurich MFLI
		if self.config_data['MFLI']['connected']:

			self.zh_session = Session(self.config_data['MFLI']['session'])
			self.zh_mfli = self.zh_session.connect_device(self.config_data['MFLI']['device_id'])
			self.zh_mfli.system.identify(True)
			
			if self.config_data['MFLI']['zh_mfli_voltage']['input']['connected']:
				# zh_mfli_voltage input
				self.zh_mfli.demods[0].adcselect(0) # connects to sig in 1
				self.zh_mfli.demods[0].harmonic(1)
				self.zh_mfli.demods[0].phaseshift(0.00)
				self.zh_mfli.demods[0].order(self.config_data['MFLI']['zh_mfli_voltage']['input']['order'])
				self.zh_mfli.demods[0].timeconstant(self.config_data['MFLI']['zh_mfli_voltage']['input']['timeconstant'])
				self.zh_mfli.demods[0].sinc(self.config_data['MFLI']['zh_mfli_voltage']['input']['sinc'])
				self.zh_mfli.demods[0].enable(self.config_data['MFLI']['zh_mfli_voltage']['input']['pc_transfer_enable'])
				self.zh_mfli.demods[0].rate(self.config_data['MFLI']['zh_mfli_voltage']['input']['pc_transfer_rate'])
				self.zh_mfli.demods[0].trigger(0) # set trigger to continuous mode
				self.zh_mfli.sigins[0].range(self.config_data['MFLI']['zh_mfli_voltage']['input']['range'])
				self.zh_mfli.sigins[0].scaling(self.config_data['MFLI']['zh_mfli_voltage']['input']['scaling'])
				self.zh_mfli.sigins[0].ac(self.config_data['MFLI']['zh_mfli_voltage']['input']['ac'])
				self.zh_mfli.sigins[0].imp50(self.config_data['MFLI']['zh_mfli_voltage']['input']['imp50'])
				self.zh_mfli.sigins[0].diff(self.config_data['MFLI']['zh_mfli_voltage']['input']['diff'])
				self.zh_mfli.sigins[0].float(self.config_data['MFLI']['zh_mfli_voltage']['input']['float'])
			
			if self.config_data['MFLI']['zh_mfli_voltage']['output']['connected']:
				# zh_mfli_voltage output
				self.zh_mfli.oscs[0].freq(self.config_data['MFLI']['zh_mfli_voltage']['output']['osc_frequency'])
				self.zh_mfli.demods[1].adcselect(0) # connects reference to sig in 1
				self.zh_mfli.demods[1].harmonic(1)
				self.zh_mfli.demods[1].phaseshift(0.00)
				self.zh_mfli.sigouts[0].amplitudes[1](self.config_data['MFLI']['zh_mfli_voltage']['output']['amplitude'])
				self.zh_mfli.sigouts[0].imp50(self.config_data['MFLI']['zh_mfli_voltage']['output']['imp50'])
				self.zh_mfli.sigouts[0].range(self.config_data['MFLI']['zh_mfli_voltage']['output']['range'])
				self.zh_mfli.sigouts[0].offset(self.config_data['MFLI']['zh_mfli_voltage']['output']['offset'])
				self.zh_mfli.sigouts[0].add(self.config_data['MFLI']['zh_mfli_voltage']['output']['add'])
				self.zh_mfli.sigouts[0].diff(self.config_data['MFLI']['zh_mfli_voltage']['output']['diff'])
			
			if self.config_data['MFLI']['zh_mfli_voltage']['aux']['connected']:
				# zh_mfli_voltage aux
				aux_channel_no = self.config_data['MFLI']['zh_mfli_voltage']['aux']['aux_channel'] - 1
				self.zh_mfli.auxouts[aux_channel_no].outputselect(0) # set to demod R value
				self.zh_mfli.auxouts[aux_channel_no].preoffset(self.config_data['MFLI']['zh_mfli_voltage']['aux']['preoffset'])
				self.zh_mfli.auxouts[aux_channel_no].scale(self.config_data['MFLI']['zh_mfli_voltage']['aux']['scale'])
				self.zh_mfli.auxouts[aux_channel_no].offset(self.config_data['MFLI']['zh_mfli_voltage']['aux']['offset'])
				self.zh_mfli.auxouts[aux_channel_no].limitlower(self.config_data['MFLI']['zh_mfli_voltage']['aux']['limitlower'])
				self.zh_mfli.auxouts[aux_channel_no].limitupper(self.config_data['MFLI']['zh_mfli_voltage']['aux']['limitupper'])
				self.zh_mfli.auxouts[aux_channel_no].tipprotect.enable(self.config_data['MFLI']['zh_mfli_voltage']['aux']['tipprotect_enable'])
				self.zh_mfli.auxouts[aux_channel_no].tipprotect.polarity(self.config_data['MFLI']['zh_mfli_voltage']['aux']['tipprotect_polarity'])
				self.zh_mfli.auxouts[aux_channel_no].tipprotect.source(self.config_data['MFLI']['zh_mfli_voltage']['aux']['tipprotect_source'])
				self.zh_mfli.auxouts[aux_channel_no].tipprotect.value(self.config_data['MFLI']['zh_mfli_voltage']['aux']['tipprotect_value'])
			
			if self.config_data['MFLI']['zh_mfli_current']['input']['connected']:
				# zh_mfli_current input
				self.zh_mfli.demods[0].adcselect(1) # connects to curr in 1
				self.zh_mfli.demods[0].harmonic(1)  # why?
				self.zh_mfli.demods[0].phaseshift(0.00)
				self.zh_mfli.demods[0].order(self.config_data['MFLI']['zh_mfli_current']['input']['order'])
				self.zh_mfli.demods[0].timeconstant(self.config_data['MFLI']['zh_mfli_current']['input']['timeconstant'])
				self.zh_mfli.demods[0].sinc(self.config_data['MFLI']['zh_mfli_current']['input']['sinc'])
				self.zh_mfli.demods[0].enable(self.config_data['MFLI']['zh_mfli_current']['input']['pc_transfer_enable'])
				self.zh_mfli.demods[0].rate(self.config_data['MFLI']['zh_mfli_current']['input']['pc_transfer_rate'])
				self.zh_mfli.demods[0].trigger(0) # set trigger to continuous mode

				self.zh_mfli.currins[0].range(self.config_data['MFLI']['zh_mfli_current']['input']['range'])
				self.zh_mfli.currins[0].scaling(self.config_data['MFLI']['zh_mfli_current']['input']['scaling'])
				self.zh_mfli.currins[0].float(self.config_data['MFLI']['zh_mfli_current']['input']['float'])

			if self.config_data['MFLI']['zh_mfli_current']['output']['connected']:
				#zh_mfli_current output
				self.zh_mfli.oscs[0].freq(self.config_data['MFLI']['zh_mfli_current']['output']['osc_frequency'])
				self.zh_mfli.demods[1].adcselect(1) # connects reference to sig in 1
				self.zh_mfli.demods[1].harmonic(1)
				self.zh_mfli.demods[1].phaseshift(0.00)
				self.zh_mfli.sigouts[0].amplitudes[1](self.config_data['MFLI']['zh_mfli_current']['output']['amplitude'])
				self.zh_mfli.sigouts[0].imp50(self.config_data['MFLI']['zh_mfli_current']['output']['imp50'])
				self.zh_mfli.sigouts[0].range(self.config_data['MFLI']['zh_mfli_current']['output']['range'])
				self.zh_mfli.sigouts[0].offset(self.config_data['MFLI']['zh_mfli_current']['output']['offset'])
				self.zh_mfli.sigouts[0].add(self.config_data['MFLI']['zh_mfli_current']['output']['add'])
				self.zh_mfli.sigouts[0].diff(self.config_data['MFLI']['zh_mfli_current']['output']['diff'])

			if self.config_data['MFLI']['zh_mfli_current']['aux']['connected']:
				# zh_mfli_current aux
				aux_channel_no = self.config_data['MFLI']['zh_mfli_current']['aux']['aux_channel'] - 1
				self.zh_mfli.auxouts[aux_channel_no].outputselect(0) # set to demod R value
				self.zh_mfli.auxouts[aux_channel_no].preoffset(self.config_data['MFLI']['zh_mfli_current']['aux']['preoffset'])
				self.zh_mfli.auxouts[aux_channel_no].scale(self.config_data['MFLI']['zh_mfli_current']['aux']['scale'])
				self.zh_mfli.auxouts[aux_channel_no].offset(self.config_data['MFLI']['zh_mfli_current']['aux']['offset'])
				self.zh_mfli.auxouts[aux_channel_no].limitlower(self.config_data['MFLI']['zh_mfli_current']['aux']['limitlower'])
				self.zh_mfli.auxouts[aux_channel_no].limitupper(self.config_data['MFLI']['zh_mfli_current']['aux']['limitupper'])
				self.zh_mfli.auxouts[aux_channel_no].tipprotect.enable(self.config_data['MFLI']['zh_mfli_current']['aux']['tipprotect_enable'])
				self.zh_mfli.auxouts[aux_channel_no].tipprotect.polarity(self.config_data['MFLI']['zh_mfli_current']['aux']['tipprotect_polarity'])
				self.zh_mfli.auxouts[aux_channel_no].tipprotect.source(self.config_data['MFLI']['zh_mfli_current']['aux']['tipprotect_source'])
				self.zh_mfli.auxouts[aux_channel_no].tipprotect.value(self.config_data['MFLI']['zh_mfli_current']['aux']['tipprotect_value'])

			self.daq_module = self.zh_session.modules.daq
			self.filename = self.config_data['MFLI']['zh_mfli_daq']['save_filename']
			self.daq_module.triggernode(self.config_data['MFLI']['zh_mfli_daq']['triggernode'])
			self.daq_module.type(self.config_data['MFLI']['zh_mfli_daq']['type'])
			self.daq_module.clearhistory(self.config_data['MFLI']['zh_mfli_daq']['clearhistory'])
			self.daq_module.bandwidth(self.config_data['MFLI']['zh_mfli_daq']['bandwidth'])
			self.daq_module.historylength(self.config_data['MFLI']['zh_mfli_daq']['historylength'])
			self.daq_module.save.directory(self.save_path)
			self.daq_module.save.filename(self.filename)
			self.daq_module.save.fileformat(self.config_data['MFLI']['zh_mfli_daq']['fileformat']) # 1 for csv, 4 for hdf5
			self.daq_module.endless(self.config_data['MFLI']['zh_mfli_daq']['endless'])
			self.daq_module.subscribe(self.config_data['MFLI']['zh_mfli_daq']['subscribe'])

		"""
		TODO Add functionality that automatically detects and informs the user of which modules are connected 
		to which slots, rather than the slots being predefined. Also add error if there are any issues with modules connecting
		"""

		return None

	def setup_mfli(self, 
				   on:bool = False, 
				   freq:float = 70000.00000000, 
				   Amp_Vpk:float = 0.1, 
				   timeconstant:float = 20e-6,
				   rate:float = 100000000.00000000
				   ):
		
		self.zh_mfli.sigouts[0].on(0)
		self.zh_mfli.demods[0].timeconstant(timeconstant)
		self.zh_mfli.sigouts[0].amplitudes[1](Amp_Vpk)
		self.zh_mfli.auxouts[3].outputselect(2)
		self.zh_mfli.auxouts[3].scale(1.00000000)
		self.zh_mfli.oscs[0].freq(freq)
		self.zh_mfli.sigins[0].ac(0)
		self.zh_mfli.sigins[0].scaling(1.40000000)
		self.zh_mfli.demods[0].order(4)
		self.zh_mfli.sigins[0].range(1.00000000)
		self.zh_mfli.demods[0].rate(rate)
		
		self.zh_mfli.sigouts[0].enables[1](1)

		if on is True:
			self.zh_mfli.sigouts[0].on(1)
			print("MFLI setup completed. The MFLI is currently on")
		else:
			self.zh_mfli.sigouts[0].on(0)
			print("MFLI setup completed. The MFLI is currently off")

		return None

	def spirack_1D_sweep(self, 
					  	 sweep_DAC_name:str, 
						 voltage_range:tuple, 
						 step_size: float = 10e-3,
			  		   	 time_per_point = 1e-4, 
					   	 voltage_configuration: dict[str,tuple] = {},
					   	 plot = False
					   	 ):

		"""
		This function allows the user to create a 1D Voltage Trace with minimal interaction with Q1ASM and the Qblox cluster.

		Args:

		num_steps:         The number of voltage steps in the sweep for both gates
		length_per_point:  The full duration of a single point in nanoseconds
		start_point:       The starting voltage for the trace in volts
		end_point:         The ending voltages for the trace in volts
		acq_sequencer:     The label of the sequencer being used for acquisition
		acquisition_name:  The name given to the acquisition
		acquisition_delay: The amount of time before an acquisition starts in nanoseconds 
		plot:              If True, run_2D_sweep() will create a heat map of the data; Set to False by default 
						   and will output a voltage sweep through the qcm into a device, and will acquire a signal from the device, which will
						   then be plotted against the input voltage. 
							
		"""
		
		
		# First, we obtain all the names for each dac
		
		dac_name_list = list(self.dac_name_Mods_and_Dacs.keys())

		# Then, we determine which dacs are being set in the provided voltage configuration

		dacs_and_vals = []

		for name in dac_name_list:

			if name in voltage_configuration:
				
				dacs_and_vals.append((self.dac_name_Mods_and_Dacs.get(name), voltage_configuration.get(name)))


# Now, we can set the voltage for each dac
		
		for info, val in dacs_and_vals:	

			curr_module = getattr(self.spirack, info[0])
			curr_dac = getattr(curr_module, info[1])

			curr_dac.voltage(val)
			while curr_dac.is_ramping():
				time.sleep(0.00001)

			curr_voltage = curr_dac.voltage()
			print(f"{info[1]} of {info[0]} has been set to: {curr_voltage} V")
			print(f"curr_module is {curr_module}, curr_dac is {curr_dac}")

		curr_module_1 = getattr(self.spirack, self.dac_name_Mods_and_Dacs.get(sweep_DAC_name)[0])
		curr_dac_1 = getattr(curr_module_1, self.dac_name_Mods_and_Dacs.get(sweep_DAC_name)[1])

		volt_sweep_1 = np.arange(voltage_range[0], voltage_range[1]+step_size, step_size)

		# Setting the initial voltage (in case of a mismatch from previous sweeps)
		curr_dac_1.voltage(voltage_range[0])
		while curr_dac_1.is_ramping():
			time.sleep(0.0001)


#MFLI setup
		""" self.daq_module.execute()
		self.zh_mfli.sigouts[0].on(1) """




#Start Sweeping
		for v1 in volt_sweep_1:
			
			curr_dac_1.voltage(v1)
			print(f"setting {curr_dac_1} to {v1} V")
			while curr_dac_1.is_ramping():
				time.sleep(0.00001)
			time.sleep(time_per_point)

		curr_dac_1.voltage(0)
		while curr_dac_1.is_ramping():
			time.sleep(0.00001)

#stop MFLI

		""" self.zh_mfli.sigouts[0].on(0)
		
		self.daq_module.finish()
		self.daq_module.unsubscribe('*')
		time.sleep(1)

		self.daq_module.save.save(1)
		time.sleep(0.5) """

		if plot:

			file_path = f"{self.save_path}\{self.filename}_000\\{self.filename}_00000.h5"
			print(file_path)
			dataframes = []
			
			time.sleep(0.5)
			with h5py.File(file_path, 'r+') as f:
				for group_name in f.keys():  # e.g. '000', '001', ...
					try:
						timestamp_path = f"{group_name}/dev3901/demods/0/sample.r.avg/timestamp"
						value_path = f"{group_name}/dev3901/demods/0/sample.r.avg/value"

						timestamps = f[timestamp_path][:]
						values = f[value_path][:]

						df = pd.DataFrame({
							"timestamp": timestamps,
							"value": values,
							"group": group_name
						})

						dataframes.append(df)
					except KeyError as e:
						print(f"Skipping {group_name}: {e}")
			
			f.close()

			# Concatenate all into one DataFrame
			full_df = pd.concat(dataframes, ignore_index=True)

			plt.figure(figsize=(10, 5))
			plt.plot(full_df['timestamp'], full_df['value'], marker='.', linestyle='-', markersize=3)
			plt.xlabel("Timestamp (raw units)")
			plt.ylabel("Value")
			plt.title("Demodulated Signal vs Timestamp")
			plt.grid(True)
			plt.tight_layout()
			plt.show()

		return None

	def spirack_2D_sweep(self, 
					     sweep_DAC_names:list[str], 
						 voltage_ranges:list[tuple], 
						 step_size: float = 10e-3,
			  		     time_per_point = 10e-3, 
					     voltage_configuration: dict[str,tuple] = {},
					     plot = False
					     ):

		"""
		This function allows the user to create a 2D Voltage Trace with minimal interaction with Q1ASM and the Qblox cluster.

		The function takes inputs:

		num_steps:         The number of voltage steps in the sweep for both gates
		length_per_point:  The full duration of a single point in nanoseconds
		start_point:       The starting voltage for the trace in volts
		end_point:         The ending voltages for the trace in volts
		acq_sequencer:     The label of the sequencer being used for acquisition
		acquisition_name:  The name given to the acquisition
		acquisition_delay: The amount of time before an acquisition starts in nanoseconds 
		plot:              If True, run_2D_sweep() will create a heat map of the data; Set to False by default 
						   and will output a voltage sweep through the qcm into a device, and will acquire a signal from the device, which will
						   then be plotted against the input voltage. 
							
		"""

		# First, we obtain all the names for each dac
		
		dac_name_list = list(self.dac_name_Mods_and_Dacs.keys())

		# Then, we determine which dacs are being set in the provided voltage configuration

		dacs_and_vals = []

		for name in dac_name_list:

			if name in voltage_configuration:
				
				dacs_and_vals.append((self.dac_name_Mods_and_Dacs.get(name), voltage_configuration.get(name)))

		# Now, we can set the voltage for each dac

		for info, val in dacs_and_vals:	

			curr_module = getattr(self.spirack, info[0])
			curr_dac = getattr(curr_module, info[1])

			curr_dac.voltage(val)
			while curr_dac.is_ramping():
				time.sleep(0.001)

			curr_voltage = curr_dac.voltage()
			print(f"{info[1]} of {info[0]} has been set to: {curr_voltage} V")

		curr_module_1 = getattr(self.spirack, self.dac_name_Mods_and_Dacs.get(sweep_DAC_names[0])[0])
		curr_dac_1 = getattr(curr_module_1, self.dac_name_Mods_and_Dacs.get(sweep_DAC_names[0])[1])
		volt_sweep_1 = np.arange(voltage_ranges[0][0], voltage_ranges[0][1] + step_size, step_size)

		curr_module_2 = getattr(self.spirack, self.dac_name_Mods_and_Dacs.get(sweep_DAC_names[1])[0])
		curr_dac_2 = getattr(curr_module_2, self.dac_name_Mods_and_Dacs.get(sweep_DAC_names[1])[1])
		volt_sweep_2 = np.arange(voltage_ranges[1][0], voltage_ranges[1][1] + step_size, step_size)

		# Setting the initial voltage (in case of a mismatch from previous sweeps)
		curr_dac_1.voltage(voltage_ranges[0][0])
		curr_dac_2.voltage(voltage_ranges[1][0])
		while curr_dac_1.is_ramping() or curr_dac_2.is_ramping():
			time.sleep(0.001)

		self.zh_mfli.sigouts[0].on(0)
		time.sleep(0.01)
		self.daq_module.execute()
		time.sleep(0.01)
		self.zh_mfli.sigouts[0].on(1)
		
		

		for v2 in volt_sweep_2:
			
			for v1 in volt_sweep_1:
				
				curr_dac_1.voltage(v1)
				print(f"setting {curr_dac_1} to {v1} V")
				while curr_dac_1.is_ramping():
					time.sleep(0.001)
				time.sleep(time_per_point)

			curr_dac_1.voltage(0)
			while curr_dac_1.is_ramping():
				time.sleep(0.001)

			curr_dac_2.voltage(v2)
			print(f"setting {curr_dac_2} to {v2} V")
			while curr_dac_2.is_ramping():
				time.sleep(0.001)
			time.sleep(time_per_point)

		curr_dac_1.voltage(0)
		curr_dac_2.voltage(0)
		while curr_dac_2.is_ramping() or curr_dac_1.is_ramping():
			time.sleep(0.001)

		time.sleep(0.1)
		self.zh_mfli.sigouts[0].on(0)
		self.daq_module.finish()
		self.daq_module.unsubscribe('*')
		time.sleep(1)

		self.daq_module.save.save(1)
		time.sleep(5)

		
		if plot:

			file_path = f"{self.save_path}\\{self.filename}_000\\{self.filename}_00000.h5"
			print(file_path)
			dataframes = []
			
			with h5py.File(file_path, 'r+') as f:
				for group_name in f.keys():  # e.g. '000', '001', ...
					try:
						timestamp_path = f"{group_name}/dev3901/demods/0/sample.r.avg/timestamp"
						value_path = f"{group_name}/dev3901/demods/0/sample.r.avg/value"

						timestamps = f[timestamp_path][:]
						values = f[value_path][:]

						df = pd.DataFrame({
							"timestamp": timestamps,
							"value": values,
							"group": group_name
						})

						dataframes.append(df)
					except KeyError as e:
						print(f"Skipping {group_name}: {e}")
			time.sleep(50)
			f.close()

			# Concatenate all into one DataFrame
			full_df = pd.concat(dataframes, ignore_index=True)

			plt.figure(figsize=(10, 5))
			plt.plot(full_df['timestamp'], full_df['value'], marker='.', linestyle='-', markersize=3)
			plt.xlabel("Timestamp (raw units)")
			plt.ylabel("Value")
			plt.title("Demodulated Signal vs Timestamp")
			plt.grid(True)
			plt.tight_layout()
			plt.show()

		return None

	def average_results_csv_only(self, files):
		import pandas as pd
		from pathlib import Path
		import os

		# 1) Specify the paths for 10 CSV files (you can put them in a list or use a wildcard to find them)
		# Method 1: List them manually
		# files = [
		#     "file1.csv", "file2.csv", "file3.csv", "file4.csv", "file5.csv",
		#     "file6.csv", "file7.csv", "file8.csv", "file9.csv", "file10.csv",
		# ]

		path = self.save_path

		# Method 2: Use a wildcard to collect files from a folder (make sure it matches exactly these 10 files)
		# files = sorted(Path(path).glob("*.csv"))[:csv_file_number]  # Change this to your folder

		# 2) Read files into a list of DataFrames
		dfs = [pd.read_csv(f) for f in files]

		# 3) Check if the column names and order are the same (optional, but recommended)
		cols0 = dfs[0].columns
		for i, df in enumerate(dfs[1:], start=2):
			if not df.columns.equals(cols0):
				raise ValueError(f"File {i} has different column names/order from file 1. Please fix them first.")

		# 4) Average the numeric columns element-wise
		# Combine all rows, then calculate the mean for numeric columns.
		# For non-numeric columns, keep the values from the first file.
		df_all = pd.concat(dfs, axis=0, ignore_index=True)

		# Find numeric columns
		num_cols = df_all.select_dtypes(include="number").columns

		# Average by aligning rows from each file:
		# Method: stack the numeric columns from each DataFrame into a 3D array, then average across files.
		# A simpler way: take the mean for each cell in numeric columns directly.
		# Step 1: Extract numeric columns from each DataFrame and stack them together
		import numpy as np

		for i, df in enumerate(dfs):
			print(f"File {i+1}: {df.shape}")
	    
		num_arrays = np.stack([df[num_cols].to_numpy() for df in dfs], axis=0)  # shape: (10, n_rows, n_num_cols)
		mean_num = num_arrays.mean(axis=0)  # shape: (n_rows, n_num_cols)

		# Create the final result: keep the non-numeric columns from the first DataFrame,
		# and replace the numeric columns with the averaged values
		result = dfs[0].copy()
		result[num_cols] = mean_num

		# Remove any extra spaces around column names
		result.columns = result.columns.str.strip()
		
		
		experiment_time = datetime.now()
		timestamp = experiment_time.strftime('%Y-%m-%d_%H-%M%S')
		file_name = f"{timestamp}_averaged_result.csv"
		file_path = os.path.join(path, file_name)

		# 5) Save the result to a CSV file
		result.to_csv(file_path, index=False)
		import numpy as np
		import pandas as pd
		import matplotlib.pyplot as plt
		from matplotlib.colors import Normalize, LogNorm
		from scipy.interpolate import griddata

		# 1) Read data
		fname = os.path.join(self.save_path,file_name)
		df = pd.read_csv(fname, header=0)

		x_col = "QCM Output 1"
		y_col = "QCM Output 2"
		z_col = "QRM Input Path Q"

		x = df[x_col].to_numpy()
		y = df[y_col].to_numpy()
		z = df[z_col].to_numpy()

		# Optionally drop rows with NaNs in x,y,z before interpolation
		mask_valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
		x, y, z = x[mask_valid], y[mask_valid], z[mask_valid]

		# 2) Regular grid
		nx, ny = 400, 400
		xi = np.linspace(x.min(), x.max(), nx)
		yi = np.linspace(y.min(), y.max(), ny)
		Xi, Yi = np.meshgrid(xi, yi)

		# 3) Interpolate
		Zi = griddata(points=np.column_stack([x, y]), values=z, xi=(Xi, Yi), method='linear')

		# 3a) Mask invalid
		masked_Zi = np.ma.masked_invalid(Zi)

		# 3b) Choose normalization so yellow = highest plotted value
		# Option A: simple min/max on valid interpolated grid
		grid_vals = masked_Zi.compressed()  # 1D array of valid values

		if grid_vals.size == 0:
			# Fallback: if interpolation produced all NaNs (e.g., convex hull too small),
			# use raw z for limits and plot scatter only
			vmin = np.nanmin(z)
			vmax = np.nanmax(z)
			use_grid = False
		else:
			use_grid = True
			# Robust clipping to improve contrast (adjust percentiles as needed)
			lo, hi = np.percentile(grid_vals, [2, 98])
			vmin = lo
			vmax = hi
			if vmin >= vmax:  # guard against degenerate case
				vmin = grid_vals.min()
				vmax = grid_vals.max()

		# Optional: if z spans orders of magnitude and is positive, consider LogNorm
		use_log = False  # set True if you want logarithmic color scaling and z>0
		norm = LogNorm(vmin=max(vmin, np.finfo(float).eps), vmax=vmax) if use_log else Normalize(vmin=vmin, vmax=vmax)

		# 4) Plot
		plt.figure(figsize=(7, 6), dpi=150)
		cmap = "viridis"  # yellow is the top of viridis

		if use_grid:
			im = plt.pcolormesh(Xi, Yi, masked_Zi, shading='auto', cmap=cmap, norm=norm)
		else:
			# If grid unusable, create a dummy image for colorbar scaling
			# and rely on scatter for visualization
			im = plt.scatter([], [], c=[], cmap=cmap, norm=norm)  # placeholder

		# Overlay original points with same normalization
		plt.scatter(x, y, c=z, s=5, cmap=cmap, norm=norm, edgecolor='none', alpha=0.5)

		# Colorbar reflecting the chosen normalization
		cbar = plt.colorbar(im)
		cbar.set_label(z_col)

		# Labels and title
		plt.xlabel(x_col)
		plt.ylabel(y_col)
		plt.title(f"2D Heatmap of {z_col} (renormalized)")

		plt.tight_layout()
		plt.show()

		return(print(f"Saved at {file_path}"))

	def run_1D_trace(self, 
					 qcm_output_name: str,
					 voltage_range: tuple,
					 step_size: float,
					 time_per_point: float,
					 acquisition_delay: float = 0.0, 
					 repeats: int = 0,    
					 voltage_configuration: dict[str,tuple] = {},
					 plot = False
					 ):

		"""
		This function allows the user to create a 1D Voltage Trace with minimal interaction with Q1ASM and the Qblox cluster.

		The function takes inputs:

		qcm_output_name:        The name of the DAC used for the trace
		voltage_range:          The start and end points for the sweep in volts
		step_size:              The step size between points in volts
		time_per_point:         The time of each step in nanoseconds
		acquisition_delay:      The amount of time before an acquisition starts in nanoseconds
		repeats:                The number of times a sequence is repeated
		voltage_configuration:  The DC voltages set by the Spi Rack before the sweep is performed
		plot:                   If True, run_2D_sweep() will create a heat map of the data; Set to False by default 
						        and will output a voltage sweep through the qcm into a device, and will acquire a signal from the device, which will
						        then be plotted against the input voltage. 
							
		"""

		if self.config_data['MFLI']['connected']:
			self.setup_mfli(on=True, Amp_Vpk = 1.0, timeconstant = 20e-6)
		
		dataI = []

		# First, we obtain all the names for each dac if the Spi Rack is connected
		
		if self.config_data['spirack']['connected']:

			dac_name_list = list(self.dac_name_Mods_and_Dacs.keys())

			# Then, we determine which dacs are being set in the provided voltage configuration

			dacs_and_vals = []

			for name in dac_name_list:

				if name in voltage_configuration:
					
					dacs_and_vals.append((self.dac_name_Mods_and_Dacs.get(name), voltage_configuration.get(name)))

			# Now, we can set the voltage for each dac

			for info, val in dacs_and_vals:	

				curr_module = getattr(self.spirack, info[0])
				curr_dac = getattr(curr_module, info[1])

				curr_dac.voltage(val)
				while curr_dac.is_ramping():
					time.sleep(0.001)

				curr_voltage = curr_dac.voltage()
				print(f"{info[1]} of {info[0]} has been set to: {curr_voltage} V")

		# First, we define an empty output seqeunce, basically an empty list. We also define our voltage sweep parameters

		output_seq_0 = []

		seq_time = 0

		output_0_list = np.arange(voltage_range[0], voltage_range[1] + step_size, step_size)

		num_steps = len(output_0_list)

		# print(output_0_list)

		# Here, we append the steps to the output_sqe_0 list

		for i in output_0_list:
			output_seq_0.append(['square', time_per_point, i])
		
		seq_time += time_per_point*num_steps

		# Now, we define our input sequence.

		input_seq_0 = ['acq_0', acquisition_delay, time_per_point*num_steps]

		# Now, we disconnect any prexsisting connections

		qcm_module = self.qcm_module
		qrm_module = self.qrm_module
		rf_module = self.rf_module
		cluster = self.cluster

		# TODO add error message if there are mislabeled modules

		sh.disconnect_io(qcm_module)
		sh.disconnect_io(qrm_module)

		I_offset, Q_offset = sh.acquire_scope_and_calc_offsets(qrm_module)

		sh.disconnect_io(qcm_module)
		sh.disconnect_io(qrm_module)
		sh.disconnect_io(rf_module)

		# Now, we upload our sequences to the modules and specify sequencers		

		qcm_module.sequencer0.sequence(sh.make_output_sequence(output_seq_0, module = "qcm", iterations = repeats))
		qrm_module.sequencer0.sequence(sh.make_input_sequence(input_seq_0, resolution = time_per_point)[0])
		rf_module.sequencer0.sequence(sh.marker_only_sequence())

		seq_time += 150 # Accounting for ToF

		# Now, we connect the modules to the sequencers

		output_num = qcm_output_name.replace("O", "")
		connector = "connect_out" + str(int(output_num) - 1)
		curr_out = getattr(qcm_module.sequencer0, connector)
		
		curr_out("I")
		sh.connect_input(module = qrm_module, sequencer = 0, input_index = 0, path = 0, resolution = time_per_point)

		""" 
		The QRM has a built-in amplifier that automatically amplifies any imput signal by 6 dB. In the following, 
		we are simply offsetting that amplification. There's also a DC offset, which we account for here.
		
		"""
		input_gain = -6

		qrm_module.in0_gain(input_gain)
		qrm_module.in1_gain(input_gain)
		
		if abs(I_offset) > 0.02:
			qrm_module.in0_offset(-0.0)
		else:
			qrm_module.in0_offset(-I_offset)

		qrm_module.in1_offset(-Q_offset)

		# Then, we enable the sync protocol for all sequencers

		qcm_module.sequencer0.sync_en(True)
		qrm_module.sequencer0.sync_en(True)
		rf_module.sequencer0.sync_en(True)

		# Here we arm the sequencers

		qcm_module.arm_sequencer(0)
		qrm_module.arm_sequencer(0)
		rf_module.arm_sequencer(0)

		# This next step runs the sequences we loaded

		cluster.start_sequencer()

		# Convert time from seconds to nanoseconds

		seq_time /= 1e9

		time.sleep(seq_time)

		# Then, we stop the sequencers

		qcm_module.stop_sequencer(0)
		qrm_module.stop_sequencer(0)
		rf_module.stop_sequencer(0)

		if self.config_data['MFLI']['connected']:
			self.zh_mfli.sigouts[0].on(0)

		X = np.asarray(output_0_list)

		for data0 in dataI:

			# Now, we have to reshape the data to be the same shape as X and Y

			if data0.shape[0] % (len(output_0_list)) != 0:
				raise ValueError(f"The amount of data taken is unable to be shaped into {np.shape(X)}")
				
			data0_reshaped = data0.reshape(X.shape[0], repeat_num)

			data0_final = data0_reshaped.mean(axis=1) * repeat_num

			#data0_final = avg_data0.reshape(X.shape[0],X.shape[1])
	
		# If plot is True, then this part of the method will create a heat map as a function of the voltages

		if plot:
			experiment_time = datetime.now()
			timestamp = experiment_time.strftime('%Y-%m-%d_%H-%M%S')

			filename = f"{timestamp}1D_trace_NumSteps{num_steps}_LengthPerPoint{time_per_point}_fineGateVol_coarseGateVol_startPoint{voltage_range[0]}_endPoint{voltage_range[1]}_resolution{time_per_point}"

			sh.plot_input(module = qrm_module, sequencer = 0, acquisition_name = 'acq_0', save_path = self.save_path, filename = filename)

			# This section retrieves the data from the acquisition

			qrm_module.get_acquisition_status(0) # Wait for the sequencer to stop with a timeout period of one minute.
			qrm_module.store_scope_acquisition(0, 'acq_0') # Move acquisition data from temporary memory to acquisition list.
			readout_data = qrm_module.get_acquisitions(0) # Get acquisition list from instrument.

			resolution = qrm_module.sequencer0.integration_length_acq()

			if resolution == time_per_point:
				pass
			else:
				time_per_point = resolution

			# Find the number of bins to determine what kind of acquistion we are doing.
			num_bins = len(readout_data['acq_0']['acquisition']['bins']['integration']['path0'])

			# print("number of bins: ",num_bins)

			if num_bins == 1:

				# print(f"resolution in plot_input: {resolution}")
				# If it is a single acquisition with resolution of 1 ns
				data0 = readout_data['acq_0']['acquisition']['scope']['path0']['data'] # Extract path 0 data

			else:
				
				repeat_num = sh.make_input_sequence(input_seq_0, resolution = time_per_point)[1]

				# print(f"resolution in plot_input: {resolution}")

				data0 = np.array(readout_data['acq_0']['acquisition']['bins']['integration']['path0']) / time_per_point # Extract path 0 data

			#print(data0)

			#print(data0.shape)

			# Now, we add this sweeps data to the data list

			dataI.append(data0)

			if repeats > 1:
				self.average_results_csv_only(csv_file_number = repeats)

		# Resets the connection to the cluster. If not done, the offsets will be incorrect when playing another sequence.

		cluster.reset()

		return None

	def run_2D_sweep(self, 
					 qcm_output_names: list[str],
					 voltage_ranges: list[tuple],
					 step_size: float,
					 time_per_point,
					 acquisition_delay: float = 0.0, 
					 repeats: int = 0,    
					 voltage_configuration: dict[str,tuple] = {},
					 plot = False
					 ):

		"""
		This function allows the user to create a 1D Voltage Trace with minimal interaction with Q1ASM and the Qblox cluster.

		The function takes inputs:

		qcm_output_names:       The name of the DACs used for the trace sweep
		voltage_ranges:         The start and end points for the sweeps in volts
		step_size:              The step size between points in volts
		time_per_point:         The time of each step in nanoseconds
		acquisition_delay:      The amount of time before an acquisition starts in nanoseconds
		repeats:                The number of times a sequence is repeated
		voltage_configuration:  The DC voltages set by the Spi Rack before the sweep is performed
		plot:                   If True, run_2D_sweep() will create a heat map of the data; Set to False by default 
						        and will output a voltage sweep through the qcm into a device, and will acquire a signal from the device, which will
						        then be plotted against the input voltage. 
							
		"""



		"""
		WARMING: start_points and end_points have to be symmetric when using the bias tees. For example, 
		start_points = [-0.5, -0.5], end_points = [0.5, 0.5].

		The reason is that if it is not symmetric about 0, the capactior will see the signal as an AC signal
		with some DC offset. This causes the "DC signal" to relax to zero. 
		"""

		if self.config_data['MFLI']['connected']:
			self.setup_mfli(on=True, Amp_Vpk = 1.0, timeconstant = 20e-6)
		
		# Before anything, we have to set up the data variable to hold the data from each repeat

		dataI = []
		dataQ = []

		# Obtain List of Dacs	
		dac_name_list = list(self.dac_name_Mods_and_Dacs.keys())

		# Determine dacs to be set to the provided voltage configuration

		dacs_and_vals = [(self.dac_name_Mods_and_Dacs.get(name), voltage_configuration.get(name)) 
				   for name in dac_name_list 
				   if name in voltage_configuration]

		# Set the voltage for each dac
		for info, val in dacs_and_vals:	

			curr_module = getattr(self.spirack, info[0])
			curr_dac = getattr(curr_module, info[1])

			curr_dac.voltage(val)
			while curr_dac.is_ramping():
				time.sleep(0.001)

			curr_voltage = curr_dac.voltage()
			print(f"{info[1]} of {info[0]} has been set to: {curr_voltage} V")


		for i in range(repeats):

			# Define an empty output seqeunce and voltage sweep parameters

			output_seq_0 = []

			seq_time = 0

			output_0_list = np.arange(voltage_ranges[0][0], voltage_ranges[0][1] + step_size, step_size)
			num_steps_0 = len(output_0_list)

			for i in output_0_list:
				output_seq_0.append(['square', time_per_point, i])

			# Now, we need to define our second output sequence 

			output_seq_1 = []

			output_1_list = np.arange(voltage_ranges[1][0], voltage_ranges[1][1] + step_size, step_size)

			num_steps_1 = len(output_1_list)

			for i in output_1_list:
				output_seq_1.append(['square', time_per_point*num_steps_0, i])

			seq_time += time_per_point*num_steps_0*num_steps_1 # Accounting for length of the sequence

			# Finally, we define our input sequence

			if plot:

				input_seq_0 = ['acq_0', acquisition_delay, time_per_point*num_steps_0*num_steps_1]

		### --- Set Up Voltage Sweep --- ###
			qcm_module = self.qcm_module
			qrm_module = self.qrm_module
			cluster = self.cluster

			# TODO add error message if there are mislabeled modules
			# Disconnect any pre-existing connections.
			sh.disconnect_io(qcm_module)
			sh.disconnect_io(qrm_module)

			# Acquire offsets
			I_offset, Q_offset = sh.acquire_scope_and_calc_offsets(qrm_module)

			sh.disconnect_io(qcm_module)
			sh.disconnect_io(qrm_module)

			# Upload sequences to modules and specify sequencers		
			qcm_module.sequencer0.sequence(sh.make_output_sequence(output_seq_0, module = "qcm", iterations = num_steps_1))
			qcm_module.sequencer1.sequence(sh.make_output_sequence(output_seq_1, module = "qcm"))
			qrm_module.sequencer0.sequence(sh.make_input_sequence(input_seq_0, resolution = time_per_point)[0])

			seq_time += 150 # Accounting for ToF

			# Connect modules to sequencers
			output_num0 = qcm_output_names[0].replace("O", "")
			connector0 = "connect_out" + str(int(output_num0) - 1)
			curr_out0 = getattr(qcm_module.sequencer0, connector0)
			curr_out0("I")

			output_num1 = qcm_output_names[1].replace("O", "")
			connector1 = "connect_out" + str(int(output_num1) - 1)
			curr_out1 = getattr(qcm_module.sequencer1, connector1)
			curr_out1("I")

			sh.connect_input(module = qrm_module, sequencer = 0, input_index = 0, path = 0, resolution = time_per_point)

			""" 
			The QRM has a built-in amplifier that automatically amplifies any input signal by 6 dB. In the following, 
			we are simply offsetting that amplification. There's also a DC offset, which we account for here.
			
			"""
			input_gain = -6
			qrm_module.in0_gain(input_gain)
			qrm_module.in1_gain(input_gain)
			
			if abs(I_offset) > 0.02:
				qrm_module.in0_offset(-0.02)
			else:
				qrm_module.in0_offset(-I_offset)

			qrm_module.in1_offset(-Q_offset)

			# Then, we enable the sync protocol for all sequencers
			qcm_module.sequencer0.sync_en(True)
			qcm_module.sequencer1.sync_en(True)
			qrm_module.sequencer0.sync_en(True)

			# Arm the sequencers

			qcm_module.arm_sequencer(0)
			qcm_module.arm_sequencer(1)
			qrm_module.arm_sequencer(0)

			# Start loaded sequences and time total duration
			start = time.time()
			cluster.start_sequencer()

			# Convert time from seconds to nanoseconds and sleep
			seq_time /= 1e9
			time.sleep(seq_time)

			# Stop the sequencers
			qcm_module.stop_sequencer(0)
			qcm_module.stop_sequencer(1)
			qrm_module.stop_sequencer(0)

			duration = time.time() - start
			print(f"Acquisition Completed in: {duration*1e3} ms.")

			# This section constructs both axes for the heat map
			X, Y = np.meshgrid(output_0_list, output_1_list)

			print("\n=== DEBUG: meshgrid shapes ===")
			print("X shape:", X.shape, "Y shape:", Y.shape)
			print("grid size:", X.shape[0] * X.shape[1])

			# If plot, then create a heat map as a function of the voltages
			if plot:
			
				# print(np.shape(X))

			# This section retrieves the data from the acquisition

				qrm_module.get_acquisition_status(0) # Wait for the sequencer to stop with a timeout period of one minute.
				qrm_module.store_scope_acquisition(0, 'acq_0') # Move acquisition data from temporary memory to acquisition list.
				readout_data = qrm_module.get_acquisitions(0) # Get acquisition list from instrument.

				# Find the number of bins to determine what kind of acquistion we are doing.
				num_bins = len(readout_data['acq_0']['acquisition']['bins']['integration']['path0'])

				# print("number of bins: ",num_bins)

				if num_bins == 1:

					# print(f"resolution in plot_input: {resolution}")
					# If it is a single acquisition with resolution of 1 ns
					data0 = readout_data['acq_0']['acquisition']['scope']['path0']['data'] # Extract path 0 data
					data1 = readout_data['acq_0']['acquisition']['scope']['path1']['data'] # Extract path 1 data

				else:
					
					repeat_num = sh.make_input_sequence(input_seq_0, resolution = time_per_point)[1]

					# print(f"resolution in plot_input: {resolution}")

					data0 = np.array(readout_data['acq_0']['acquisition']['bins']['integration']['path0']) / time_per_point # Extract path 0 data
					data1 = np.array(readout_data['acq_0']['acquisition']['bins']['integration']['path1']) / time_per_point # Extract path 1 data
				
				print("\n=== DEBUG: raw acquisition shapes ===")
				print("data0 shape:", np.shape(data0))
				print("data1 shape:", np.shape(data1))
				print("num_steps_0:", num_steps_0, "num_steps_1:", num_steps_1)
				print("expected points:", num_steps_0 * num_steps_1)

				#print(data0)

				#print(data0.shape)

				# Now, we add this sweeps data to the data list

				dataI.append(data0)
				dataQ.append(data1)

			# Reset cluster connection

			cluster.reset()

		saved_csv_files = []

		for data0, data1 in zip(dataI,dataQ):
			print("\n=== DEBUG before reshape ===")
			print("data0 len:", len(data0))
			print("data1 len:", len(data1))
			print("expected total points (including repeats):", X.shape[0] * X.shape[1] * repeat_num)

			# Now, we have to reshape the data to be the same shape as X and Y
				
			data0_reshaped = data0.reshape(X.shape[0]*X.shape[1], repeat_num)

			avg_data0 = data0_reshaped.mean(axis=1) * repeat_num

			data0_final = avg_data0.reshape(X.shape[0],X.shape[1])

			if data1.shape[0] % (len(output_0_list)*len(output_1_list)) != 0:
				raise ValueError(f"The amount of data taken is unable to be shaped into {np.shape(Y)}")
				
			data1_reshaped = data1.reshape(Y.shape[0]*Y.shape[1], repeat_num)

			avg_data1 = data1_reshaped.mean(axis=1) * repeat_num

			data1_final = avg_data1.reshape(Y.shape[0],Y.shape[1])

			# Now, we save the data to a csv file

			save_data = []

			for i, y in enumerate(output_1_list):
				for j, x in enumerate(output_0_list):
					save_data.append([x, y, data0_final[i, j], data1_final[i, j]])
			
			print("\n=== DEBUG before saving CSV ===")
			print("number of rows saved:", len(save_data))
			print("expected rows:", X.shape[0] * X.shape[1])

			experiment_time = datetime.now()

			timestamp = experiment_time.strftime('%Y-%m-%d_%H-%M%S')
			
			filename = f"{timestamp}_2D_sweep_voltage_ranges_{voltage_ranges}_step_size{step_size}_time_per_point{time_per_point}_20us_time_constant.csv"

			full_path = os.path.join(self.save_path, filename)

			np.savetxt(full_path ,save_data, delimiter = ',', fmt = '%s', header = 'QCM Output 1, QCM Output 2, QRM Input Path Q, QRM Input Path I', comments = '')

			saved_csv_files.append(full_path)
			print(f"CSV file saved: {full_path}") 

			# TODO add error if data is not the same shape as X and Y

			np.set_printoptions(threshold=np.inf)

			# print(X)

			# Plot the heatmap(s)

			fig, ax = plt.subplots(1, 2, figsize = (15, 10))

			# Heatmap of Path 0

			# print(data0_final[-1][-1])
			# print(data0_final[-1][-2])
			data0_final[-1][-1] = data0_final[-1][-2]
			plot0 = ax[0].pcolormesh(X, Y, data0_final, cmap = 'viridis', shading = 'auto')
			fig.colorbar(plot0, ax = ax[0], label = 'Path 0')
			ax[0].set_title('Heatmap of Path 0')
			ax[0].set_xlabel('V1')
			ax[0].set_ylabel('V2')


			# Heatmap of Path 1

			plot1 = ax[1].pcolormesh(X, Y, data1_final, cmap = 'viridis', shading = 'auto')
			fig.colorbar(plot1, ax = ax[1], label = 'Path 1')
			ax[1].set_title('Heatmap of Path 1')
			ax[1].set_xlabel('V1')
			ax[1].set_ylabel('V2')

			
			filename = f"{timestamp}_2D_sweep.png"
			full_path = os.path.join(self.save_path, filename)

			plt.savefig(full_path,
			   dpi = 600
			   )
			print(f"໒(⊙ᴗ⊙)७✎▤: Plot has been saved at {full_path} ")
			plt.show()
				
		# print(qrm_module.get_sequencer_status(0))
		# print(qrm_module.get_acquisition_status(0))
		# print(qrm_module.get_assembler_status())
		
		print(saved_csv_files)

		if repeats > 1:
				self.average_results_csv_only(saved_csv_files)

		return None

	def run_square_pulse(self, 
						qcm_output_names: list[str],
						pulse_amplitudes: list[float],
						pulse_lengths: list[float],
						times_between_pulses: list[float],
						total_acquisition_time: list[int] = [0],
						acquisition_delay: list[float] = [0.0],
						acquisition_resolution: list[int] = [1],
						repeats: list[int] = [],    
						voltage_configuration: dict[str,tuple] = {},
						plot = False
						):

			"""
			This function allows the user to create a 1D Voltage Trace with minimal interaction with Q1ASM and the Qblox cluster.

			The function takes inputs:

			qcm_output_name:        The name of the DAC used for the trace
			pulse_amplitude:        The amplitude of the square pulse in volts
			pulse_length:           The length of the square pulse in nanoseconds
			time_between_pulses:    The set time between each square pulse, if repeats is non-zero
			acquisition_time:       Duration of acquisition
			acquisition_delay:      The amount of time before an acquisition starts in nanoseconds
			acquisition_resolution: The resolution of your acquisition. If duration is under 16 384 ns, highest resolution of 1 ns is used no matter what. If duration is longer then minimum resolution needed is 250ns
			repeats:                The number of times a sequence is repeated for each output
			voltage_configuration:  The DC voltages set by the Spi Rack before the sweep is performed
			plot:                   If True, run_2D_sweep() will create a heat map of the data; Set to False by default 
									and will output a voltage sweep through the qcm into a device, and will acquire a signal from the device, which will
									then be plotted against the input voltage. 								
			"""

			if self.config_data['MFLI']['connected']:
				self.setup_mfli(on=True, Amp_Vpk = 1.0, timeconstant = 20e-6)
			
			dataI = []

			# First, we obtain all the names for each dac if the Spi Rack is connected
			if self.config_data['spirack']['connected']:

				dac_name_list = list(self.dac_name_Mods_and_Dacs.keys())

				# Then, we determine which dacs are being set in the provided voltage configuration

				dacs_and_vals = []

				for name in dac_name_list:

					if name in voltage_configuration:
						
						dacs_and_vals.append((self.dac_name_Mods_and_Dacs.get(name), voltage_configuration.get(name)))

				# Now, we can set the voltage for each dac

				for info, val in dacs_and_vals:	

					curr_module = getattr(self.spirack, info[0])
					curr_dac = getattr(curr_module, info[1])

					curr_dac.voltage(val)
					while curr_dac.is_ramping():
						time.sleep(0.001)

					curr_voltage = curr_dac.voltage()
					print(f"{info[1]} of {info[0]} has been set to: {curr_voltage} V")

			if repeats == []:
				repeats = [1] * len(qcm_output_names)

			# First, we define an empty output seqeunce, as a dictionary. We also define our voltage sweep parameters

			output_sequences = {}

			for i in qcm_output_names:
				output_sequences[i] = []
			
			seq_time = 0

			input_seq = []

			# Here, we check that the lists of parameters have the same length and that no list has length greater than 4

			if len(qcm_output_names) != len(pulse_amplitudes) != len(pulse_lengths) != len(times_between_pulses) != len(repeats):
				raise ValueError("There is a mismatch in the number of parameters set in each category. Please ensure that lengths of lists for all names and pulse parameter numbers match")

			for i in [len(qcm_output_names), len(pulse_amplitudes), len(pulse_lengths), len(times_between_pulses), len(repeats)]:
				if i > 4:
					raise ValueError("One of the specified pulse parameters has more than 4 arguments. Please have a maximum of 4 arguments per parameter.")

			# Here, we check if the amplitude is above +-2.5 V and raise an error is true

			for i in pulse_amplitudes:
				if abs(i) > 2.5:
					raise ValueError("The pulse amplitude set is above 2.5 V. The QCM can only output +-2.5 V. Please input a valid pulse amplitude.")

			# Here, we check if the qrm inputs are given correctly

			if len(acquisition_delay) != len(acquisition_resolution) != len(total_acquisition_time):
				raise ValueError("There is a mismatch in the number of parameters set for acquisition. Please ensure that lengths of lists match")
			
			for i in [len(acquisition_delay), len(acquisition_resolution), len(total_acquisition_time)]:
				if i > 2:
					raise ValueError("One of the specified acquisition parameters has more than 2 arguments. Please have a maximum of 2 arguments per acquisition parameter.")
				
			# Here, we append the steps to the output_seq list
			
			for i, output_seq in enumerate(output_sequences.values()):

				output_seq.append(['square', pulse_lengths[i], pulse_amplitudes[i]])
				output_seq.append(['square', times_between_pulses[i], 0.0])
			
			print(output_sequences)

			seq_time += max(pulse_lengths)*max(repeats)
			seq_time += max(times_between_pulses)*max(repeats)

			# Now, we define our input sequence, if plotting has been enabled

			if plot:
				for i in range(len(acquisition_delay)):
					input_seq.append([f'acq_{i}', acquisition_delay[i], (total_acquisition_time[i])])

			# Now, we disconnect any prexsisting connections

			qcm_module = self.qcm_module
			qrm_module = self.qrm_module
			rf_module = self.rf_module
			cluster = self.cluster

			# TODO add error message if there are mislabeled modules

			sh.disconnect_io(qcm_module)
			sh.disconnect_io(qrm_module)
			sh.disconnect_io(rf_module)

			if plot:
				I_offset, Q_offset = sh.acquire_scope_and_calc_offsets(qrm_module)

				sh.disconnect_io(qcm_module)
				sh.disconnect_io(qrm_module)
				sh.disconnect_io(rf_module)

			# Now, we upload our sequences to the modules and specify sequencers		

			for i, output_seq in enumerate(output_sequences.values()):
				output_num = qcm_output_names[i].replace("O", "")
				qcm_curr_sequencer = getattr(qcm_module, "sequencer" + str(int(output_num) - 1))
				qcm_curr_sequence = getattr(qcm_curr_sequencer, "sequence")
				qcm_curr_sequence(sh.make_output_sequence(output_seq, module = "qcm", iterations = repeats[i]))

			if plot:
				for i in range(len(acquisition_delay)):
					qrm_curr_sequencer = getattr(qrm_module, "sequencer" + str(i))
					qrm_curr_sequence = getattr(qrm_curr_sequencer, "sequence")
					qrm_curr_sequence(sh.make_input_sequence(input_seq[i], resolution=acquisition_resolution[i])[0])
			
			rf_module.sequencer1.sequence(sh.marker_only_sequence())

			seq_time += 150 # Accounting for ToF

			# Now, we connect the modules to the sequencers

			for name in qcm_output_names:
				output_num = name.replace("O", "")
				curr_sequencer = getattr(qcm_module, "sequencer" + str(int(output_num) - 1))
				curr_out = getattr(curr_sequencer, "connect_out" + str(int(output_num) - 1))
				curr_out("I")

			if plot:
				for i in range(len(acquisition_delay)):
					sh.connect_input(module = qrm_module, sequencer = i, input_index = i, path = 0, resolution=acquisition_resolution[i])

			""" 
			The QRM has a built-in amplifier that automatically amplifies any imput signal by 6 dB. In the following, 
			we are simply offsetting that amplification. There's also a DC offset, which we account for here.
			
			"""

			if plot:
				input_gain = -6

				qrm_module.in0_gain(input_gain)
				qrm_module.in1_gain(input_gain)
				
				if abs(I_offset) > 0.02:
					qrm_module.in0_offset(-0.016)
				else:
					qrm_module.in0_offset(-I_offset)
				
				if abs(Q_offset) > 0.02:
					qrm_module.in1_offset(-0.016)
				else:
					qrm_module.in1_offset(-Q_offset)

				for i in range(len(acquisition_delay)):
					if i == 0:
						print(f"Current Gain: {qrm_module.in0_gain()} dB, Offset: {qrm_module.in0_offset()} V")
					if i == 1:
						print(f"Current Gain: {qrm_module.in1_gain()} dB, Offset: {qrm_module.in1_offset()} V")
			
			

			# Then, we enable the sync protocol for all sequencers

			for name in qcm_output_names:
				output_num = name.replace("O", "")
				curr_sequencer = getattr(qcm_module, "sequencer" + str(int(output_num) - 1))
				curr_sequencer.sync_en(True)
				print(qcm_module.get_sequencer_status(int(output_num) - 1))
			
			if plot:
				for i in range(len(acquisition_delay)):
					qrm_curr_sequencer = getattr(qrm_module, "sequencer" + str(i))
					qrm_curr_sequencer.sync_en(True)
					print(qrm_module.get_sequencer_status(i))
			
			rf_module.sequencer0.sync_en(True)

			# Here we arm the sequencers
			for name in qcm_output_names:
				output_num = name.replace("O", "")
				qcm_module.arm_sequencer(int(output_num) - 1)
				print(qcm_module.get_sequencer_status(int(output_num) - 1))

			if plot:
				for i in range(len(acquisition_delay)):
					qrm_module.arm_sequencer(i)
					print(qrm_module.get_sequencer_status(i))
			
			rf_module.arm_sequencer()

			# This next step runs the sequences we loaded

			cluster.start_sequencer()

			# Convert time from seconds to nanoseconds
		
			seq_time /= 1e9
		
			time.sleep(seq_time)

			# Then, we stop the sequencers

			for name in qcm_output_names:
				output_num = name.replace("O", "")
				qcm_module.stop_sequencer(int(output_num) - 1)
				print(qcm_module.get_sequencer_status(int(output_num) - 1))

			if plot:
				for i in range(len(acquisition_delay)):
					qrm_module.stop_sequencer(i)
					print(qrm_module.get_sequencer_status(i))
			
			rf_module.stop_sequencer()

			if self.config_data['MFLI']['connected']:
				self.zh_mfli.sigouts[0].on(0)
		
			# If plot is True, then this part of the method will create a heat map as a function of the voltages

			if plot:

				output_list = pulse_amplitudes
				output_list.append(0.0)

				X = np.asarray(output_list)

				for data0 in dataI:

					# Now, we have to reshape the data to be the same shape as X and Y

					if data0.shape[0] % (len(output_list)) != 0:
						raise ValueError(f"The amount of data taken is unable to be shaped into {np.shape(X)}")
						
					data0_reshaped = data0.reshape(X.shape[0], repeat_num)

					data0_final = data0_reshaped.mean(axis=1) * repeat_num

					#data0_final = avg_data0.reshape(X.shape[0],X.shape[1])

				experiment_time = datetime.now()
				timestamp = experiment_time.strftime('%Y-%m-%d_%H-%M%S')

				filename = f"{timestamp}Square_Pulse_{pulse_amplitudes}_{pulse_lengths}_{times_between_pulses}"

				for i in range(len(acquisition_delay)):

					sh.plot_input(module = qrm_module, sequencer = i, acquisition_name = f'acq_{i}', acquisition_time = total_acquisition_time, save_path = self.save_path, filename = filename + f"_in{i}")

					# This section retrieves the data from the acquisition

					qrm_module.get_acquisition_status(i) # Wait for the sequencer to stop with a timeout period of one minute.
					qrm_module.store_scope_acquisition(i, f'acq_{i}') # Move acquisition data from temporary memory to acquisition list.
					readout_data = qrm_module.get_acquisitions(i) # Get acquisition list from instrument.

					qrm_curr_res = getattr(qrm_module, "sequencer" + str(i))

					resolution = qrm_curr_res.integration_length_acq()

					# Find the number of bins to determine what kind of acquistion we are doing.
					num_bins = len(readout_data[f'acq_{i}']['acquisition']['bins']['integration']['path0'])
					# print("number of bins: ",num_bins)

					if num_bins == 1:

						# print(f"resolution in plot_input: {resolution}")
						# If it is a single acquisition with resolution of 1 ns
						data0 = readout_data[f'acq_{i}']['acquisition']['scope']['path0']['data'] # Extract path 0 data

					else:
						
						repeat_num = sh.make_input_sequence(input_seq[i], resolution = resolution)[1]

						# print(f"resolution in plot_input: {resolution}")

						data0 = np.array(readout_data[f'acq_{i}']['acquisition']['bins']['integration']['path0']) / resolution # Extract path 0 data #TODO Replace time per point

					#print(data0)

					#print(data0.shape)

					# Now, we add this sweeps data to the data list

					dataI.append(data0)

					'''if repeats > 1:
						self.average_results_csv_only(csv_file_number = repeats)'''

			# Resets the connection to the cluster. If not done, the offsets will be incorrect when playing another sequence.

			cluster.reset()

			if total_acquisition_time > 100e6:
				for i in range(10):

					time.sleep(5.0)

					cluster.reset()

			return None

	def run_pulse_program(self,
						  pulse_setup: dict[str, list],
						  total_acquisition_time: list[int] = [0],
						  acquisition_delay: list[float] = [0.0],
						  acquisition_resolution: list[int] = [1],
						  repeats: list[int] = [],    
						  voltage_configuration: dict[str,tuple] = {},
						  plot = False):
		
		"""
		This function allows the user to create a 1D Voltage Trace with minimal interaction with Q1ASM and the Qblox cluster.

		The function takes inputs:

		pulse_setup:            Format: {"O1": [["square", pulse_duration, pulse_amplitude], ["ramp", pulse_duration, start_amplitude, stop_amplitude, volt_step_size], ...],
										 "O2": [["square", pulse_duration, pulse_amplitude], ["ramp", pulse_duration, start_amplitude, stop_amplitude, volt_step_size], ...],
										  .
										  .
										}
								* Name of Output is "O1" through "O4".
									Pulse names can be "square" or "ramp".
									pulse_duration is in nanoseconds
									pulse_amplitude, start_amplitude, stop_amplitude, and volt_step_size is in volts
									Be sure to include 0 amplitude steps in between pulses and at the end
		acquisition_time:       The amount of time to acquire for in nanoseconds
		acquisition_delay:      The amount of time before an acquisition starts in nanoseconds
		acquisition_resolution: The resolution of your acquisition. If duration is under 16 384 ns, highest resolution of 1 ns is used no matter what. If duration is longer then minimum resolution needed is 250ns
		repeats:                The number of times a sequence is repeated
		voltage_configuration:  The DC voltages set by the Spi Rack before the sweep is performed
		plot:                   If True, run_2D_sweep() will create a heat map of the data; Set to False by default 
								and will output a voltage sweep through the qcm into a device, and will acquire a signal from the device, which will
								then be plotted against the input voltage. 				
		"""
		
		if self.config_data['MFLI']['connected']:
				self.setup_mfli(on=True, Amp_Vpk = 1.0, timeconstant = 20e-6)
			
		dataI = []

		# First, we obtain all the names for each dac if the Spi Rack is connected
		if self.config_data['spirack']['connected']:

			dac_name_list = list(self.dac_name_Mods_and_Dacs.keys())
			print(dac_name_list)

			# Then, we determine which dacs are being set in the provided voltage configuration

			dacs_and_vals = []

			for name in dac_name_list:

				if name in voltage_configuration:
					
					dacs_and_vals.append((self.dac_name_Mods_and_Dacs.get(name), voltage_configuration.get(name)))

			# Now, we can set the voltage for each dac

			for info, val in dacs_and_vals:	

				curr_module = getattr(self.spirack, info[0])
				curr_dac = getattr(curr_module, info[1])

				curr_dac.voltage(val)
				while curr_dac.is_ramping():
					time.sleep(0.001)

				curr_voltage = curr_dac.voltage()
				print(f"{info[1]} of {info[0]} has been set to: {curr_voltage} V")

		# First, we define our voltage sweep parameters
	
		seq_time = 0

		pulse_amplitudes = []
		input_seq = []

		# Create a list for 1 repeats for each outputs if argument is not given

		if repeats == []:
			repeats = [1] * len(pulse_setup)

		# Empty copy of pulse_setup.values()
		output_seqs_list = [[] for _ in range(len(pulse_setup))]
		qcm_output_names = list(pulse_setup.keys())

		# Here, we check if the qrm inputs are given correctly

		if len(acquisition_delay) != len(acquisition_resolution) != len(total_acquisition_time):
			raise ValueError("There is a mismatch in the number of parameters set for acquisition. Please ensure that lengths of lists match")
		
		for i in [len(acquisition_delay), len(acquisition_resolution), len(total_acquisition_time)]:
			if i > 2:
				raise ValueError("One of the specified acquisition parameters has more than 2 arguments. Please have a maximum of 2 arguments per acquisition parameter.")
			
		for i, output_seq in enumerate(pulse_setup.values()):
			
			for j, step in enumerate(output_seq):

				# Here, we check that the input lists of parameters have the same length and that no list has length not equal to 3 for square and 5 for ramp
				
				if step[0] == "square" and len(step) != 3:
					raise ValueError(f"More or less than 3 parameters for a square pulse were given in Step {j+1} for O{i+1}, check format in documentation")
				
				elif step[0] == "ramp" and len(step) != 5:
					raise ValueError(f"More or less than 5 parameters for a ramp pulse were given in Step {j+1} for O{i+1}, check format in documentation")
				
				# Here, we check if the amplitude is above +-2.5 V and raise an error if true

				if step[0] == "square" and abs(step[-1]) > 2.5:
					raise ValueError(f"The square pulse amplitude set in Step {j+1} for O{i+1} is above 2.5 V. The QCM can only output +-2.5 V. Please input a valid pulse amplitude.")
				
				elif step[0] == "ramp" and (abs(step[-1]) > 2.5 or abs(step[-2]) > 2.5 or abs(step[-3]) > 2.5):
					raise ValueError(f"The ramp pulse start, stop, or voltage step size set in Step {j+1} for O{i+1} is above 2.5 V. The QCM can only output +-2.5 V. Please input a valid pulse amplitude.")
				
				# Here, we check that the ramp has the correct sign on the step size

				if step[0] == "ramp":

					if step[2] > step[3]:
						if step[4] > 0:
							raise ValueError(f"A positive step size was given for a decreasing ramp in Step {j+1}. Please give a negative step size.")
						
					if step[2] < step[3]:
						if step[4] < 0:
							raise ValueError(f"A negative step size was given for a increasing ramp in Step {j+1}. Please give a positive step size.")
				
				# Here, we find the total duration for each step and add it to seq_time

				seq_time += step[1]

				# Here, we convert the ramps into a sequence of square pulses that increase in voltage

				if step[0] == "square":
					output_seqs_list[i].append(step)
					pulse_amplitudes.append(step[-1])

				elif step[0] == "ramp":
					output_list = np.arange(step[2], step[3] + step[4], step[4])
					time_per_point = step[1]/(len(output_list))
					
					if time_per_point < 10:
						time_per_point = 10
						step_size = abs(step[2] - step[3]) / ((step[1]/time_per_point) - 1)
						output_list = np.arange(step[2], step[3] + step_size, step_size)
						counter = 10
						while output_list[-1] != step[3]:
							step_size = round(abs(step[2] - step[3]) / ((step[1]/time_per_point) - 1), counter)
							output_list = np.arange(step[2], step[3] + step_size, step_size)
							counter -= 1
							if counter == 4:
								break

						print(f"WARNING: The voltage step size given for one of the ramps is too small. Voltage step size has been reconfigured to {step_size}V so that the amount of time per ramp step is 10 ns")
							
					for amp in output_list:
						output_seqs_list[i].append(["square", time_per_point, amp])
						pulse_amplitudes.append(amp)
					
		output_sequences = {}

		for i, key in enumerate(pulse_setup.keys()):
				output_sequences[key] = output_seqs_list[i]
		print(output_sequences)

		# Now, we define our input sequence, if plotting has been enabled

		if plot:
			for i in range(len(acquisition_delay)):
				input_seq.append([f'acq_{i}', acquisition_delay[i], (total_acquisition_time[i])])

		# Now, we disconnect any prexsisting connections

		qcm_module = self.qcm_module
		qrm_module = self.qrm_module
		rf_module = self.rf_module
		cluster = self.cluster

		# TODO add error message if there are mislabeled modules

		sh.disconnect_io(qcm_module)
		sh.disconnect_io(qrm_module)
		sh.disconnect_io(rf_module)

		if plot:
			I_offset, Q_offset = sh.acquire_scope_and_calc_offsets(qrm_module)

			sh.disconnect_io(qcm_module)
			sh.disconnect_io(qrm_module)
			sh.disconnect_io(rf_module)

		# Now, we upload our sequences to the modules and specify sequencers		

		for i, seqs in enumerate(output_sequences.values()):
			output_num = qcm_output_names[i].replace("O", "")
			qcm_curr_sequencer = getattr(qcm_module, "sequencer" + str(int(output_num) - 1))
			qcm_curr_sequence = getattr(qcm_curr_sequencer, "sequence")
			qcm_curr_sequence(sh.make_output_sequence(seqs, module = "qcm", iterations = repeats[i]))


		if plot:
			for i in range(len(acquisition_delay)):
				qrm_curr_sequencer = getattr(qrm_module, "sequencer" + str(i))
				qrm_curr_sequence = getattr(qrm_curr_sequencer, "sequence")
				qrm_curr_sequence(sh.make_input_sequence(input_seq[i], resolution=acquisition_resolution[i])[0])
		
		rf_module.sequencer0.sequence(sh.marker_only_sequence())

		seq_time += 150 # Accounting for ToF

		# Now, we connect the modules to the sequencers

		for name in output_sequences.keys():
			output_num = name.replace("O", "")
			curr_sequencer = getattr(qcm_module, "sequencer" + str(int(output_num) - 1))
			curr_out = getattr(curr_sequencer, "connect_out" + str(int(output_num) - 1))
			curr_out("I")

		if plot:
			for i in range(len(acquisition_delay)):
				sh.connect_input(module = qrm_module, sequencer = i, input_index = i, path = 0, resolution=acquisition_resolution[i])

		""" 
		The QRM has a built-in amplifier that automatically amplifies any imput signal by 6 dB. In the following, 
		we are simply offsetting that amplification. There's also a DC offset, which we account for here.
		
		"""

		if plot:
			input_gain = -6

			qrm_module.in0_gain(input_gain)
			qrm_module.in1_gain(input_gain)
			
			if abs(I_offset) > 0.02:
				qrm_module.in0_offset(-0.016)
			else:
				qrm_module.in0_offset(-I_offset)
			
			if abs(Q_offset) > 0.02:
				qrm_module.in1_offset(-0.016)
			else:
				qrm_module.in1_offset(-Q_offset)

			for i in range(len(acquisition_delay)):
				if i == 0:
					print(f"Current Gain: {qrm_module.in0_gain()} dB, Offset: {qrm_module.in0_offset()} V")
				if i == 1:
					print(f"Current Gain: {qrm_module.in1_gain()} dB, Offset: {qrm_module.in1_offset()} V")

		# Then, we enable the sync protocol for all sequencers

		for name in output_sequences.keys():
			output_num = name.replace("O", "")
			curr_sequencer = getattr(qcm_module, "sequencer" + str(int(output_num) - 1))
			curr_sequencer.sync_en(True)
			print(qcm_module.get_sequencer_status(int(output_num) - 1))

		if plot:
			for i in range(len(acquisition_delay)):
				qrm_curr_sequencer = getattr(qrm_module, "sequencer" + str(i))
				qrm_curr_sequencer.sync_en(True)
				print(qrm_module.get_sequencer_status(i))

		
		#rf_module.sequencer0.sync_en(True)

		# Here we arm the sequencers

		for name in output_sequences.keys():
			output_num = name.replace("O", "")
			qcm_module.arm_sequencer(int(output_num) - 1)
			print(qcm_module.get_sequencer_status(int(output_num)))

		if plot:
			for i in range(len(acquisition_delay)):
				qrm_module.arm_sequencer(i)
				print(qrm_module.get_sequencer_status(i))
		
		rf_module.arm_sequencer()

		# This next step runs the sequences we loaded

		cluster.start_sequencer()

		# Convert time from seconds to nanoseconds

		seq_time /= 1e9

		time.sleep(seq_time)

		# Then, we stop the sequencers

		for name in output_sequences.keys():
			output_num = name.replace("O", "")
			qcm_module.stop_sequencer(int(output_num) - 1)
			print(qcm_module.get_sequencer_status(int(output_num) - 1))

		if plot:
			for i in range(len(acquisition_delay)):
				qrm_module.stop_sequencer(i)
				print(qrm_module.get_sequencer_status(i))
		
		rf_module.stop_sequencer()

		if self.config_data['MFLI']['connected']:
			self.zh_mfli.sigouts[0].on(0)
	
		# If plot is True, then this part of the method will create a heat map as a function of the voltages

		if plot:

			output_list = pulse_amplitudes
			output_list.append(0.0)

			X = np.asarray(output_list)

			for data0 in dataI:

				# Now, we have to reshape the data to be the same shape as X and Y

				if data0.shape[0] % (len(output_list)) != 0:
					raise ValueError(f"The amount of data taken is unable to be shaped into {np.shape(X)}")
					
				data0_reshaped = data0.reshape(X.shape[0], repeat_num)

				data0_final = data0_reshaped.mean(axis=1) * repeat_num

				#data0_final = avg_data0.reshape(X.shape[0],X.shape[1])

			experiment_time = datetime.now()
			timestamp = experiment_time.strftime('%Y-%m-%d_%H-%M%S')

			filename = f"{timestamp}Square_Pulse_Program"
			
			for i in range(len(acquisition_delay)):
				sh.plot_input(module = qrm_module, sequencer = i, acquisition_name = f'acq_{i}', acquisition_time = total_acquisition_time, save_path = self.save_path, filename = filename + f"_in{i}")
			
				# This section retrieves the data from the acquisition

				qrm_module.get_acquisition_status(i) # Wait for the sequencer to stop with a timeout period of one minute.
				qrm_module.store_scope_acquisition(i, f'acq_{i}') # Move acquisition data from temporary memory to acquisition list.
				readout_data = qrm_module.get_acquisitions(i) # Get acquisition list from instrument.

				qrm_curr_res = getattr(qrm_module, "sequencer" + str(i))

				resolution = qrm_curr_res.integration_length_acq()

				# Find the number of bins to determine what kind of acquistion we are doing.
				num_bins = len(readout_data[f'acq_{i}']['acquisition']['bins']['integration']['path0'])

				# print("number of bins: ",num_bins)

				if num_bins == 1:

					# print(f"resolution in plot_input: {resolution}")
					# If it is a single acquisition with resolution of 1 ns
					data0 = readout_data[f'acq_{i}']['acquisition']['scope']['path0']['data'] # Extract path 0 data

				else:
					
					repeat_num = sh.make_input_sequence(input_seq, resolution = resolution)[1] #TODO Replace time per point

					# print(f"resolution in plot_input: {resolution}")

					data0 = np.array(readout_data[f'acq_{i}']['acquisition']['bins']['integration']['path0']) / resolution # Extract path 0 data #TODO Replace time per point

				#print(data0)

				#print(data0.shape)

				# Now, we add this sweeps data to the data list

				dataI.append(data0)

				'''if repeats > 1:
					self.average_results_csv_only(csv_file_number = repeats)'''
		
		# Resets the connection to the cluster. If not done, the offsets will be incorrect when playing another sequence.

		cluster.reset()
		
		if total_acquisition_time > 100e6:
			for i in range(10):

				time.sleep(5.0)

				cluster.reset()

		return None

	def run_pulse_program_csv(self,
							  csv_file: str):
		
		'''
		This function allows the user to create a 1D Voltage Trace with minimal interaction with Q1ASM and the Qblox cluster.
		Takes a csv file as an input and converts it into readable QBlox code. Minimum time step is 10ns.
		Please remember that the csv file needs to end with a 0V after the final time step is given.

		The function takes inputs:

		csv_file: string of the csv file name (include entire directory if file isn't in the same folder as this python)
				  * CSV file must be in the format:
				  	time (ns) | O1 voltage (V) | O2 voltage (V) | O3 Voltage (V) | O4 Voltage (V)
				 	__________|________________|________________|________________|_______________
					          |                |                |                |               
					          |                |                |                |               
					          |                |                |                |        
		'''
		
		# Reads CSV file given 
		
		df = pd.read_csv(csv_file, delimiter=",")
		
		# Separates the Time and Voltage data
		
		t = df[df.columns[0]]
		voltages = df[df.columns[1:]]

		# Checks if the number of voltages columns are between 1 and 4 and checks if all coumns have the same number of rows

		if len(voltages.columns) < 1 or len(voltages.columns) > 4:
			raise ValueError("The csv provided doesn't have the required amount of voltage columns (between 1 and 4)")

		for i in range(len(voltages.columns)):
			col = voltages[voltages.columns[i]]
			if len(t) != len(col):
				raise ValueError("The number of rows in each column are not the same. Please check your csv file and ensure that the number of rows are the same for each and every column!")
		
		# Initializes a dictionary to pass into the run_square_program function after being populated with CSV data

		qcm_output_names = ["O1", "O2", "O3", "O4"]
		qcm_output_names = qcm_output_names[:len(voltages.columns)]
		
		output_sequences = {}

		for i in qcm_output_names:
			output_sequences[i] = []

		# Convert CSV data into readable QBlox code structure

		for i, output_seq in enumerate(output_sequences.values()):
			voltage_col = voltages[voltages.columns[i]]

			for row in range(1, len(voltage_col) - 1):
				output_seq.append(["square", t[row+1] - t[row], voltage_col[row]])

		# Run the QBlox code through the run_square_program function

		self.run_pulse_program(output_sequences)
		return None

	def aquire_data(self, acq_sequencer: int, acquisition_name: str, acquisition_length: int, acquisition_path: list, acquisition_delay = 0, resolution = 300, save_path = r'C:\\Users\\coher\\Desktop', plot = False):

	
		# Before anything, we have to set up the data variable to hold the data from each repeat

		dataI = []
		dataQ = []

		# Now, we define our input sequence.

		input_seq_0 = [acquisition_name, acquisition_delay, acquisition_length]

		# Now, we disconnect any prexsisting connections

		qrm_module = self.qrm_module
		cluster = self.cluster

		# TODO add error message if there are mislabeled modules

		sh.disconnect_io(qrm_module)


		I_offset, Q_offset = sh.acquire_scope_and_calc_offsets(qrm_module)

		sh.disconnect_io(qrm_module)

		
		# Now, we upload our sequences to the modules and specify sequencers		

		eval(f"qrm_module.sequencer{acq_sequencer}.sequence(sh.make_input_sequence({input_seq_0}, resolution = {resolution})[0])")
		
		for path in acquisition_path:
			if path == "I1":
				sh.connect_input(module = qrm_module, sequencer = acq_sequencer, input_index = 0, path = 0, resolution = resolution)
			elif path == "I2":
				sh.connect_input(module = qrm_module, sequencer = acq_sequencer, input_index = 1, path = 1, resolution = resolution)
			else:
				raise ValueError(f"Invalid aquisition_path: {path}. Input can either be I1 or I2")
					
		""" 
		The QRM has a built-in amplifier that automatically amplifies any imput signal by 6 dB. In the following, 
		we are simply offsetting that amplification. There's also a DC offset, which we account for here.
		
		"""
		input_gain = -6

		qrm_module.in0_gain(input_gain)
		qrm_module.in1_gain(input_gain)
		
		if abs(I_offset) > 0.004:
			qrm_module.in0_offset(-0.004)
		else:
			qrm_module.in0_offset(-I_offset)

		if abs(Q_offset) > 0.001:
			qrm_module.in1_offset(-0.001)
		else:
			qrm_module.in1_offset(-Q_offset)

		# Then, we enable the sync protocol for all sequencers
		eval(f"qrm_module.sequencer{acq_sequencer}.sync_en(True)")
		

		# Here we arm the sequencers

		qrm_module.arm_sequencer(acq_sequencer)

		# This next step runs the sequences we loaded

		cluster.start_sequencer()

		# Convert time from seconds to nanoseconds
		seq_time = acquisition_length
		seq_time /= 1e9

		#time.sleep(seq_time)

		# Then, we stop the sequencers

		qrm_module.stop_sequencer(acq_sequencer)

		# If plot is True, then this part of the method will create a heat map as a function of the voltages

		if plot:
			
			sh.plot_input(module = qrm_module, sequencer = acq_sequencer, acquisition_name = acquisition_name, save_path = self.save_path, filename = f"{timestamp}_averaging_test")

			# This section retrieves the data from the acquisition

		qrm_module.get_acquisition_status(acq_sequencer) # Wait for the sequencer to stop with a timeout period of one minute.
		qrm_module.store_scope_acquisition(acq_sequencer, acquisition_name) # Move acquisition data from temporary memory to acquisition list.
		readout_data = qrm_module.get_acquisitions(acq_sequencer) # Get acquisition list from instrument.

		if acq_sequencer == 0:
			resolution = qrm_module.sequencer0.integration_length_acq()
		elif acq_sequencer == 1:
			resolution = qrm_module.sequencer1.integration_length_acq()
		elif acq_sequencer == 2:
			resolution = qrm_module.sequencer2.integration_length_acq()
		elif acq_sequencer == 3:
			resolution = qrm_module.sequencer3.integration_length_acq()
		elif acq_sequencer == 4:
			resolution = qrm_module.sequencer4.integration_length_acq()
		elif acq_sequencer == 5:
			resolution = qrm_module.sequencer5.integration_length_acq()
		else:
			print("ERROR: sequencer index invalid")
			return None
		
		save_data = []
		data_header = ""

		for path in acquisition_path:

			if (path == "I1") or (path == "I2"):

				if path == "I1":
					path_name = 'path0'
				elif path == "I2":
					path_name = 'path1'

		# Find the number of bins to determine what kind of acquistion we are doing.
				num_bins = len(readout_data[acquisition_name]['acquisition']['bins']['integration'][path_name])

				# print("number of bins: ",num_bins)

				if num_bins == 1:

					# print(f"resolution in plot_input: {resolution}")
					# If it is a single acquisition with resolution of 1 ns
					data = readout_data[acquisition_name]['acquisition']['scope'][path_name]['data'] # Extract path 0 data

				else:
					
					repeat_num = sh.make_input_sequence(input_seq_0, resolution = resolution)[1]

					# print(f"resolution in plot_input: {resolution}")

					data = np.array(readout_data[acquisition_name]['acquisition']['bins']['integration'][path_name]) / resolution # Extract path 0 data

				#print(data0)

				#print(data0.shape)

				# Now, we add this sweeps data to the data list
				print(data)
				if path == "I1":
					dataI.append(data)
					save_data.append(data)
					data_header += "QRM I1, "
				elif path == "I2":
					dataQ.append(data)
					save_data.append(data)
					data_header += "QRM I2, "
			
			else:
				raise ValueError(f"Invalid aquisition_path: {path}. Input can either be I1 or I2")
			
		# self.dataI = dataI
		# self.dataQ = dataQ
					
					

		# Resets the connection to the cluster. If not done, the offsets will be incorrect when playing another sequence.

		cluster.reset()

		experiment_time = datetime.now()

		timestamp = experiment_time.strftime('%Y-%m-%d_%H-%M%S')
		
		filename = f"Qblox_Measurement_{timestamp}.csv"

		full_path = os.path.join(save_path, filename)

		np.savetxt(full_path ,save_data, delimiter = '\n', fmt = '%s', header = data_header, comments = '')

	

		return None
	
	def calibration(self):
		
		qrm_module = self.qrm_module

		I_offset, Q_offset = sh.acquire_scope_and_calc_offsets(qrm_module)

		if abs(I_offset) > 0.004:
			qrm_module.in0_offset(-0.004)
		else:
			qrm_module.in0_offset(-I_offset)

		if abs(Q_offset) > 0.001:
			qrm_module.in1_offset(-0.001)
		else:
			qrm_module.in1_offset(-Q_offset)

		return print("Calibration Completed ¯\_(ツ)_/¯  good luck")

	def SetSpiRACKVoltage(self, voltage_configuration:dict[str,tuple]):
		
		"""
		Sets the voltage of a specific DAC on the spirack.

		Function Inputs:
		voltage_configuration:  Dictionary containing the dac name as stated in the config.yaml file as the key and the voltage you want to set as the value
		"""
		
		# First, we obtain all the names for each dac if the Spi Rack is connected
		if self.config_data['spirack']['connected']:

			dac_name_list = list(self.dac_name_Mods_and_Dacs.keys())

			# Then, we determine which dacs are being set in the provided voltage configuration

			dacs_and_vals = []

			for name in dac_name_list:

				if name in voltage_configuration:
					
					dacs_and_vals.append((self.dac_name_Mods_and_Dacs.get(name), voltage_configuration.get(name)))

			# Now, we can set the voltage for each dac

			for info, val in dacs_and_vals:	

				curr_module = getattr(self.spirack, info[0])
				curr_dac = getattr(curr_module, info[1])

				curr_dac.voltage(val)
				while curr_dac.is_ramping():
					time.sleep(0.001)

				curr_voltage = curr_dac.voltage()
				print(f"{info[1]} of {info[0]} has been set to: {curr_voltage} V")

	def ClusterReset(self):

		'''
		Resets the QBlox Cluster
		'''

		self.cluster.reset()
