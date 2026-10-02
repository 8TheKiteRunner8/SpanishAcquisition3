"""
seqeuence_helper.py		V 3.0

This is a library of useful functions to make Qblox sequencing faster and more user-friendly.
See sequence_helper_tutorial.ipynb to learn how to use this library.

Created: 		Apr 27, 2026
Last updated: 	Apr 27, 2026
Tested:			Apr 27, 2026
				On firmware:			2.0.0
				On qblox-instruments:	1.2.1	

Author: Kyle MacRobbie

Edits: Made by Ben Van Osch, Rishabh Iyer, Zeyu Lu, and Dhruv Shah detailed below

1. The first edit was to the make_output_sequence() function, to change condition for using the awg offset 
   for square pulses rather than using the play command. This edit was made because to make a sequence of 
   square pulses that are longer than 65535 ns, you cannot use the upd_param command which has a limit at 
   65535 ns. Previously, make_output_sequence() only decided to switch to waveforms if the length of ramps 
   was > 16384 ns, but we also needed it to switch if blocks are longer than 16384 ns.

2. The second edit was to introduce a plot_input_multi() function. This is useful if the user wishes to, say,
   perform a 2D voltage sweep and check if the outputs are what the user expects, without having to write 
   multiple lines for each output. In general, this function will output plots for each sequencer input against time.

3. The third edit was an addition of some error messages to ensure that users aren't inputting mismatching parameters.

4. The fourth edit is an attempt to replace the make_output_sequence() function with one that only uses square pulses.
   The reason for this is, for long enough pulses, squares seems to have a better resolution than ramps. Currently,
   it seems like using set_awg_offs is a more reliable method than using ramps, so long as you have enough space for
   wait/upd_param commands in your Q1ASM program.

5. The fifth edit is to add an input offset function that Luke had initially created. The QRM, on top of having a
   built-in amplifier that we must account for, also has an offset due to ADC thermal effects. This function allows
   the user to quickly calculate the offset and adjust for it in their code.

6. Modified make_rf_sequence and connect_rf_output to set NCO frequency in Q1ASM and LO in QCoDeS. 

7. Added rf_sweep functionality to make_rf_sequence.  

8. Updated functions to have generalized functionality on all instruments for both control and readout.

"""

# Imports
import time
import json
import scipy
import math
import matplotlib.pyplot as plt
from typing import TYPE_CHECKING, Callable
from qcodes.instrument import find_or_create_instrument
from qblox_instruments import Cluster, ClusterType
import numpy as np
import inspect
import os

def make_waveforms(specs:dict):
	"""
	Function that takes a dictionary of values with the parameters
	of a waveform, and returns a dictionary with the requested data
	
	specs will have the form:
	{
	'name': [type, length, magnitude, index], 
	}

	or for a ramp:
	{
	'name': [type, length, initial voltage, final voltage, index],
	}
	
	Supported types are:
		- 'block'
		- 'gaussian'
		- 'sine'
		- 'cosine'
		- 'ramp'
	"""

	output = {}   # Creating a dictionary to store the waveforms in

	# Loop through each instruction and create the corresponding waveform, then add that waveform to the output dictionary
	for name in specs:
		data = []

		if specs[name][0] == 'block':
			data = [specs[name][2] for i in range(0, specs[name][1])]

		elif specs[name][0] == 'gaussian':
			data = scipy.signal.windows.gaussian(specs[name][1], std=0.12 * specs[name][1]).tolist()

		elif specs[name][0] == 'sine':
			data = [math.sin((2 * math.pi / specs[name][1]) * i) for i in range(0, specs[name][1])]

		elif specs[name][0] == 'cosine':
			data = [math.cos((2 * math.pi / specs[name][1]) * i) for i in range(0, specs[name][1])]

		elif specs[name][0] == 'ramp':
			data = [(((specs[name][3] - specs[name][2]) / (specs[name][1])) * i) + specs[name][2] for i in range(0, specs[name][1])]

		else:
			print(f"ERROR: waveform type '{specs[name][0]}' is not supported by make_waveforms().\nSee the docstring at the start of the function for supported waveforms")
			data = None


		output[name] = {} # Add a new entry to the waveform dictionary
		output[name]['data'] = data	# Add the data to this new entry
		output[name]['index'] = int(specs[name][len(specs[name]) - 1]) # Add the index to this new entry

	return output

def plot_waveforms(waveforms):
	"""Function that takes a waveform dictionary
	and plots the waveforms"""

	# Generate the plot
	fig, ax = plt.subplots(1, 1, figsize = (14, 4))

	# Loop through each waveform given and plot it with the given label
	for wave in waveforms:
		ax.plot(waveforms[wave]['data'], label = wave)

	# Set titles
	ax.set_title("Waveforms")
	ax.set_xlabel("Time [ns]")
	ax.set_ylabel("Amplitude [a.u.]")
	
	# Add a grid to the plot
	ax.grid(ls = '--')

	# Display a legend
	plt.legend()

	plt.show()
	return None

def make_output_sequence(input:list, module:str, iterations:int = 1, plot = False):
	
	"""
	Function that generates a Q1ASM sequence for a series of gate 
	voltages and ramps based on the input. THIS IS A SQUARE VERSION ONLY.
	
	The function also specifies whether the gates are being controlled
	by the QRM or QCM. The 'module' arguement can either be
		- 'qrm'
		- 'qcm'
	
	The input is a list of the following form:

		['type', 'length', 'magnitude']
	
	The input list should be in the order in which the voltages should be executed.
	"""

	# This will extract the line of code in the python notebook that called this function
	# By the naming convention of the modules, the first three letters of that line will be either "qrm" or "qcm" from the name of the module.
	
	interpreted_module = str(inspect.getframeinfo(inspect.currentframe().f_back)[3][0])
	i = 0
	for char in interpreted_module:
		if char != '	':
			break
		else:
			i += 1
	
	# i will be the index of the first non-tab character. This will take any indentatoin into account
	
	interpreted_module = interpreted_module[i:i+3]

	# Check to see if the first three letters of that line are indeed the names of one of the modules. 
	# If not, it is most likely that the naming convention has not been followed.
	
	if interpreted_module != 'qcm' and interpreted_module != 'qrm':
		print(f"ERROR: module name not recognized ({interpreted_module}). Please name your modules one of the following:\n\t- qrm_module\n\t- qcm_module\n\nIf you are trying to sequence an RF module, please use make_rf_sequence() instead of make_output_sequence().")
		return None

	module_range = 0		# Just setting up this variable
	awg_offs_range = 32767	# The range of values that can be passed into the Q1ASM command set_awg_offs

	# Determine what module is being used, make sure the module given to the function and the previously extracted module name correspond to the same module
	# This is a precaution since if you accidentally pass "qrm" to the function while using the QCM, you will get 5x the voltage in the output
	# Since the QCM has 5x the range as the QRM. So this double-checking prevents that from happening.
	
	if module == "qcm" and interpreted_module == "qcm":
		module_range = 2.5
	elif module == "qrm" and interpreted_module == "qrm":
		module_range = 0.5
	elif module == "qrm" and interpreted_module == "qcm":
		print("ERROR: module name and module passed into make_output_sequence() do not match.")
		return None
	elif module == "qcm" and interpreted_module == "qrm":
		print("ERROR: module name and module passed into make_output_sequence() do not match.")
		return None
	else:
		print("ERROR: module type not supported by make_sequence()\nSee the docstring at the start of the function for supported module types.")
		return None
	
	square_len = []
	for step in input:
		if step[0] == 'square':
			square_len.append(step[1])
		else:
			square_len.append(0)

	square_len_max = max(square_len)

	if square_len_max <= 65535:

		# Initializing our sequence string variable
		sequence = f""""""

		sequence += """\n	wait_sync	4"""

		# Check to see if we need to add looping
		if iterations > 1:
			sequence += f"""	\n 	move	{int(iterations)},R0\n	loop:"""
		

		# Looping through the input list, adding to the sequence string and waveforms dictionary as needed.
		temp_str = """"""

		for step in input:
			# Check what type of step it is (square or ramp).
			step_type = step[0]
			step_len = int(step[1])

			if step_type == "square":
				offset = step[2]
				offset_q1 = round((offset/module_range)*awg_offs_range) # Convert the offset to the Q1ASM value
				temp_str = f"""\n	set_awg_offs	{offset_q1},{offset_q1}\n	upd_param	{step_len}""" # Add the step to our sequence string

			else:
				print("ERROR: step type not supported by make_sequence().\nSupported step types are 'square'")

			sequence += temp_str

		waveforms = {}

		# Check to see if we need to add looping
		if iterations > 1:
			sequence += f"""\n	loop	R0,@loop"""

		# End by resetting the offset to 0 and stopping the sequence
		sequence += """\n	set_awg_offs	0,0\n	upd_param	4\n	stop"""

	# If the amount of ramping is greater than 16384 ns, we cannot store all of the ramping in waveforms.
	# So we will have to create ramps by stepping the voltage with some resolution, that will be determined by how many steps we need
	# and how many commands we are allowed to pass into a sequence.
	
	else:

		# Before defining a resolution, we need to find the square steps to see how many commands we can use to make our ramps.
		# Since upd_param has a maximum wait time of 65535, if the square pulse is longer than that, we need to use multiple upd_param values.
		
		num_squares = 0
		for step in input:
			if step[0] == 'square':	# if this is a square pulse
				num_commands = math.ceil(step[1]/65535) # The number of steps that will be necessary to make this square pulse
				num_squares += num_commands
			else:
				print("Input is not correct! Each voltage step should be a square.")
				return None

		# print(f"The number of square commands in the sequence is: {num_squares}")

		# Initializing out sequence string
		
		sequence_play = """"""

		# Checking to see if we need to have multiple plays of the same sequence
		
		if iterations > 1:
			sequence_play += f"""\n			move	{int(iterations)},R0\n	loop:"""

		# Loop through each step and add its commands to the sequence string
		
		for step in input:

			# If it is a square pulse
			
			if step[0] == 'square':
				voltage = step[2]
				offset_q1 = round((voltage / module_range) * awg_offs_range) # Convert the offset to the Q1ASM value
				
				if offset_q1 < 0:
					temp_string_1 = str(offset_q1)
					temp_string_2 = temp_string_1[1:]
					string = 'a' + temp_string_2

				else:
					string = str(offset_q1)

				# If the square pulse is longer than 65535 ns, we need to include loops
				
				repeat_num = math.ceil(step[1]/65535)

				sequence_play += f"""\n	move				{repeat_num},R1\n	offset{string}loop:"""
				
				sequence_play += f"""\n		set_awg_offs		{offset_q1},{offset_q1}\n		upd_param		{int(step[1]/repeat_num)}\n		loop		R1,@offset{string}loop"""

			else:
				print("Input is not correct! Each voltage step should be a square.")
				return None

		# Adding the end of the loop
		
		if iterations > 1:
			sequence_play += f"""\n	loop	R0,@loop"""

		# Create the whole sequence be beginning with syncing, and ending with resetting the offset to 0 and stopping
		
		sequence = """\n	wait_sync	4""" + sequence_play + f"""\n	set_awg_offs	0,0\n	upd_param	4\n	stop"""
		waveforms = {}

	# Create the sequence dictionary to be passed to the sequencer.
	
	sequence_dict = {
		"waveforms": waveforms,
		"weights": {},
		"acquisitions": {},
		"program": sequence
	}

	print(sequence) # uncomment to have the function print the sequence; used for sanity checks

	return sequence_dict

def make_rf_sequence(input:list, marker = None, iterations = 1):
	"""Function that generates a Q1ASM for rf pulses.
	
	The input list will take the form:

		For wait commands
		[
			['type', 'length']
		]
	
		For square pulses
		[
			['type', 'length', 'magnitude', 'frequency']
		]
		
		For RF Sweeps
		
		[
            ['type', 'length', 'magnitude', 'start freq', 'stop freq', 'steps']
		]

		Supported types:
			- 'rf_sweep'
			- 'square'
			- 'wait'
	
	The input list should be in the order in which the voltages should be executed.
	"""

	# Initialize our sequence string
	seq = f""""""

	# Check if we need to loop:
	if iterations > 1:
		seq += f"""	move	{iterations},R0\n	loop:"""

	# Sync with other sequencers
	seq += f"""\n	wait_sync	4"""

	# Loop through the steps and add their commands to the sequence string
	for step in input:
		# Wait commands
		if step[0] == 'wait':
			seq += f"""\n	set_awg_offs	0,0"""	# Set the voltage to 0
			# Since the maximum wait time for an upd_param command is 65535 ns, we will need multiple commands if there are longer waits than 65535 ns.
			num_full_waits = math.floor(step[1]/65535)	# Number of full wait commands
			remainder = step[1]%65535	# Remaining wait time
			# Adding the wait time to the sequence string
			for i in range(num_full_waits):
				seq += f"""\n	upd_param	65535"""
			if remainder != 0:
				seq += f"""\n	upd_param	{remainder}"""

		# Square pulses
		elif step[0] == 'square':
			# Set the marker to the appropriate value
			if marker == 0:
				seq += f"""\n	set_mrk	{0b1001}"""
			elif marker == 1:
				seq += f"""\n	set_mrk	{0b0110}"""
			elif marker == 'both':
				seq += f"""\n	set_mrk {0b1111}"""
			elif marker == None:
				None
			else:
				print("ERROR: marker type not supported. Supported arguements are:\n- 0\n- 1\n- 'both'")
			
			awg_offs_range = 32767	# The range of values that can be passed into the Q1ASM command set_awg_offs
			offset = step[2]	# Voltage to set the offset to
			frequency = int(step[3])
			offset_q1 = round((offset/1)*awg_offs_range) # Convert the offset to the Q1ASM value
			frequency_q1 = frequency*4
			seq += f"""\n	set_awg_offs	{offset_q1},{offset_q1}""" # Set the offset
			seq += f"""\n	set_freq	    {frequency_q1}"""
            # Since the maximum wait time for an upd_param command is 65535 ns, we will need multiple commands if there are longer pulses than 65535 ns.
			num_full_waits = math.floor(step[1]/65535) # Number of full waits
			remainder = step[1]%65535	# Remaining wait time
			# Adding wait time to the sequence string
			for i in range(num_full_waits):
				seq += f"""\n	upd_param	65535"""
			if remainder != 0:
				seq += f"""\n	upd_param	{remainder}"""
			seq += f"""\n	set_mrk	0"""

        # RF Sweeps
		elif step[0] == 'rf_sweep':
			# Set the marker to the appropriate value
			if marker == 0:
				seq += f"""\n   set_mrk {0b1001}"""
			elif marker == 1:
				seq += f"""\n	set_mrk	{0b0110}"""
			elif marker == 'both':
				seq += f"""\n	set_mrk {0b1111}"""
			elif marker == None:
				None
			else:
				print("ERROR: marker type not supported. Supported arguements are:\n- 0\n- 1\n- 'both'")
			
			# Set the offsets
			awg_offs_range = 32767
			offset = step[2]

			# Set the frequencies
			frequency_start = int(step[3])
			frequency_stop = int(step[4])
			frequency_steps = int(step[5])

			# Set the mapping to Q1ASM
			offset_q1 = round((offset/1)*awg_offs_range) 
			frequency_start_q1 = frequency_start*4
			frequency_inc_q1 = (int((frequency_stop-frequency_start)/(frequency_steps-1)))*4

			# Q1ASM Code
			seq += f""" \n	set_awg_offs {offset_q1},{offset_q1}""" 
			
			seq += f""" \n   move {frequency_steps},R2	# Loop index
                        \n nop
                        \n move {frequency_start_q1},R3	# NCO frequency
						\n nop"""
			seq += f""" \n   loopnco: set_freq R3"""

			# Since the maximum wait time for an upd_param command is 65535 ns, we will need multiple commands if there are longer pulses than 65535 ns.
			num_full_waits = math.floor(step[1]/65535) # Number of full waits
			remainder = step[1]%65535	# Remaining wait time

			# Adding wait time to the sequence string
			for i in range(num_full_waits):
				seq += f"""  \n upd_param 65535"""
			if remainder != 0:
				seq += f""" \n  upd_param {remainder}"""
			seq += f"""\n   add R3,{frequency_inc_q1},R3	# Increment the NCO frequency """
			seq += f"""\n   loop R2,@loopnco"""
			seq += f"""\n   set_mrk	0"""

		else:
			print("ERROR: module type not supported by make_sequence()\nSee the docstring at the start of the function for supported module types.")
			return None
	
	# Check if we need to loop:
	if iterations > 1:
		seq += f"""\n   loop R0,@loop"""

	# Set the offset voltage to 0, turn off the marker and stop the sequencer
	seq += f"""\n   set_mrk	{0b0000}\n  set_awg_offs 0,0\n	upd_param 4\n	stop"""

	# Adding all information to a sequence dictionary to be passed to the sequencer
	sequence_dict = {
		"waveforms": {},
		"weights": {},
		"acquisitions": {},
		"program": seq
	}

	return sequence_dict

def connect_rf_output(sequencer:int, output_index:int, lo_freq:int):
	"""Function that makes a connection to the QCM-RF and sets the NCO and LO frequencies
	to the specified values"""

	# Using eval() allows us to instert the variables into the commands we are trying to play
	eval(f"rf_module.sequencer{sequencer}.connect_out{output_index}(True)") # Connect to the selected output
	eval(f"rf_module.sequencer{sequencer}.mod_en_awg(True)") # Enable modulation of the NCO
	eval(f"rf_module.out{output_index}_lo_en(True)") # Enable modulation of the LO
	eval(f"rf_module.out{output_index}_lo_freq({lo_freq})") # Set LO frequency
	eval(f"rf_module.out0_offset_path0(0)")
	eval(f"rf_module.out0_offset_path1(0)")
	eval(f"rf_module.out1_offset_path0(0)")
	eval(f"rf_module.out1_offset_path0(0)")
	eval(f"rf_module.sequencer{sequencer}.sync_en(True)") # Enable syncing to other sequencers
	return None

def make_input_sequence(input:list, iterations:int = 1, resolution = 300):
	
	"""
	Function that takes a list of acquisition instructions and make
	a sequence dictionary with the Q1ASM sequence and acquisitions needed
	in order to do a full acquisition
	
	The acquisition input list will take the following form:
		['name', delay, duration]
	"""

	# Duration of the acquisition
	
	duration = input[2]

	# If the acquistion is within the time limit of a 16 microsecond acquisition.
	
	if duration <= 16384:
		repeat_num = 1

		if resolution != 1:
			print(f"Resolution Error: Your acquisition is: {duration} ns, which is equal to or shorter than 16 384 ns. Please set your resolution to 1 ns.")
			return None
		
		# Set up the acquisition
		acquisitions = {
			f"{input[0]}": {"num_bins": 1, "index": 0}
		}

		# Initialize the sequence string
		
		seq = f""""""

		# Check if we need to loop
		
		if iterations > 1:
			seq += f"""	move	{iterations},R0\n	loop:"""

		# Set up the sequence
		
		seq += f"""\n	wait_sync	4\n		wait      150"""

		delay = int(input[1])
		
		# If there is a delay for the acquisition start, add a wait command
		
		if delay != 0:
			# Since the maximum wait time for an upd_param command is 65535 ns, we will need multiple commands if there are longer waits than 65535 ns.
			
			num_full_waits = math.floor(delay/65535)	# Number of full wait commands
			remainder = delay%65535	# Remaining wait time
			
			# Adding the wait time to the sequence string
			
			for i in range(num_full_waits):
				seq += f"""\n	wait	65535"""
			if remainder != 0:
				seq += f"""\n	wait	{remainder}"""
		
		# Add the acquire command
		
		seq += f"""\n 	acquire		0,0,{duration}"""		

		# Check if we need to loop
		
		if iterations > 1:
			seq += f"""\n	loop	R0,@loop"""

		# Stop
		
		seq += f"""\n	stop"""
	
	else:
		# If the acquisition is more than 16 microseconds.
		if resolution < 250:
			raise ValueError(f"Resolution Error: Your acquisition is: {duration} ns, which is longer than 16 384 ns. Please set your resolution to at least 250 ns.")

		num_bins = math.ceil(duration / resolution)

		repeat_num = math.ceil(resolution/16384)

		print(f"duration: {duration}, resolution: {resolution}, number of bins: {num_bins}")

		acquisitions = {
				f"{input[0]}": {"num_bins": num_bins*repeat_num, "index": 0}
			}

		# Set up the acquisition sequence, ToF is accounted for as 150 ns
		
		seq = f"""		move 		0,R0\n		wait_sync	4\n		wait		150"""

		delay = int(input[1])
		
		# If there is a delay for the acquisition start
		
		if delay != 0:
			# Since the maximum wait time for an upd_param command is 65535 ns, we will need multiple commands if there are longer waits than 65535 ns.
			
			num_full_waits = math.floor(delay/65535)	# Number of full wait commands
			remainder = delay%65535	# Remaining wait time
			
			# Adding the wait time to the sequence string
			
			for i in range(num_full_waits):
				seq += f"""\n		wait		65535"""
			if remainder != 0:
				seq += f"""\n		wait		{remainder}"""

		# Loop and acquire for however long it needs

		if resolution <= 16384:
			seq += f"""\n		move		{num_bins},R1\n		loop:\n		acquire		0,R0,{resolution}\n		add		R0,1,R0\n		loop		R1,@loop\n		stop"""
		
			repeat_num = 1

		else:

			# Ensure that the acquisition repeats

			seq += f"""\n		move		{num_bins*repeat_num},R1"""

			seq += f"""\n	loop:\n		acquire		0,R0,{int(resolution/repeat_num)}\n		add		R0,1,R0"""

			seq += f"""\n		loop		R1,@loop\n		stop"""

	# print(f"Input sequence:\n{seq}")

	# Add the information to the sequence dictionary
	
	sequence = {
		"waveforms": {},
		"weights": {},
		"acquisitions": acquisitions,
		"program": seq,
	}

	print(seq)

	return sequence, repeat_num

def plot_input(module, sequencer:int, acquisition_name:str, acquisition_time:int, path = 0, save_path = None, filename = None):
	module.get_acquisition_status(sequencer) # Wait for the sequencer to stop with a timeout period of one minute.
	module.store_scope_acquisition(sequencer, acquisition_name) # Move acquisition data from temporary memory to acquisition list.
	readout_data = module.get_acquisitions(sequencer) # Get acquisition list from instrument.
	# filename = filename.replace('.', '_')

	# Find the number of bins to determine what kind of acquistion we are doing.
	num_bins = len(readout_data[acquisition_name]['acquisition']['bins']['integration']['path0'])

	if num_bins == 1:
		# If it is a single acquisition with resolution of 1 ns
		data0 = readout_data[acquisition_name]['acquisition']['scope']['path0']['data'][:acquisition_time] # Extract path 0 data
		data1 = readout_data[acquisition_name]['acquisition']['scope']['path1']['data'] # Extract path 1 data

		# Create plot
		#fig, ax = plt.subplots(2, 1, figsize = (14, 4))
		t = np.arange(0, acquisition_time, 1)

		# Plot both paths data
		if path == 0 or path == 'both':
			plt.plot(t, data0, alpha = 0.9, label = "Path 0")
		if path == 1 or path == 'both':
			pass
			#plt.plot(t, data1, alpha = 0.9, label = "Path 1")

		# Set titles
		plt.title("QRM input")
		plt.xlabel("Time [ns]")
		plt.ylabel("Input [V]")

		# Add a grid to the plot
		plt.grid(ls = '--')
		
		save_path.replace(".", "_")
		filename.replace(".", "_")

		full_path = os.path.join(save_path, filename + ".pdf")

		plt.savefig(full_path,dpi = 600)
		print(f"໒(⊙ᴗ⊙)७✎▤: Plot has been saved at {full_path}.")

		# Display a legend
		plt.legend()
		plt.show()

		# print(data0)
		# print(type(data0))
		
		save_data = []
		save_path.replace(".", "_")
		filename.replace(".", "_")

		full_path = os.path.join(save_path, filename + ".csv")
		
		# print(full_path)
		
		for i, x in enumerate(data0):
			save_data.append([x, data0[i]])
		
		np.savetxt(full_path ,save_data, delimiter = ',', fmt = '%s', header = 'QCM Output 1, QCM Output 2, QRM Input Path Q, QRM Input Path I', comments = '')

	else: 
		# If we are doing a long acquistion (more than 16384 ns), with the data stored in the bins
		# Initialize our resolution variable, to be set later
		resolution = 0

		# Get the resolution from the integration length of the sequencer used for the acquistion
		if sequencer == 0:
			resolution = module.sequencer0.integration_length_acq()
		elif sequencer == 1:
			resolution = module.sequencer1.integration_length_acq()
		elif sequencer == 2:
			resolution = module.sequencer2.integration_length_acq()
		elif sequencer == 3:
			resolution = module.sequencer3.integration_length_acq()
		elif sequencer == 4:
			resolution = module.sequencer4.integration_length_acq()
		elif sequencer == 5:
			resolution = module.sequencer5.integration_length_acq()
		else:
			print("ERROR: sequencer index invalid")
			return None

		repeat_num = math.ceil(resolution/16384)

		data0 = np.array(readout_data[acquisition_name]['acquisition']['bins']['integration']['path0']) / resolution * repeat_num # Extract path 0 data
		data1 = np.array(readout_data[acquisition_name]['acquisition']['bins']['integration']['path1']) / resolution * repeat_num # Extract path 1 data
		
		t = np.arange(resolution, resolution * num_bins + 0.1, resolution)

		# Create plot
		fig, ax = plt.subplots(1, 1, figsize = (14, 4))

		#print(data0[-1])
		#print(data0[-2])
		data0[-1] = data0[-2]
		
		# Plot both paths data
		if path == 0 or path == 'both':
			ax.plot(t, data0, alpha = 1.0, label = "Path 0")
		if path == 1 or path == 'both':
			pass
			#ax.plot(t, data1, alpha = 1.0, label = "Path 1")

		# Set titles
		ax.set_title(f"QRM input {sequencer}")
		ax.set_xlabel("Time [ns]")
		ax.set_ylabel("Input [V]")

		# Add a grid to the plot
		ax.grid(ls = '--')

		if save_path is None or filename is None:raise ValueError("save_path and filename must be provided for saving plots.")

		save_path.replace(".", "_")
		filename.replace(".", "_")


		full_path = os.path.join(save_path, filename + ".pdf")
		plt.savefig(full_path ,dpi = 600)
		print(f"໒(⊙ᴗ⊙)७✎▤: Plot has been saved at {full_path} ")
		
		# Display a legend
		plt.legend()
		plt.show()

		# print(data0)
		# print(type(data0))
		
		save_data = []
		full_path = os.path.join(save_path, filename + ".csv")
		
		# print(full_path)
		
		for i, x in enumerate(data0):
			save_data.append([x, data0[i]])
		
		np.savetxt(full_path ,save_data, delimiter = ',', fmt = '%s', header = 'QCM Output 1, QCM Output 2, QRM Input Path Q, QRM Input Path I', comments = '')


	return None

def plot_input_multi(module, sequencers:list, acquisition_names:list, path = 'both'):
	
	readout_data = []
	
	num_bins = []

	for i in sequencers:

		module.get_acquisition_status(sequencers[i]) # Wait for the sequencer to stop with a timeout period of one minute.

		module.store_scope_acquisition(sequencers[i], acquisition_names[i]) # Move acquisition data from temporary memory to acquisition list.

		readout_data.append(module.get_acquisitions(sequencers[i])) # Get acquisition list from instrument.

		# Find the number of bins to determine what kind of acquistion we are doing.
	
		num_bins.append(len(readout_data[i][acquisition_names[i]]['acquisition']['bins']['integration']['path0']))
	
	#for i in sequencers:
		#print(num_bins[i])

	for i in sequencers:

		# Get the resolution from the integration length of the sequencer used for the acquistion
		if sequencers[i] == 0:
			resolution = module.sequencer0.integration_length_acq()
		elif sequencers[i] == 1:
			resolution = module.sequencer1.integration_length_acq()
		elif sequencers[i] == 2:
			resolution = module.sequencer2.integration_length_acq()
		elif sequencers[i] == 3:
			resolution = module.sequencer3.integration_length_acq()
		elif sequencers[i] == 4:
			resolution = module.sequencer4.integration_length_acq()
		elif sequencers[i] == 5:
			resolution = module.sequencer5.integration_length_acq()
		else:
			print("ERROR: sequencer index invalid")
			return None

		# print(f"resolution in plot_input: {resolution}")
		data0 = np.array(readout_data[i][acquisition_names[i]]['acquisition']['bins']['integration']['path0']) / resolution # Extract path 0 data
		data1 = np.array(readout_data[i][acquisition_names[i]]['acquisition']['bins']['integration']['path1']) / resolution # Extract path 1 data
		t = np.arange(resolution / 2, resolution * num_bins[i] + 0.1, resolution)

		# Create plot
		fig, ax = plt.subplots(1, 1, figsize = (14, 4))

		# Plot both paths data
		if path == 0:
			ax.plot(t, data0, alpha = 0.9, label = "Path 0")
		elif path == 1:
			ax.plot(t, data1, alpha = 0.9, label = "Path 1")
		else:
			ax.plot(t, data0, alpha = 0.9, label = "Path 0")
			ax.plot(t, data1, alpha = 0.9, label = "Path 1")

		# Set titles
		ax.set_title("QRM input")
		ax.set_xlabel("Time [ns]")
		ax.set_ylabel("Input [V]")


		# Add a grid to the plot
		ax.grid(ls = '--')

		# Display a legend
		plt.legend()
		plt.show()
	return None

def marker_only_sequence():
	""" Function that makes a sequence for the QCM_RF to only play its marker to the oscilloscope
	"""
	sequence_dict = {
		"waveforms": {},
		"weights": {},
		"acquisitions": {},
		"program": f"""	wait_sync	4\n	set_mrk	{15}\n	upd_param	500\n	set_mrk	{0b0000}\n	upd_param	4\n	stop"""
	}
	return sequence_dict

def connect_marker_only(sequencer:int):

	eval(f"rf_module.sequencer{sequencer}.sync_en(True)") # Enable sync with the other sequencers
	return None

def connect_output(sequencer:int, output_index:int, path:int):
	""" Function to set up the connection from a sequencer and an output
	"""
	# Determine which path should be used
	path_name = 'I'
	if path == 1:
		path_name = 'Q'
	eval(f"module.sequencer{sequencer}.connect_out{output_index}('{path_name}')") # Connect to the output
	eval(f"module.sequencer{sequencer}.sync_en(True)") # Enable sync with the other sequencers
	return None

def connect_input(module, sequencer:int, input_index:int, path:int, resolution = 300):
	
	# Function to set up the connection from a sequencer and an input
	
	# Determine the path being used
	
	path_name = 'I'
	if path == 1:
		path_name = 'Q'
	
	eval(f"module.sequencer{sequencer}.connect_acq_{path_name}('in{input_index}')")	# Connect to the input
	
	module.scope_acq_sequencer_select(sequencer) # Select scope mode
	
	eval(f"module.scope_acq_trigger_mode_path{path}('sequencer')") # Set the scope to trigger off of the sequencer acquire commands
	
	eval(f"module.sequencer{sequencer}.delete_acquisition_data(all = True)") # Delete the previous acquisition
	
	'''if resolution == 1:
		eval(f"module.sequencer{sequencer}.integration_length_acq({4})") # Set the integration length / resolution'''
	if resolution != 1:
		eval(f"module.sequencer{sequencer}.integration_length_acq({resolution})") # Set the integration length / resolution
	
	eval(f"module.scope_acq_avg_mode_en_path{path}(True)") # Enable averaging over many loops
	
	eval(f"module.sequencer{sequencer}.sync_en(True)") # Enable syncing with other sequencers
	return None

def disconnect_io(module):
	module.disconnect_outputs() # Disconnect the module's outputs
	if module.is_qrm_type:
		module.disconnect_inputs() #  If there are inputs, disconnect the inputs

def sequence_helper_test():

	"""This function is to provide a test that the sequence_helper file 
	has been successfully loaded in the notebook being used"""

	return "test successful"

def acquire_scope_and_calc_offsets(module) -> tuple[float, float]:

	acquisitions = {
    	"non_weighed": {"num_bins": 10, "index": 0},
    	"weighed": {"num_bins": 10, "index": 1},
    	"large": {"num_bins": 131072, "index": 2},
    	"avg": {"num_bins": 10, "index": 3},
    	"single": {"num_bins": 1, "index": 4},
	}
	
	seq_prog = """
        move    1000, R0        #Loop iterator.

    loop: acquire 0,1,20000   #Acquire bins and store them in "non_weighed" acquisition.
        loop     R0, @loop #Run until number of iterations is done.

        stop                #Stop.
    """

    # Add sequence program, waveforms, weights and acquisitions to single dictionary and write to JSON file.
    
	sequence = {
        "waveforms": {},
        "weights": {},
        "acquisitions": acquisitions,
        "program": seq_prog,
    }

	with open("sequence.json", "w", encoding="utf-8") as file:
		json.dump(sequence, file, indent=4)
		file.close()

    # Upload sequence.
	module.sequencer0.sequence("sequence.json")

    # Arm and start sequencer.
	module.arm_sequencer(0)
	module.start_sequencer()

    # Retrieve results
	module.store_scope_acquisition(0, "single")
	single_acq = module.get_acquisitions(0)
	I_data = np.array(single_acq["single"]["acquisition"]["scope"]["path0"]["data"])
	Q_data = np.array(single_acq["single"]["acquisition"]["scope"]["path1"]["data"])

    # Plot results
	# fig, ax = plt.subplots(1, 1)
	# ax.plot(I_data, label="I")
	# ax.plot(Q_data, label="Q")
	# ax.set_xlabel("Time (ns)", fontsize=20)
	# ax.set_ylabel("Relative amplitude", fontsize=20)
	# plt.legend()
	# plt.show()

    # Print mean offset values
	I_offset, Q_offset = np.mean(I_data), np.mean(Q_data)
	# print(f"I Offset : {I_offset*1e3:.3f} mV \nQ Offset : {Q_offset*1e3:.3f} mV")

	time.sleep(1)

	return I_offset, Q_offset