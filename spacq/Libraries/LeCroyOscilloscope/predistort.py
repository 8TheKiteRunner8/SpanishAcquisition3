"""
predistort.py

Handles the generation and testing of infinite impulse reponse (IIR) filters
which are used to correct for the signal distortion caused by an electical system.
To preform the correction, a square pulse is sent through the system and the response is
measured. An exponential decay function is then fit to the response, to extract function parameters.
By taking the reciprocal of the Laplace transform of the decay function, we get the inverse transfer function.
The bilinear transform of the transfer function is taken next to determine filter coefficients a1, b0, b1,
of the IIR filter. This approach is then iterated on until the output of the system procduces the ideal signal
of a flat square pulse. The filter coefficents can then be applied to an arbitrary pulse to correct for distortions.

Note: The load impedance connected to the bias tee is extremely important to consider because it can alter the time constant of the circuit.
In our testing, this was the primary cause of signal distortion because the load impedance was in parallel with bias tee's resistor,
meaning the actual time constant of the circuit is changed. We tested the following two cases:

Our bias tee was an RC circuit with R approximately equal to 2.2kOhm.
When on a 50Ohm load, the parallel combination of load and R was close to 50Ohm, which made for a small time constant,
	and thus distortions were present on a short timescale.
When on a 1MOhm load, the parallel combination of load and R was close to 2.2kOhm, which made for a much larger time constant,
	and thus distortion were not present on the short timescale relevant to Qblox.

The correction approach is based on the following work:
J. Butscher, "Shaping of Fast Flux Pulses for Two-Qubit Gates: Inverse Filtering", M.S. thesis,
	Quantum Device Lab, ETH Zurich, Zurich, 2018.

This thesis was applied to superconducting qubits, which is all matched to 50Ohm. Quantum dot spin qubits however, have
	a high Z load impedance since they are voltages applied to gates.

predistort.py has been tested on a bias T connected directly between the inputs and outputs of Qblox,
and tested with the bias tee input from Qblox, and the output going to a LeCroy oscilloscope to act as a 1MOhm load.

Authors: Noah Stieler, Luke Dyer
"""

import json
import os

import matplotlib.pyplot as plt
import numpy as np
from numpy.typing import NDArray
from qblox_instruments import Cluster
from qblox_instruments.types import InstrumentType
import scipy.optimize

class Instrument:
	"""Base class for the Qblox and LeCroy hardware.
		not to be instantiated, instead instantiate InstrumentLeCroy and InstrumentQBlox"""
	def __init__(self):
		self.outSamplingRate = None
		self.acqSamplingRate = None
		self.triggerLevel = None
	
	@property
	def outSamplingPeriod(self):
		if self.outSamplingRate is not None:
			return 1.0/self.outSamplingRate
		else:
			return None
	
	@property
	def acqSamplingPeriod(self):
		if self.acqSamplingRate is not None:
			return 1.0/self.acqSamplingRate
		else:
			return None
	
	def initializeAcquisition(self) -> str:
		print("Function not implemented")
		return None
	
	def arm(self) -> None:
		print("Function not implemented")
		return
	
	def wait(self) -> None:
		print("Function not implemented")
		return
	
	def saveWaveform(self) -> list:
		print("Function not implemented")
		return []
	
	def loadSquarePulse(self, pulseLength: int) -> None:
		print("Function not implemented")
		return ""

	def loadPulse(self, waveform: list[float]) -> None:
		print("Function not implemented")
		return ""
	
	def playPulse(self) -> None:
		print("Function not implemented")
		return

def iterativeIIRFiltering(numIterations:int,
							curveFitBounds:list[tuple],
							pulseDuration:int,
							acqInstr:Instrument, outInstr:Instrument,
							paramGuessList:list[tuple] = [(0.3, 1e-4, 10e-6)],
							reductionFactor:float = 4):
	"""
	Apply numIterations IIR filters to a square pulse of pulseDuration. Send out the pulse,
	acquire the response, use it to calculate the next IIR filter, apply the filter to the pulse
	that was sent out, and use the resulting signal for the next iteration.
	### Parameters
	numIterations:int
		Number of filters to generate.
	curveFitBounds:list[tuple]
		List of lower and upper bounds for each iteration, in terms of sample index, not time.
		If the length of curveFitBounds is less than numIterations, the extra iterations uses the 
		last set of user specified bounds.
	pulseDuration:int
		Length of the square pulse used for generating filters.
		Value is in nanoseconds.
	acqInstr: Instrument
		Instrument to acquire data from. Must be a child and implement the proper
		functions from the Instrument parent class above
	outInstr: Instrument
		Instrument to send a pulse from. Must be a child and implement the proper
		functions from the Instrument parent class above
	paramGuessList:list[tuple]
		3-valued tuple used as initial parameter guess for scipy.optimize.curve_fit().
		Similar to curveFitBounds, if the length of paramGuessList is less than numIterations,
		the extra iterations uses the last set of user-specfified guesses.
	reductionFactor:float
		Extra gain factor to scale the output waveform during filtering to ensure
		it does not get clipped by Qblox's input or ouput limits.
	acqInstr:InstrumentLeCroy
		If given an instance of InstrumentLeCroy, connect the circuit output to the input of
		the LeCroy oscilloscope, and the output of Qblox to the circuit input. This configuration
		is for generating filters with 1MOhm input impedance on the output terminal.
	### Returns
	list[dict]
		Each entry in the list is a dictionary which stores all the relevant information
		from that filtering iteration. The dictionary has the following keys:\n
			"response" 		Raw output waveform acquired on the iteration.\n
			"curveFitY"		Y values of the fitted curve.\n
			"curveFitX" 	X values of the fitted curve, in seconds.\n
			"input" 		The waveform that was played on this iteration.\n
			"filter" 		Tuple containing the filter coefficients: (a1, b0, b1).
			"pulseDuration" Duration of square pulse.
	"""
	
	#Pad the remaining curve fits and parameter guesses with the final
	#user specified curve fit and parameter guess respectively.
	if len(curveFitBounds) < numIterations:
		for i in range(numIterations - len(curveFitBounds)):
			curveFitBounds.append(curveFitBounds[-1])
	if len(paramGuessList) < numIterations:
		for i in range(numIterations - len(paramGuessList)):
			paramGuessList.append(paramGuessList[-1])

	iterationData = []
	currentWaveform = []

	#Square pulse
	currentWaveform = [0.8 for i in range(pulseDuration)]

	# Loop through every iteration
	for iteration in range(numIterations):
		iterationData.append({})

		# Acquisition data for this run
		acqData = []

		# Load the pulse and then delete the sequence json
		outInstr.loadPulse(currentWaveform)

		# Initalize the acuisition instrument
		acqInstr.initializeAcquisition()
		samplePeriod = 1.0/acqInstr.acqSamplingRate

		# loop over the number of sweeps the acuisiton instrument requires
		for i in range(acqInstr.numSweeps):
			# Arm the acuisition and then play the sequence
			acqInstr.arm()
			outInstr.playPulse()
			acqInstr.wait()

		# Save the waveform from the acquisition intrument
		acqData = acqInstr.saveWaveform()


		# Start waveform as close to 0 as possible
		newStart = 0
		for i in range(len(acqData)):
			if acqData[i] > acqInstr.triggerLevel:
				newStart = i
				break

		acqData = acqData[newStart-1:]

		# Get the fit bounds
		fitStartIndex = curveFitBounds[iteration][0]
		fitEndIndex = curveFitBounds[iteration][1]
		fitStartIndex -= newStart
		fitEndIndex -= newStart

		# Get the x and y data for the fit
		xData = [(fitStartIndex + i)*samplePeriod for i in range(fitEndIndex - fitStartIndex)]
		yData = acqData[fitStartIndex:fitEndIndex]

		# Try and fit to the curve
		try:
			popt, pcov = scipy.optimize.curve_fit(_expDecayModel,
											np.array(xData), np.array(yData),
											p0=paramGuessList[iteration])
		except Exception as e:
			#Return the previous successful iterations
			print(e)
			print(f"iterativeIIRFiltering failed on iteration {iteration}.")
			return iterationData

		A, B, tau = popt #expDecayModel parameters
		curveFit = _expDecayModel(np.array(xData), A, B, tau)

		#Calculate IIR filter coefficients
		lamb = 2*A*tau + 2*B*tau + A*samplePeriod
		#The term with A*tau was missing a factor of 2 in the original work
		a1 = (2*A*tau + 2*B*tau - A*samplePeriod)/lamb
		b0 = (2*tau + samplePeriod)/lamb
		b1 = (-2*tau + samplePeriod)/lamb

		#Save all the data from this iteration
		iterationData[iteration]["response"] = acqData
		iterationData[iteration]["curveFitY"] = curveFit.tolist()
		iterationData[iteration]["curveFitX"] = xData
		iterationData[iteration]["input"] = currentWaveform
		iterationData[iteration]["filter"] = (a1, b0, b1)
		iterationData[iteration]["pulseDuration"] = pulseDuration

		#Filter the input waveform and send it back through the device
		filteredData = _IIRFilter(a1, b0, b1, currentWaveform)

		#After normalizing filter results, divide each sample by reductionFactor.
		#This is to ensure that the response on the input stays below Qblox's maximum
		#input value.
		filteredData = ((np.array(filteredData)/(reductionFactor*max(filteredData)))).tolist()
	
		currentWaveform = filteredData

	return iterationData

def singlePulseIterativeIIRFiltering(numIterations:int,
									curveFitBounds:list[tuple],
									pulseDuration:int,
									acqInstr:Instrument, outInstr:Instrument,
									paramGuessList:list[tuple] = [(0.3, 1e-4, 10e-6)]):
	voltageThreshold = 0.05

	#Pad the remaining curve fits and parameter guesses with the final
	#user specified curve fit and parameter guess respectively.
	if len(curveFitBounds) < numIterations:
		for i in range(numIterations - len(curveFitBounds)):
			curveFitBounds.append(curveFitBounds[-1])
	if len(paramGuessList) < numIterations:
		for i in range(numIterations - len(paramGuessList)):
			paramGuessList.append(paramGuessList[-1])

	iterationData = []
	currentWaveform = []

	#Square pulse
	currentWaveform = [1.0 for i in range(pulseDuration)]

	# Acquisition data for this run
	acqData = []

	# Load the pulse and then delete the sequence json
	outInstr.loadSquarePulse(pulseDuration)

	# Initalize the acquisition instrument
	acqInstr.initializeAcquisition()

	# loop over the number of sweeps the acuisiton instrument requires
	for i in range(acqInstr.numSweeps):
		# Arm the acuisition and then play the sequence
		acqInstr.arm()
		outInstr.playPulse()
		acqInstr.wait()

	# Save the waveform from the acquisition intrument
	acqData = acqInstr.saveWaveform()

	# Start waveform as close to 0 as possible
	newStart = 0
	for i in range(len(acqData)):
		if acqData[i] > voltageThreshold:
			newStart = i
			break

	acqData = acqData[newStart-1:]

	# Loop through every iteration
	for iteration in range(numIterations):
		iterationData.append({})

		# Get the fit bounds
		fitStartIndex = curveFitBounds[iteration][0]
		fitEndIndex = curveFitBounds[iteration][1]
		fitStartIndex -= newStart
		fitEndIndex -= newStart

		# Get the x and y data for the fit
		xData = [(fitStartIndex + i)*acqInstr.acqSamplingPeriod for i in range(fitEndIndex - fitStartIndex)]
		yData = acqData[fitStartIndex:fitEndIndex]

		# Try and fit to the curve
		try:
			popt, pcov = scipy.optimize.curve_fit(_expDecayModel,
											np.array(xData), np.array(yData),
											p0=paramGuessList[iteration])
		except Exception as e:
			#Return the previous successful iterations
			print(e)
			print(f"iterativeIIRFiltering failed on iteration {iteration}.")
			return iterationData

		A, B, tau = popt #expDecayModel parameters
		curveFit = _expDecayModel(np.array(xData), A, B, tau)

		#Calculate IIR filter coefficients
		Ts = acqInstr.acqSamplingPeriod
		lamb = 2*A*tau + 2*B*tau + A*Ts
		#The term with A*tau was missing a factor of 2 in the original work
		a1 = (2*A*tau + 2*B*tau - A*Ts)/lamb
		b0 = (2*tau + Ts)/lamb
		b1 = (-2*tau + Ts)/lamb

		#Save all the data from this iteration
		iterationData[iteration]["response"] = acqData
		iterationData[iteration]["curveFitY"] = curveFit.tolist()
		iterationData[iteration]["curveFitX"] = xData
		iterationData[iteration]["input"] = currentWaveform
		iterationData[iteration]["filter"] = (a1, b0, b1)
		iterationData[iteration]["pulseDuration"] = pulseDuration

		#Filter the input waveform and send it back through the device
		filteredData = _IIRFilter(a1, b0, b1, acqData)

		acqData = _IIRFilter(a1, b0, b1, acqData)

		#After normalizing filter results, divide each sample by reductionFactor.
		#This is to ensure that the response on the input stays below Qblox's maximum
		#input value.
		filteredData = ((np.array(filteredData)/(max(filteredData)))).tolist()

	return iterationData

def plotFilterIterations(iterationData:list[dict], pulseDuration:int, samplePeriod:float) -> None:
	"""
	Plot every iteration of the IIR filtering procedure.
	### Parameters
	iterationData:list[dict]
		list[dict] that was returned by iterativeIIRFiltering.
	pulseDuration:int
		Length of the square pulse used for generating filters.
		Value is in nanoseconds.
	### Returns
	None
	"""
	# Loop through and plot each iteration curve fit and response
	for i in range(len(iterationData)):
		fig, ax = plt.subplots(1, 2, sharex=True, figsize = (15, 5))

		acqDataTime = [j*samplePeriod for j in range(len(iterationData[i]["response"]))]
		ax[0].set_title(f"Input and Response Signal (iteration={i})")
		ax[0].plot(acqDataTime[:pulseDuration + int(0.1 * pulseDuration)], iterationData[i]["response"][:pulseDuration + int(0.1 * pulseDuration)], label = "Response")
		ax[0].plot(iterationData[i]["curveFitX"], iterationData[i]["curveFitY"], label = "Fit")
		#ax[0].plot(acqDataTime[:pulseDuration + int(0.1 * pulseDuration)], iterationData[i]["input"][:pulseDuration + int(0.1 * pulseDuration)], label = "Input")
		ax[0].legend()
		ax[0].set_xlabel("Time (s)")
		
		ax[1].set_title(f"Response Signal (iteration={i})")
		ax[1].plot(acqDataTime[:pulseDuration + int(0.1 * pulseDuration)], iterationData[i]["response"][:pulseDuration + int(0.1 * pulseDuration)], label = "Response")
		ax[1].set_ylim(min(iterationData[i]["response"][200:pulseDuration]) - 0.002, max(iterationData[i]["response"][200:pulseDuration]) + 0.002)
		ax[1].plot(iterationData[i]["curveFitX"], iterationData[i]["curveFitY"], label = "fit")
		ax[1].legend()
		ax[1].set_xlabel("Time (s)")

		fig.text(0.08, 0.5, 'Voltage (V)', va='center', rotation='vertical')

	plt.show()

	return

def applyFiltersToWaveform(iterationData:list[dict], waveform:list[float], reductionFactor:float = 4) -> list[float]:
	"""
	Apply all the filters in iterationData to waveform.
	### Parameters
	iterationData:list[dict]
		Formatted list returned by iterativeIIRFiltering().
	waveform:list[float]
		Waveform to be filtered, specified as a list of points.
	reductionFactor:float
		Extra gain factor to scale the output waveform during filtering to ensure
		it does not get clipped by Qblox's input or ouput limits.
	### Returns
	list[float]
		The filtered waveform.
	"""
	output = waveform.copy()
	for iteration in range(len(iterationData)):
		output = _IIRFilter(
			iterationData[iteration]["filter"][0], iterationData[iteration]["filter"][1],
			iterationData[iteration]["filter"][2], output)
		
		#After normalizing filter results, divide each sample by reductionFactor.
		#This is to ensure that the response on the input stays below Qblox's maximum
		#input value.
		output = ((np.array(output)/(reductionFactor*max(output)))).tolist()
	return output

def _expDecayModel(t:NDArray, A:float, B:float, tau:float) -> NDArray:
	"""
	Exponential decay function, A + B*e^(-t/tau)
	"""
	return A + B*np.exp(-1*t/tau)

def _IIRFilter(a1:float, b0:float, b1:float, x:list[float]) -> list[float]:
	"""
	Applies a first-order IIR filter with coefficients a1, b0, b1
		to the input list x.
	### Parameters
	a1:float
		IIR filter coefficient
	b0:float
		IIR filter coefficient
	b1:float
		IIR filter coefficient
	x:list[float]
		Input signal to filter as a list of points.
	### Returns
	list[float]
		Filtered signal.
	"""
	y = [0.0] #First order IIR filter requires one sample at the start
	x.insert(0, 0.0)
	for i in range(1, len(x)):
		y.append(b0*x[i] + b1*x[i-1] + a1*y[i-1])
	y.pop(0) #To ensure output list is the same length as input
	x.pop()
	return y