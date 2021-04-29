#  Copyright 2021 by Benjamin J. Land (a.k.a. BenLand100)
#
#  This file is part of kdfit.
#
#  kdfit is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  kdfit is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with kdfit.  If not, see <https://www.gnu.org/licenses/>.

import numpy as np
try:
    import cupy as cp
except:
    print('kdfit.term could not import CuPy - falling back to NumPy')
    cp = np # Use numpy to emulate cupy on CPU
import itertools as it
from .calculate import Calculation
from .signal import Signal
from .utility import PDFBinner, PDFEvaluator, binning_to_edges
        
class Sum(Calculation):
    '''
    A Calculation that adds a series of input Calculations
    '''
    def __init__(self, name, *inputs):
        super().__init__(name, inputs)
        self.name = name
    def calculate(self, inputs, verbose=False):
        return np.sum(inputs)
    
class UnbinnedNegativeLogLikelihoodFunction(Calculation):
    '''
    Calculates the negative log likelihood of data x_kj with a PDF given by
    the sum of the PDFs from some scaled signals. The signal scales, or number 
    of events for that signal, are the inputs to this calculation. Also 
    calculates the Poisson likelihood of observing k events given a mean event 
    rate that is the sum of the signal scales. The final result is the negative
    logarithm of the product of the data likelihood and Poisson likelihood that
    omits any terms that are constant as a function of input scales.
    '''
    def __init__(self, name, signals, observables):
        self.signals = signals
        self.observables = observables
        n_evs = [s.nev_param for s in signals]
        pdf_sk = [PDFEvaluator(s.name+'_eval',s,observables) for s in signals]
        super().__init__(name,n_evs+pdf_sk)

    def calculate(self, inputs, verbose=False):
        n_evs = cp.asarray(list(inputs[:len(self.signals)]))
        pdf_sk = cp.asarray(list(inputs[len(self.signals):]))
        if verbose:
            print('Evaluate:',', '.join(['%0.3f'%s for s in n_evs]))
        pdf_k = cp.sum((pdf_sk.T*n_evs),axis=1)
        if cp == np: # No GPU acceleration
            res = np.sum(n_evs) - np.sum(np.log(pdf_k))
        else:
            res = cp.sum(n_evs).get() - cp.sum(cp.log(pdf_k)).get()
        if verbose:
            print('NLL:',res)
        if np.isnan(res):
            raise Exception('NaN unbinned likelihood!')
        return res

class BinnedNegativeLogLikelihoodFunction(Calculation):
    '''
    Calculates the negative log likelihood of binned data x_kj with a PDF given by
    the sum of the PDFs from some scaled signals. The signal scales, or number 
    of events for that signal, are the inputs to this calculation. Also 
    calculates the Poisson likelihood of observing k events given a mean event 
    rate that is the sum of the signal scales. The final result is the negative
    logarithm of the product of the data likelihood and Poisson likelihood that
    omits any terms that are constant as a function of input scales.
    '''
    def __init__(self, name, signals, observables, binning=21):
        self.bin_edges = binning_to_edges(binning)
        self.bin_edges = cp.ascontiguousarray(cp.asarray(self.bin_edges))
        self.a_kj = cp.ascontiguousarray(cp.asarray([cp.asarray(x) for x in it.product(*self.bin_edges[:, :-1])]))
        self.b_kj = cp.ascontiguousarray(cp.asarray([cp.asarray(x) for x in it.product(*self.bin_edges[:,1:  ])]))
        self.bin_vol = cp.ascontiguousarray(cp.prod(self.b_kj-self.a_kj,axis=1))
        self.signals = signals
        self.observables = observables
        n_evs = [s.nev_param for s in signals]
        binned_signals = [PDFBinner(s.name+'_binner',s,binning=binning) for s in signals]
        super().__init__(name,n_evs+binned_signals+[observables])
        self.last_x_kj = None
    
    def calculate(self, inputs, verbose=False):
        n_evs = inputs[:len(self.signals)]
        binned_signals = inputs[len(self.signals):-1]
        x_kj = inputs[-1]
        if x_kj is not self.last_x_kj:
            if np == cp:
                self.counts,_ = np.histogramdd(x_kj,bins=self.bin_edges)
            else: # FIXME cupy-9.0.0 implements histogramdd (needs testing)
                counts,_ = np.histogramdd(x_kj,bins=self.bin_edges.get())
                self.counts = cp.asarray(counts)
            self.last_x_kj = x_kj
        if verbose:
            print('Evaluate:',', '.join(['%0.3f'%s for s in n_evs]))
        expected = cp.sum(cp.asarray([n*bin_ints for n,bin_ints in zip(n_evs,binned_signals)]),axis=0)
        expected = expected.reshape(self.counts.shape)
        mask = expected != 0
        res = cp.sum(n_evs) - cp.sum(self.counts[mask]*cp.log(expected[mask]))
        if verbose:
            print('NLL:',res)
        if np.isnan(res):
            raise Exception('NaN binned likelihood!')
        return res if np == cp else res.get()
