# -*- coding: utf-8 -*-
"""
A pure, side-effect-free calculation engine that computes the target fan speed from temperature curves.
The class keeps internal state for auto mode (e.g. hysteresis and the current target) but performs no hardware I/O.
"""

from typing import List, Optional, Dict, cast
import sys

from gui.qt import QObject

from .interpolation import PchipInterpolator, clip, interp
from .state import ProfileState

from config.settings import (
    MIN_FAN_PERCENT, MAX_FAN_PERCENT,
    MIN_POINTS_FOR_INTERPOLATION,
    TEMP_READ_ERROR_VALUE, INIT_APPLIED_PERCENTAGE
)

# Type hints
FanTable = List[List[int]] # [[temperature, speed], ...]

class AutoTemperatureController(QObject):
    """
    Computes and smoothly adjusts the fan speed from the given temperature and fan curves.
    An I/O-free state calculator, driven by AppServices.
    """
    def __init__(self):
        super().__init__()
        # --- Internal interpolators and curve data ---
        self._cpu_interpolator: Optional[PchipInterpolator] = None
        self._gpu_interpolator: Optional[PchipInterpolator] = None
        self._cpu_curve_data: FanTable = []
        self._gpu_curve_data: FanTable = []

        # --- Runtime state variables ---
        self._last_theoretical_target: int = INIT_APPLIED_PERCENTAGE

    def get_last_theoretical_target(self) -> int:
        """Returns the last computed theoretical target speed for UI display."""
        return self._last_theoretical_target if self._last_theoretical_target != INIT_APPLIED_PERCENTAGE else 0

    def update_curves(self, cpu_curve: FanTable, gpu_curve: FanTable):
        """
        Updates the fan curve data used for interpolation.
        Clears the old interpolator cache so it is recreated on next use.
        """
        self._cpu_curve_data = self._validate_and_sort(cpu_curve)
        self._gpu_curve_data = self._validate_and_sort(gpu_curve)
        self._cpu_interpolator = self._create_interpolator(self._cpu_curve_data, "CPU")
        self._gpu_interpolator = self._create_interpolator(self._gpu_curve_data, "GPU")
        self.reset_state()

    def update_auto_settings(self, profile: ProfileState):
        """Kept as a no-op so the appearance_changed connection does not break. The ramp no longer reads the step setting."""
        return

    def reset_state(self):
        """Resets the theoretical target cache. The fan ramp flag is reset by AppServices at the call site."""
        self._last_theoretical_target = INIT_APPLIED_PERCENTAGE

    def _validate_and_sort(self, table: FanTable) -> FanTable:
        """Sorts the table by temperature and ensures data points are basically valid."""
        if not isinstance(table, list): return []
        valid_points = [p for p in table if isinstance(p, list) and len(p) == 2 and all(isinstance(x, (int, float)) for x in p)]
        return sorted(valid_points, key=lambda x: x[0])

    def _create_interpolator(self, table: FanTable, curve_name: str) -> Optional[PchipInterpolator]:
        """Creates a PCHIP interpolator object from the fan table."""
        if len(table) < MIN_POINTS_FOR_INTERPOLATION: return None
        
        # Handle points with identical temperatures: keep the higher speed for safety
        unique_temps_map: Dict[float, float] = {}
        for t, s in table:
            if t not in unique_temps_map or s > unique_temps_map[t]:
                unique_temps_map[t] = s
        
        if len(unique_temps_map) < MIN_POINTS_FOR_INTERPOLATION: return None
        
        unique_temps = sorted(unique_temps_map.keys())
        unique_speeds = [unique_temps_map[t] for t in unique_temps]
        
        try:
            return PchipInterpolator([float(t) for t in unique_temps], [float(s) for s in unique_speeds], extrapolate=False)
        except Exception as e:
            print(f"Error creating PCHIP interpolator for {curve_name}: {e}", file=sys.stderr)
            return None

    def _linear_interpolate(self, temperature: float, table: FanTable) -> float:
        """Fallback: simple linear interpolation."""
        temps = [float(p[0]) for p in table]
        speeds = [float(p[1]) for p in table]
        return interp(temperature, temps, speeds)

    def _interpolate_single_curve(self, temperature: float, table: FanTable, interpolator: Optional[PchipInterpolator]) -> int:
        """Computes the target speed for a single curve, preferring PCHIP with linear interpolation as fallback."""
        if not table: return MIN_FAN_PERCENT
        if temperature == TEMP_READ_ERROR_VALUE: return MIN_FAN_PERCENT

        min_temp_curve, min_speed_curve = table[0]
        max_temp_curve, max_speed_curve = table[-1]

        # Handle temperatures outside the curve range
        if temperature <= min_temp_curve: return int(min_speed_curve)
        if temperature >= max_temp_curve: return int(max_speed_curve)

        interp_speed = 0.0
        if interpolator:
            try:
                interp_speed = interpolator(temperature)
            except Exception:
                interp_speed = self._linear_interpolate(temperature, table)
        else:
            interp_speed = self._linear_interpolate(temperature, table)

        final_speed = cast(float, interp_speed)
        clipped_speed = cast(float, clip(final_speed, MIN_FAN_PERCENT, MAX_FAN_PERCENT))
        return int(round(clipped_speed))

    def _calculate_theoretical_target(self, cpu_temp: float, gpu_temp: float) -> int:
        """Computes the theoretical target fan speed from the current temperatures and curves."""
        cpu_target = self._interpolate_single_curve(cpu_temp, self._cpu_curve_data, self._cpu_interpolator)
        gpu_target = self._interpolate_single_curve(gpu_temp, self._gpu_curve_data, self._gpu_interpolator)
        # The final target is the higher of the CPU and GPU targets to ensure sufficient cooling
        return max(cpu_target, gpu_target)

    def theoretical_target(self, cpu_temp: float, gpu_temp: float) -> int:
        """Returns the curve's theoretical target. No stepping, no hysteresis."""
        target = self._calculate_theoretical_target(cpu_temp, gpu_temp)
        self._last_theoretical_target = target
        return target