# -*- coding: utf-8 -*-
"""
一个轻量级、基于QPainter的画布，用于显示和编辑风扇曲线。

绘图对象被缓存到一张背景 pixmap 里，只有曲线/指示器在每次重绘时画出来。
所有颜色来自 `ThemeManager` 的调色板：本控件不再读取 profile 里的颜色设置。
拖拽仍然会自动联动相邻点（速度单调、温度点保持最小间距）。
"""

from .qt import *
from typing import List, Optional, Any, Dict, Tuple, cast

from core.interpolation import PchipInterpolator, linspace, clip, interp
from core.profile_manager import ProfileManager
from config.settings import (
    MAX_TEMP_C, MIN_FAN_PERCENT, MAX_FAN_PERCENT,
    MIN_CURVE_POINTS, MIN_POINTS_FOR_INTERPOLATION, TEMP_READ_ERROR_VALUE,
    DEFAULT_MIN_DISPLAY_TEMP_C
)
from tools.localization import tr
from core.state import AppState, ProfileState
from .theme import ThemeManager
from .tooltip_manager import tooltip_manager

# 类型提示
FanTable = List[List[int]]
CurveType = str  # 'cpu' 或 'gpu'

_PLOT_RADIUS = 10


class LightweightCurveCanvas(QWidget):
    """基于QPainter的交互式风扇曲线编辑画布。"""

    transient_status_signal = Signal(str, int)
    # (curve_type, before_points, after_points) fired once per user edit
    # (drag release, add, delete); after is what was written to the profile.
    edit_committed = Signal(str, list, list)

    def __init__(
        self,
        state: AppState,
        profile_manager: ProfileManager,
        theme: ThemeManager,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.state = state
        self.profile_manager = profile_manager
        self.theme = theme
        self._current_profile: Optional[ProfileState] = None
        self.setMinimumSize(400, 250)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.cpu_curve_data: FanTable = []
        self.gpu_curve_data: FanTable = []
        self.active_curve_type: CurveType = 'cpu'
        self._appearance_settings: Dict[str, Any] = {}
        self._dragging_point_index: Optional[int] = None
        self._drag_before_data: Optional[FanTable] = None
        self._hovered_point_index: Optional[int] = None
        self._plot_area = QRectF()
        self._margins = {'left': 52, 'top': 16, 'right': 16, 'bottom': 34}

        # 缓存绘图对象
        self._background_pixmap: Optional[QPixmap] = None
        self._cached_cpu_curve_poly: Optional[QPolygonF] = None
        self._cached_gpu_curve_poly: Optional[QPolygonF] = None
        self._last_cpu_indicator_region = QRegion()
        self._last_gpu_indicator_region = QRegion()

        self._cpu_interpolator: Optional[PchipInterpolator] = None
        self._gpu_interpolator: Optional[PchipInterpolator] = None

        tooltip_manager.register(self, "canvas_tooltip")
        self._connect_signals()

        self._on_active_profile_changed(self.state.get_active_profile())
        self.set_active_curve(self.state.get_active_curve_type())
        self.update_temp_indicators(self.state.get_cpu_temp(), self.state.get_gpu_temp())
        self.setEnabled(self.state.get_is_fan_control_panel_enabled())

    # ---------------------------------------------------------------- setup ---
    def _connect_signals(self):
        """订阅AppState和ProfileState的信号。"""
        self.state.active_profile_changed.connect(self._on_active_profile_changed)
        self.state.active_curve_type_changed.connect(self.set_active_curve)
        self.state.cpu_temp_changed.connect(lambda t: self.update_temp_indicators(cpu_temp=t))
        self.state.gpu_temp_changed.connect(lambda t: self.update_temp_indicators(gpu_temp=t))
        self.state.is_fan_control_panel_enabled_changed.connect(self.setEnabled)

    @Slot(ProfileState)
    def _on_active_profile_changed(self, profile: Optional[ProfileState]):
        """当活动配置文件改变时，安全地重新连接信号。"""
        if self._current_profile and self._current_profile != profile:
            try:
                self._current_profile.cpu_fan_table_changed.disconnect(self._on_cpu_curve_data_changed)
                self._current_profile.gpu_fan_table_changed.disconnect(self._on_gpu_curve_data_changed)
                self._current_profile.appearance_changed.disconnect(self._on_appearance_changed)
            except (AttributeError, RuntimeError): pass

        self._current_profile = profile
        if not profile: return

        profile.cpu_fan_table_changed.connect(self._on_cpu_curve_data_changed)
        profile.gpu_fan_table_changed.connect(self._on_gpu_curve_data_changed)
        profile.appearance_changed.connect(self._on_appearance_changed)

        self._on_cpu_curve_data_changed(profile.cpu_fan_table)
        self._on_gpu_curve_data_changed(profile.gpu_fan_table)
        self._on_appearance_changed()

    @Slot(list)
    def _on_cpu_curve_data_changed(self, data: FanTable):
        self.cpu_curve_data = self._validate_and_sort(data)
        self._cpu_interpolator = None
        self._recache_curves()
        self.update()

    @Slot(list)
    def _on_gpu_curve_data_changed(self, data: FanTable):
        self.gpu_curve_data = self._validate_and_sort(data)
        self._gpu_interpolator = None
        self._recache_curves()
        self.update()

    @Slot()
    def _on_appearance_changed(self):
        profile = self.state.get_active_profile()
        if profile:
            self._appearance_settings = profile.to_dict()
            self._background_pixmap = None
            self._recache_curves()
            self.update()

    def _get_setting(self, key: str, default_override: Any = None) -> Any:
        return self._appearance_settings.get(key, default_override)

    # ------------------------------------------------------------ geometry ---
    def _update_plot_area(self):
        rect = self.rect()
        self._plot_area = QRectF(
            self._margins['left'], self._margins['top'],
            rect.width() - self._margins['left'] - self._margins['right'],
            rect.height() - self._margins['top'] - self._margins['bottom']
        )

    def _plot_clip_path(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(self._plot_area, _PLOT_RADIUS, _PLOT_RADIUS)
        return path

    def _data_to_widget_coords(self, temp: float, speed: float) -> QPointF:
        min_temp = self._get_setting('min_display_temp_c', DEFAULT_MIN_DISPLAY_TEMP_C)
        temp_range = MAX_TEMP_C - min_temp

        if temp_range <= 0: x_ratio = 0.0
        else: x_ratio = (clip(temp, min_temp, MAX_TEMP_C) - min_temp) / temp_range

        x = self._plot_area.left() + x_ratio * self._plot_area.width()
        y = self._plot_area.bottom() - ((speed - MIN_FAN_PERCENT) / (MAX_FAN_PERCENT - MIN_FAN_PERCENT)) * self._plot_area.height()
        return QPointF(x, y)

    def _widget_to_data_coords(self, point: QPointF) -> Tuple[float, float]:
        min_temp = self._get_setting('min_display_temp_c', DEFAULT_MIN_DISPLAY_TEMP_C)
        temp_range = MAX_TEMP_C - min_temp

        if self._plot_area.width() == 0: temp = min_temp
        else: temp = min_temp + ((point.x() - self._plot_area.left()) / self._plot_area.width()) * temp_range

        speed = MIN_FAN_PERCENT + ((self._plot_area.bottom() - point.y()) / self._plot_area.height()) * (MAX_FAN_PERCENT - MIN_FAN_PERCENT)
        return temp, speed

    def resizeEvent(self, event: QResizeEvent):
        self._update_plot_area()
        self._background_pixmap = None # 尺寸变化时，所有缓存失效
        self._recache_curves()
        super().resizeEvent(event)

    # -------------------------------------------------------------- painting ---
    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._background_pixmap is None or self._background_pixmap.size() != self.size():
            self._background_pixmap = QPixmap(self.size())
            self._background_pixmap.fill(Qt.GlobalColor.transparent)
            bg_painter = QPainter(self._background_pixmap)
            bg_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._draw_background(bg_painter)
            self._draw_grid_and_labels(bg_painter)
            bg_painter.end()

        painter.drawPixmap(0, 0, self._background_pixmap)

        self._draw_single_curve(painter, self.gpu_curve_data, 'gpu', self._cached_gpu_curve_poly)
        self._draw_single_curve(painter, self.cpu_curve_data, 'cpu', self._cached_cpu_curve_poly)

        self._draw_temp_indicators(painter)
        self._draw_indicator_badges(painter)
        self._draw_point_badge(painter)
        painter.end()

    def _draw_background(self, painter: QPainter):
        """The card already paints the outer surface; only the plot area is filled here."""
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.theme.palette().surface_alt))
        painter.drawRoundedRect(self._plot_area, _PLOT_RADIUS, _PLOT_RADIUS)

    def _axis_temperatures(self) -> List[int]:
        min_temp = self._get_setting('min_display_temp_c', DEFAULT_MIN_DISPLAY_TEMP_C)
        aligned = ((min_temp + 9) // 10) * 10
        temps = list(range(aligned, MAX_TEMP_C + 1, 10))
        if min_temp % 10 != 0:
            temps.insert(0, min_temp)
        return temps

    def _draw_grid_and_labels(self, painter: QPainter):
        palette = self.theme.palette()
        grid_color = QColor(palette.grid)
        label_color = QColor(palette.text_muted)
        pen = QPen(grid_color)
        pen.setStyle(Qt.PenStyle.DashLine)

        font = QFont(self.font())
        font.setPointSize(8)
        painter.setFont(font)

        min_temp = self._get_setting('min_display_temp_c', DEFAULT_MIN_DISPLAY_TEMP_C)

        painter.save()
        painter.setClipPath(self._plot_clip_path())
        painter.setPen(pen)
        for speed in range(MIN_FAN_PERCENT, MAX_FAN_PERCENT + 1, 10):
            p = self._data_to_widget_coords(min_temp, speed)
            painter.drawLine(QPointF(self._plot_area.left(), p.y()), QPointF(self._plot_area.right(), p.y()))
        for temp in self._axis_temperatures():
            p = self._data_to_widget_coords(temp, MIN_FAN_PERCENT)
            painter.drawLine(QPointF(p.x(), self._plot_area.top()), QPointF(p.x(), self._plot_area.bottom()))
        painter.restore()

        painter.setPen(label_color)
        for speed in range(MIN_FAN_PERCENT, MAX_FAN_PERCENT + 1, 20):
            p = self._data_to_widget_coords(min_temp, speed)
            painter.drawText(
                QRectF(self._plot_area.left() - 46, p.y() - 10, 42, 20),
                Qt.AlignmentFlag.AlignRight,
                f"{speed}{tr('percent_unit')}",
            )
        for temp in self._axis_temperatures():
            p = self._data_to_widget_coords(temp, MIN_FAN_PERCENT)
            painter.drawText(
                QRectF(p.x() - 20, self._plot_area.bottom() + 5, 40, 20),
                Qt.AlignmentFlag.AlignCenter,
                f"{temp}{tr('celsius_unit')}",
            )

    def _curve_color(self, curve_type: CurveType) -> QColor:
        palette = self.theme.palette()
        return QColor(palette.curve_cpu if curve_type == 'cpu' else palette.curve_gpu)

    def _draw_curve_fill(self, painter: QPainter, cached_poly: Optional[QPolygonF], color: QColor):
        """Gradient wash under the active curve, clipped to the rounded plot."""
        if not cached_poly or cached_poly.count() < 2:
            return
        gradient = QLinearGradient(0, self._plot_area.top(), 0, self._plot_area.bottom())
        top_color = QColor(color)
        top_color.setAlpha(60)
        bottom_color = QColor(color)
        bottom_color.setAlpha(0)
        gradient.setColorAt(0.0, top_color)
        gradient.setColorAt(1.0, bottom_color)

        path = QPainterPath(cached_poly.at(0))
        for index in range(1, cached_poly.count()):
            path.lineTo(cached_poly.at(index))
        path.lineTo(cached_poly.at(cached_poly.count() - 1).x(), self._plot_area.bottom())
        path.lineTo(cached_poly.at(0).x(), self._plot_area.bottom())
        path.closeSubpath()

        painter.save()
        painter.setClipPath(self._plot_clip_path())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)
        painter.restore()

    def _draw_single_curve(self, painter: QPainter, data: FanTable, curve_type: CurveType, cached_poly: Optional[QPolygonF]):
        if not data: return
        is_active = (self.active_curve_type == curve_type)
        base_color = self._curve_color(curve_type)
        line_width = self._get_setting("line_width_active") if is_active else self._get_setting("line_width_inactive")
        alpha = self._get_setting("alpha_active") if is_active else self._get_setting("alpha_inactive")
        point_size = self._get_setting("point_size_active")

        if is_active:
            self._draw_curve_fill(painter, cached_poly, base_color)

        line_color = QColor(base_color)
        line_color.setAlphaF(alpha)
        painter.setPen(QPen(line_color, line_width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if cached_poly:
            painter.drawPolyline(cached_poly)

        if is_active:
            ring_pen = QPen(QColor(self.theme.palette().surface), 2)
            for i, p_data in enumerate(data):
                p_widget = self._data_to_widget_coords(p_data[0], p_data[1])
                size = point_size * 1.5 if i == self._hovered_point_index else point_size
                painter.setPen(ring_pen)
                painter.setBrush(QBrush(base_color))
                painter.drawEllipse(p_widget, size / 2, size / 2)

    # -------------------------------------------------------------- badges ----
    def _chip_font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSize(8)
        return font

    def _indicator_entries(self) -> List[Tuple[str, str]]:
        """(text, border colour) for each live CPU/GPU temperature marker."""
        palette = self.theme.palette()
        entries: List[Tuple[str, str]] = []
        pairs = (
            ('cpu', 'CPU', self.state.get_cpu_temp(), palette.dot_cpu),
            ('gpu', 'GPU', self.state.get_gpu_temp(), palette.dot_gpu),
        )
        for curve_type, label, temp, color in pairs:
            if temp == TEMP_READ_ERROR_VALUE or not self.get_curve_data(curve_type):
                continue
            speed = self._get_target_speed_for_indicator(temp, curve_type)
            if speed == -1:
                continue
            text = f"{label} {int(temp)}{tr('celsius_unit')} {speed}{tr('percent_unit')}"
            entries.append((text, color))
        return entries

    def _indicator_badge_rects(self) -> List[Tuple[QRectF, str, str]]:
        metrics = QFontMetrics(self._chip_font())
        rects: List[Tuple[QRectF, str, str]] = []
        y = self._plot_area.top() + 8
        for text, color in self._indicator_entries():
            width = metrics.horizontalAdvance(text) + 16
            height = metrics.height() + 6
            rects.append((QRectF(self._plot_area.right() - 8 - width, y, width, height), text, color))
            y += height + 6
        return rects

    def _badge_area_rect(self) -> QRect:
        """Fixed reserve covering both stacked indicator chips."""
        height = int(2 * (QFontMetrics(self._chip_font()).height() + 6) + 16)
        return QRect(
            int(self._plot_area.right()) - 140,
            int(self._plot_area.top()),
            140,
            height,
        )

    def _draw_indicator_badges(self, painter: QPainter):
        palette = self.theme.palette()
        painter.setFont(self._chip_font())
        for rect, text, border_color in self._indicator_badge_rects():
            painter.setPen(QPen(QColor(border_color), 1))
            painter.setBrush(QColor(palette.surface))
            painter.drawRoundedRect(rect, 6, 6)
            painter.setPen(QColor(palette.text))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_point_badge(self, painter: QPainter):
        index = self._dragging_point_index
        if index is None:
            index = self._hovered_point_index
        if index is None:
            return
        data = self.get_curve_data(self.active_curve_type)
        if index >= len(data):
            return

        palette = self.theme.palette()
        font = self._chip_font()
        metrics = QFontMetrics(font)
        temp, speed = data[index]
        text = tr("curve_point_tooltip", temp=int(temp), speed=int(speed))
        width = metrics.horizontalAdvance(text) + 16
        height = metrics.height() + 8

        anchor = self._data_to_widget_coords(temp, speed)
        x = anchor.x() + 12
        y = anchor.y() - height - 8
        if x + width > self.width() - 4:
            x = anchor.x() - width - 12
        if y < 4:
            y = anchor.y() + 12

        rect = QRectF(x, y, width, height)
        painter.setFont(font)
        painter.setPen(QPen(QColor(palette.border), 1))
        painter.setBrush(QColor(palette.surface))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor(palette.text))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    @Slot(str)
    def set_active_curve(self, curve_type: CurveType):
        if self.active_curve_type != curve_type:
            self.active_curve_type = curve_type
            self._hovered_point_index = None
            self.update()

    # ---------------------------------------------------------- indicators ---
    @Slot()
    def update_temp_indicators(self, cpu_temp: Optional[float] = None, gpu_temp: Optional[float] = None):
        update_region = QRegion()

        if cpu_temp is not None:
            update_region += self._last_cpu_indicator_region
            new_region = self._calculate_indicator_region(cpu_temp, 'cpu')
            update_region += new_region
            self._last_cpu_indicator_region = new_region

        if gpu_temp is not None:
            update_region += self._last_gpu_indicator_region
            new_region = self._calculate_indicator_region(gpu_temp, 'gpu')
            update_region += new_region
            self._last_gpu_indicator_region = new_region

        if not update_region.isEmpty():
            # the stacked top-right chips change with every reading
            update_region += QRegion(self._badge_area_rect())
            self.update(update_region)

    def _calculate_indicator_region(self, temp: float, curve_type: CurveType) -> QRegion:
        if temp == TEMP_READ_ERROR_VALUE or not self.get_curve_data(curve_type):
            return QRegion()

        speed = self._get_target_speed_for_indicator(temp, curve_type)
        min_temp = self._get_setting('min_display_temp_c', DEFAULT_MIN_DISPLAY_TEMP_C)

        if speed != -1 and temp >= min_temp:
            p = self._data_to_widget_coords(temp, speed)
            horiz_rect = QRect(int(self._plot_area.left()), int(p.y() - 1), int(p.x() - self._plot_area.left()), 3)
            vert_rect = QRect(int(p.x() - 1), int(p.y()), 3, int(self._plot_area.bottom() - p.y()))
            return QRegion(horiz_rect).united(QRegion(vert_rect))

        return QRegion()

    def _draw_temp_indicators(self, painter: QPainter):
        palette = self.theme.palette()
        cpu_temp = self.state.get_cpu_temp()
        gpu_temp = self.state.get_gpu_temp()
        if cpu_temp != TEMP_READ_ERROR_VALUE and self.cpu_curve_data:
            speed = self._get_target_speed_for_indicator(cpu_temp, 'cpu')
            self._draw_indicator_lines(painter, cpu_temp, speed, QColor(palette.dot_cpu))
        if gpu_temp != TEMP_READ_ERROR_VALUE and self.gpu_curve_data:
            speed = self._get_target_speed_for_indicator(gpu_temp, 'gpu')
            self._draw_indicator_lines(painter, gpu_temp, speed, QColor(palette.dot_gpu))

    def _draw_indicator_lines(self, painter: QPainter, temp: float, speed: int, color: QColor):
        min_temp = self._get_setting('min_display_temp_c', DEFAULT_MIN_DISPLAY_TEMP_C)
        if speed != -1 and temp >= min_temp:
            p = self._data_to_widget_coords(temp, speed)
            pen = QPen(color, 1.5, Qt.PenStyle.DotLine); painter.setPen(pen)
            painter.drawLine(int(p.x()), int(self._plot_area.bottom()), int(p.x()), int(p.y()))
            painter.drawLine(int(self._plot_area.left()), int(p.y()), int(p.x()), int(p.y()))

    def _get_target_speed_for_indicator(self, temperature: float, curve_type: CurveType) -> int:
        table = self.get_curve_data(curve_type)
        if not table: return -1
        interpolator = self._get_cached_interpolator(curve_type)
        if interpolator:
            return int(round(cast(float, clip(cast(float, interpolator(temperature)), MIN_FAN_PERCENT, MAX_FAN_PERCENT))))
        return int(round(interp(temperature, [p[0] for p in table], [p[1] for p in table])))

    # --------------------------------------------------------------- curves ---
    def _get_cached_interpolator(self, curve_type: CurveType) -> Optional[PchipInterpolator]:
        attr_name = f"_{curve_type}_interpolator"
        if getattr(self, attr_name): return getattr(self, attr_name)

        table = self.get_curve_data(curve_type)
        unique_points = sorted(list({tuple(p) for p in table}), key=lambda x: x[0])
        if len(unique_points) < MIN_POINTS_FOR_INTERPOLATION: return None

        try:
            interpolator = PchipInterpolator([float(p[0]) for p in unique_points], [float(p[1]) for p in unique_points], extrapolate=False)
            setattr(self, attr_name, interpolator)
            return interpolator
        except Exception: return None

    def _recache_curves(self):
        self._cached_cpu_curve_poly = self._create_curve_polygon(self.cpu_curve_data, 'cpu')
        self._cached_gpu_curve_poly = self._create_curve_polygon(self.gpu_curve_data, 'gpu')

    def _create_curve_polygon(self, data: FanTable, curve_type: CurveType) -> Optional[QPolygonF]:
        if not data: return None
        interpolator = self._get_cached_interpolator(curve_type)
        if not interpolator: return None

        spline_points = self._get_setting("spline_points", 100)
        temps_smooth = linspace(interpolator.x[0], interpolator.x[-1], spline_points)
        speeds_smooth = clip(cast(List[float], interpolator(temps_smooth)), MIN_FAN_PERCENT, MAX_FAN_PERCENT)
        points = [self._data_to_widget_coords(t, s) for t, s in zip(temps_smooth, cast(List[float], speeds_smooth))]
        return QPolygonF(points)

    def get_curve_data(self, curve_type: CurveType) -> FanTable:
        return self.cpu_curve_data if curve_type == 'cpu' else self.gpu_curve_data

    # ----------------------------------------------------------------- i18n ---
    def retranslate_ui(self):
        self._background_pixmap = None
        self.update()

    def on_theme_changed(self):
        """Palette changed: drop the cached background and the cached polygons."""
        self._background_pixmap = None
        self._recache_curves()
        self.update()

    def _validate_and_sort(self, table: FanTable) -> FanTable:
        min_temp = self._get_setting('min_display_temp_c', DEFAULT_MIN_DISPLAY_TEMP_C) if self._appearance_settings else 0
        valid = [[max(min_temp, min(MAX_TEMP_C, p[0])), max(MIN_FAN_PERCENT, min(MAX_FAN_PERCENT, p[1]))] for p in table if isinstance(p, list) and len(p) == 2]
        return sorted(valid, key=lambda x: x[0])

    def _update_curve_in_state(self, curve_type: CurveType, data: FanTable):
        self.profile_manager.set_curve_data(curve_type, data)
        self.transient_status_signal.emit(tr("saving_config"), 2000)

    def _commit_edit(self, curve_type: CurveType, before: Optional[FanTable], after: FanTable):
        """Persist one user edit and announce it for undo history; no-ops
        (no history entry, no write) when nothing actually changed."""
        if before is not None and [list(p) for p in before] == [list(p) for p in after]:
            return
        if before is not None:
            self.edit_committed.emit(curve_type, [list(p) for p in before], [list(p) for p in after])
        self._update_curve_in_state(curve_type, after)

    # ----------------------------------------------------------- interaction ---
    def mousePressEvent(self, event: QMouseEvent):
        pos = event.position()
        if not self._plot_area.contains(pos): return
        point_info = self._get_point_at_event(pos)
        if event.button() == Qt.MouseButton.LeftButton and point_info:
            self._dragging_point_index = point_info[1]
            self._drag_before_data = [list(p) for p in self.get_curve_data(self.active_curve_type)]
        elif event.button() == Qt.MouseButton.RightButton and point_info:
            self._delete_point(*point_info)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        pos = event.position()
        if self._plot_area.contains(pos) and event.button() == Qt.MouseButton.LeftButton and self._get_point_at_event(pos) is None:
            self._add_point(pos)

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()
        if self._dragging_point_index is not None:
            self._drag_point(pos)
        else:
            point_info = self._get_point_at_event(pos)
            new_hover = point_info[1] if point_info else None
            if new_hover != self._hovered_point_index:
                self._hovered_point_index = new_hover
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._dragging_point_index is not None and event.button() == Qt.MouseButton.LeftButton:
            self._dragging_point_index = None
            data = self.get_curve_data(self.active_curve_type)
            data.sort(key=lambda p: p[0])
            before, self._drag_before_data = self._drag_before_data, None
            self._commit_edit(self.active_curve_type, before, data)
            self.update()

    def _get_point_at_event(self, pos: QPointF) -> Optional[Tuple[CurveType, int]]:
        data = self.get_curve_data(self.active_curve_type)
        radius = self._get_setting("curve_point_picker_radius", 8.0)
        for i, p_data in enumerate(data):
            p_widget = self._data_to_widget_coords(p_data[0], p_data[1])
            if (pos.x() - p_widget.x())**2 + (pos.y() - p_widget.y())**2 < radius**2:
                return (self.active_curve_type, i)
        return None

    def _add_point(self, pos: QPointF):
        temp, speed = self._widget_to_data_coords(pos)
        before = [list(p) for p in self.get_curve_data(self.active_curve_type)]
        data = [list(p) for p in before]
        data.append([int(round(temp)), int(round(speed))])
        data.sort(key=lambda p: p[0])
        for i in range(1, len(data)):
            if data[i][1] < data[i-1][1]:
                data[i][1] = data[i-1][1]

        setattr(self, f"_{self.active_curve_type}_interpolator", None)
        self._commit_edit(self.active_curve_type, before, data)
        self.update()

    def _delete_point(self, curve_type: CurveType, index: int):
        data = [list(p) for p in self.get_curve_data(curve_type)]
        if len(data) > MIN_CURVE_POINTS:
            before = data
            del data[index]
            setattr(self, f"_{curve_type}_interpolator", None)
            self._commit_edit(curve_type, before, data)
            self.update()

    def _drag_point(self, pos: QPointF):
        """实现水平和垂直方向的完全自由、智能联动的拖拽。"""
        data = self.get_curve_data(self.active_curve_type)
        idx = self._dragging_point_index
        if idx is None: return

        temp, speed = self._widget_to_data_coords(pos)

        # 1. 应用画布边界限制
        min_temp = self._get_setting('min_display_temp_c', DEFAULT_MIN_DISPLAY_TEMP_C)
        temp = max(min_temp, min(MAX_TEMP_C, temp))
        speed = max(MIN_FAN_PERCENT, min(MAX_FAN_PERCENT, speed))

        # 2. 更新当前点的位置
        data[idx] = [int(round(temp)), int(round(speed))]

        # 3. 实时强制水平和垂直的连锁反应
        self._enforce_temperature_separation_during_drag(data, idx)
        self._enforce_speed_monotonicity_during_drag(data, idx)

        # 4. 更新状态并重绘
        setattr(self, f"_{self.active_curve_type}_interpolator", None)
        self._recache_curves()
        self.update()

    def _enforce_speed_monotonicity_during_drag(self, data: FanTable, dragged_index: int):
        """实时强制风扇速度的单调性（只能增加或持平）。"""
        # 向前连锁（处理向下拖动）
        for i in range(dragged_index, 0, -1):
            if data[i][1] < data[i-1][1]:
                data[i-1][1] = data[i][1]

        # 向后连锁（处理向上拖动）
        for i in range(dragged_index, len(data) - 1):
            if data[i][1] > data[i+1][1]:
                data[i+1][1] = data[i][1]

    def _enforce_temperature_separation_during_drag(self, data: FanTable, dragged_index: int):
        """实时强制温度点的最小间距和顺序。"""
        min_temp = self._get_setting('min_display_temp_c', DEFAULT_MIN_DISPLAY_TEMP_C)

        # 向后连锁（处理向右拖动，推开右边的点）
        for i in range(dragged_index, len(data) - 1):
            if data[i+1][0] <= data[i][0]:
                new_temp = min(MAX_TEMP_C, data[i][0] + 1)
                data[i+1][0] = new_temp

        # 向前连锁（处理向左拖动，推开左边的点）
        for i in range(dragged_index, 0, -1):
            if data[i-1][0] >= data[i][0]:
                new_temp = max(min_temp, data[i][0] - 1)
                data[i-1][0] = new_temp
