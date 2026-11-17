# -*- coding: utf-8 -*-
"""
一个纯粹的、无副作用的计算引擎，负责基于温度曲线计算目标风扇速度。
此类维护自动模式的内部状态（如迟滞和当前目标），但不执行任何硬件I/O。
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

# 类型提示
FanTable = List[List[int]] # [[温度, 速度], ...]

class AutoTemperatureController(QObject):
    """
    根据给定的温度和风扇曲线，计算并平滑地调整风扇速度。
    这是一个无I/O的状态计算器，由AppServices驱动。
    """
    def __init__(self):
        super().__init__()
        # --- 内部插值器和曲线数据 ---
        self._cpu_interpolator: Optional[PchipInterpolator] = None
        self._gpu_interpolator: Optional[PchipInterpolator] = None
        self._cpu_curve_data: FanTable = []
        self._gpu_curve_data: FanTable = []

        # --- 运行时状态变量 ---
        self._last_theoretical_target: int = INIT_APPLIED_PERCENTAGE

    def get_last_theoretical_target(self) -> int:
        """返回上次计算的理论目标速度以供UI显示。"""
        return self._last_theoretical_target if self._last_theoretical_target != INIT_APPLIED_PERCENTAGE else 0

    def update_curves(self, cpu_curve: FanTable, gpu_curve: FanTable):
        """
        更新用于插值的风扇曲线数据。
        这会清除旧的插值器缓存，以便在下次需要时重新创建。
        """
        self._cpu_curve_data = self._validate_and_sort(cpu_curve)
        self._gpu_curve_data = self._validate_and_sort(gpu_curve)
        self._cpu_interpolator = self._create_interpolator(self._cpu_curve_data, "CPU")
        self._gpu_interpolator = self._create_interpolator(self._gpu_curve_data, "GPU")
        self.reset_state()

    def reset_state(self):
        """重置理论目标缓存。风扇斜坡标志由 AppServices 在调用方重置。"""
        self._last_theoretical_target = INIT_APPLIED_PERCENTAGE

    def _validate_and_sort(self, table: FanTable) -> FanTable:
        """按温度对表进行排序并确保数据点基本有效。"""
        if not isinstance(table, list): return []
        valid_points = [p for p in table if isinstance(p, list) and len(p) == 2 and all(isinstance(x, (int, float)) for x in p)]
        return sorted(valid_points, key=lambda x: x[0])

    def _create_interpolator(self, table: FanTable, curve_name: str) -> Optional[PchipInterpolator]:
        """使用PCHIP算法从风扇表中创建插值器对象。"""
        if len(table) < MIN_POINTS_FOR_INTERPOLATION: return None
        
        # 处理具有相同温度值的点：保留速度较高的点以确保安全
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
        """作为后备方案执行简单的线性插值。"""
        temps = [float(p[0]) for p in table]
        speeds = [float(p[1]) for p in table]
        return interp(temperature, temps, speeds)

    def _interpolate_single_curve(self, temperature: float, table: FanTable, interpolator: Optional[PchipInterpolator]) -> int:
        """为单个曲线计算目标速度，优先使用PCHIP，并提供线性插值作为后备。"""
        if not table: return MIN_FAN_PERCENT
        if temperature == TEMP_READ_ERROR_VALUE: return MIN_FAN_PERCENT

        min_temp_curve, min_speed_curve = table[0]
        max_temp_curve, max_speed_curve = table[-1]

        # 处理超出曲线范围的温度
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
        """根据当前温度和曲线计算出理论上的目标风扇速度。"""
        cpu_target = self._interpolate_single_curve(cpu_temp, self._cpu_curve_data, self._cpu_interpolator)
        gpu_target = self._interpolate_single_curve(gpu_temp, self._gpu_curve_data, self._gpu_interpolator)
        # 最终目标取CPU和GPU目标中较高的一个，以确保充分散热
        return max(cpu_target, gpu_target)

    def theoretical_target(self, cpu_temp: float, gpu_temp: float) -> int:
        """返回曲线理论目标。不步进、不迟滞。"""
        target = self._calculate_theoretical_target(cpu_temp, gpu_temp)
        self._last_theoretical_target = target
        return target