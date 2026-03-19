from abc import ABC
from graph.counters.counter_candidates import CounterCandidate, FACandidate
from graph.counters.counter_candidates import MuxCYAtomCascadeCandidate
from graph.counters.counter_candidates import RippleSumCandidate
from graph.counters.counter_candidates import DualRailRippleSumCandidate
from graph.counters.counter_candidates import FiveTwoCandidate 
from graph.counters.counter_candidates import VersalAtomCascadeCandidate
from graph.counters.counter_candidates import SixThreeCandidate, TenSixCandidate
from graph.counters.absorption_counter_candidates import GateAbsorptionCounterCandidate
from graph.counters.absorption_counter_candidates import VersalPredAdderCandidate
from graph.counters.absorption_counter_candidates import RippleSumPredAdderCandidate
from graph.counters.absorption_counter_candidates import SinglePredCandidate
from graph.counters.absorption_counter_candidates import MuxCYPredAdderCandidate
from graph.final_adder import MuxCYTernaryAdder, FinalAdder, QuaternaryAdder
from typing import List

class Target(ABC):
    counter_candidates: List[CounterCandidate]
    final_adder: FinalAdder
    absorbing_counter_candidates: List[GateAbsorptionCounterCandidate]

class Versal(Target):
    def __init__(self):
        self.counter_candidates = [
            TenSixCandidate(),
            FACandidate(), 
            RippleSumCandidate(), 
            DualRailRippleSumCandidate(),
            FiveTwoCandidate(), 
            SixThreeCandidate(),
            VersalAtomCascadeCandidate()
        ]
        self.absorbing_counter_candidates = [
            VersalPredAdderCandidate(),
            RippleSumPredAdderCandidate(),
            SinglePredCandidate(),
        ]
        self.final_adder = QuaternaryAdder

class SevenSeries(Target):
    def __init__(self):
        self.counter_candidates = [FACandidate(), FiveTwoCandidate(), 
                                   SixThreeCandidate(), MuxCYAtomCascadeCandidate()]
        self.final_adder = MuxCYTernaryAdder
        self.absorbing_counter_candidates = [
            SinglePredCandidate,
            MuxCYPredAdderCandidate
        ]