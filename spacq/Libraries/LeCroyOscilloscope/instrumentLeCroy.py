import pyvisa
import time

import matplotlib.pyplot as plt

import predistort

class InstrumentLeCroy(predistort.Instrument):
	def __init__(self, gpibAddress:str, timeDiv:int, totalSampleCount:int, numSweeps:int, triggerLevel:float=0.05):
		self._TIME_DIVISION_COUNT = 10

		self.address = gpibAddress
		self.resourceManager = pyvisa.ResourceManager()
		self.instrument = self.resourceManager.open_resource(gpibAddress)

		self.timeDiv = timeDiv
		self.totalSampleCount = totalSampleCount
		self.numSweeps = numSweeps
		self.triggerLevel = triggerLevel
		self.acqSamplingRate = 1.0/((self._TIME_DIVISION_COUNT*self.timeDiv*1e-9)/self.totalSampleCount)

		self.instrument.clear()
		self.instrument.timeout = 300000#Milliseconds

	def __del__(self):
		self.instrument.close()
		self.resourceManager.close()

	def writeCmd(self, cmd:str) -> None:
		if (cmd[0:3] == 'app'):
			self.instrument.write(rf"""vbs '{cmd}' """)
		else:
			self.instrument.write(cmd)

		r = self.instrument.query(r"""vbs? 'return=app.WaitUntilIdle(5)' """)
		if r == 0: #Timeout occured
			print("ERROR: Instrument is busy, a timeout has occured.")
	
	def queryCmd(self, cmd:str):
		if (cmd[0:3] == 'app'):
			return self.instrument.query(rf"""vbs? '{cmd}' """)
		return self.instrument.query(cmd)

	def initializeAcquisition(self) -> str:
		self.writeCmd('COMM_HEADER OFF')
		self.writeCmd('app.settodefaultsetup')
		self.writeCmd('app.measure.clearall')
		self.writeCmd('app.measure.clearsweeps')

		self.writeCmd('GRID SINGLE')
		self.writeCmd('C2:TRACE OFF')
		#self.writeCmd('TRIG_MODE AUTO')
		self.writeCmd('C1:VOLT_DIV 0.5V')
		self.writeCmd(f'TIME_DIV {self.timeDiv}NS')
		self.writeCmd('TRIG_MODE STOP')
		self.writeCmd(f'MEMORY_SIZE {self.totalSampleCount}')
		self.writeCmd('F1:TRACE ON')
		self.writeCmd(f"F1:DEF EQN,'AVG(C1)',AVERAGETYPE,SUMMED,SWEEPS,{self.numSweeps}")
		self.writeCmd(f'C1:TRIG_LEVEL {self.triggerLevel}')
		return None
	
	#Delete this function
	"""def acqData(self):
		for i in range(5000):
			self.writeCmd('ARM')
			self.writeCmd('WAIT')

		#print(self.instrument.query('C1:INSPECT? "SIMPLE",FLOAT').split("  ")[1:20])
		plt.plot([ float(x.strip()) for x in self.instrument.query('F1:INSPECT? "SIMPLE",FLOAT').split("  ")[1:-2]])
		plt.show()
		# print(self.instrument.read_raw())"""

	def arm(self):
		self.writeCmd('ARM')

	def wait(self):
		self.writeCmd('WAIT')

	def saveWaveform(self) -> list:
		rawData = self.instrument.query('F1:INSPECT? "SIMPLE",FLOAT').split("  ")[1:-2]
		return [float(datum.strip()) for datum in rawData]

def main():
	numSweeps = 1000
	leCroy = InstrumentLeCroy("GPIB0::8::INSTR", timeDiv=1e5, totalSampleCount=10000,
						numSweeps=numSweeps)
	leCroy.initialize()
	
	for i in range(numSweeps):
		leCroy.setTrigger()
	
	plt.plot(leCroy.saveWaveform())
	plt.show()

#main()