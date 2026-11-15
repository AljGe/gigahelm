# -*- coding: utf-8 -*-
"""
A shared, dependency-free PCHIP (Piecewise Cubic Hermite Interpolating Polynomial) interpolation module.
Ensures the backend fan control logic and the frontend UI rendering use the exact same smooth, monotonic curve algorithm.
"""
from typing import List, Union

# --- Dependency-free numeric functions ---

def linspace(start: float, stop: float, num: int) -> List[float]:
    """Generates `num` evenly spaced numbers over the given interval."""
    if num == 0: return []
    if num == 1: return [start]
    step = (stop - start) / (num - 1)
    return [start + i * step for i in range(num)]

def clip(a: Union[float, List[float]], a_min: float, a_max: float) -> Union[float, List[float]]:
    """Clips a value or a list of values to the given range."""
    if isinstance(a, (int, float)):
        return max(a_min, min(a_max, a))
    return [max(a_min, min(a_max, val)) for val in a]

def interp(x: float, xp: List[float], fp: List[float]) -> float:
    """
    A pure-Python replacement for numpy.interp.
    Performs linear interpolation.
    """
    if not xp:
        return 0.0
    
    # Find the correct interval for x
    if x <= xp[0]:
        return fp[0]
    if x >= xp[-1]:
        return fp[-1]

    # Find the interval containing x
    i = 0
    while i < len(xp) - 1 and x > xp[i+1]:
        i += 1
    
    # Linear interpolation formula
    x0, x1 = xp[i], xp[i+1]
    y0, y1 = fp[i], fp[i+1]
    
    # Avoid division by zero if points are not unique
    if x1 == x0:
        return y0
        
    return y0 + (x - x0) * (y1 - y0) / (x1 - x0)


class PchipInterpolator:
    """
    A from-scratch implementation of PCHIP interpolation.
    It produces a monotonic cubic spline passing through all data points.
    This implementation is dependency-free.
    """
    def __init__(self, x: List[float], y: List[float], extrapolate: bool = False):
        if len(x) != len(y) or len(x) < 2:
            raise ValueError("x and y must be equal-size lists with at least 2 points.")

        # Sort points by x value to ensure correct interpolation
        sorted_points = sorted(zip(x, y))
        self.x = [p[0] for p in sorted_points]
        self.y = [p[1] for p in sorted_points]
        self.extrapolate = extrapolate

        self.n = len(self.x)
        self.h = [self.x[i+1] - self.x[i] for i in range(self.n - 1)]
        self.delta = [(self.y[i+1] - self.y[i]) / self.h[i] for i in range(self.n - 1)]
        
        self.d = self._calculate_derivatives()

    def _calculate_derivatives(self) -> List[float]:
        """Computes the derivative (slope) at each data point."""
        d = [0.0] * self.n

        # Endpoints (not central differences)
        d[0] = self.delta[0]
        d[self.n - 1] = self.delta[self.n - 2]

        # Interior points
        for i in range(1, self.n - 1):
            h0, h1 = self.h[i-1], self.h[i]
            d0, d1 = self.delta[i-1], self.delta[i]

            # If slopes have different signs or either is zero, the derivative is zero
            # to prevent overshoot and preserve monotonicity.
            if d0 * d1 <= 0:
                d[i] = 0.0
            else:
                # Weighted harmonic mean for monotonicity
                w1 = 2 * h1 + h0
                w2 = h1 + 2 * h0
                d[i] = (w1 + w2) / (w1 / d0 + w2 / d1)
        
        return d

    def __call__(self, xi: Union[float, List[float]]) -> Union[float, List[float]]:
        """Evaluates the interpolator at the given points."""
        is_scalar = not isinstance(xi, list)
        if is_scalar:
            # mypy complains if xi is not a list, so we make it a list
            xi_list = [xi]
        else:
            xi_list = xi

        results = []
        for val in xi_list:
            results.append(self._evaluate_single(val))
        
        return results[0] if is_scalar else results

    def _evaluate_single(self, val: float) -> float:
        """Evaluates the interpolator for a single value."""
        # Find the interval containing val
        if val < self.x[0]:
            return self.y[0] if not self.extrapolate else self._extrapolate(val, 0)
        if val > self.x[-1]:
            return self.y[-1] if not self.extrapolate else self._extrapolate(val, self.n - 2)

        # Binary search for the correct interval
        low, high = 0, self.n - 1
        while low <= high:
            mid = (low + high) // 2
            if self.x[mid] < val:
                low = mid + 1
            elif self.x[mid] > val:
                high = mid - 1
            else:
                return self.y[mid] # Exact match
        
        # `high` is now the index of the interval start
        i = high if high >= 0 else 0
        if i >= self.n - 1: i = self.n - 2 # Make sure we do not go out of bounds

        # Normalize t to the interval [0, 1]
        t = (val - self.x[i]) / self.h[i]

        # Hermite basis functions
        h00 = (2 * t**3) - (3 * t**2) + 1
        h10 = (t**3) - (2 * t**2) + t
        h01 = (-2 * t**3) + (3 * t**2)
        h11 = (t**3) - (t**2)

        # Cubic Hermite spline formula
        return (h00 * self.y[i] +
                h01 * self.y[i+1] +
                h10 * self.h[i] * self.d[i] +
                h11 * self.h[i] * self.d[i+1])

    def _extrapolate(self, val: float, i: int) -> float:
        """Linear extrapolation using the endpoint derivative."""
        return self.y[i] + (val - self.x[i]) * self.d[i]