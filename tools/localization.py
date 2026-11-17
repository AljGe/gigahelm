# -*- coding: utf-8 -*-
"""
处理应用的国际化(i18n)。

这里就是唯一的字符串来源：没有 JSON 语言文件，没有磁盘 IO。
新增字符串只需要同时添加到下面两个字典里，`tests.test_localization`
会强制两个字典的键集合完全一致。
"""

from typing import Dict
from config.settings import DEFAULT_LANGUAGE, KNOWN_LANGUAGES

# ==============================================================================
# 默认翻译字典
# ==============================================================================

# --- 默认英文翻译 ---
DEFAULT_ENGLISH_TRANSLATIONS: Dict[str, str] = {
    # 窗口和UI元素
    "window_title": "Gigahelm",
    "mode_bios": "BIOS",
    "mode_auto": "Auto",
    "mode_custom": "Custom",
    "custom_speed_label": "Custom Speed:",
    "charge_threshold_label": "Charge Limit:",
    "cpu_curve_button": "CPU",
    "gpu_curve_button": "GPU",
    "reset_curve_button": "Reset",
    "controller_notice_title": "Controller notice",
    "start_on_boot_label": "Start on Boot",
    "notifications_label": "Action notifications",
    "notifications_tooltip": "Balloon confirmations when switching profiles or charge limits. Fault and thermal alerts always show.",
    "ui_refresh_label": "UI refresh",
    "ui_refresh_tooltip": "How often the dashboard repaints temperatures and fan speeds.",
    "percent_unit": "%",
    "rpm_unit": " RPM",
    "celsius_unit": "°C",
    "rename_button": "Rename",
    "delete_button": "Delete",

    # 仪表盘卡片
    "card_fan_title": "Fan control",
    "card_fan_desc": "Choose how the fans respond to temperature.",
    "card_battery_title": "Battery care",
    "card_battery_desc": "Cap the charge level to slow battery wear.",
    "curve_card_title": "Fan curves",
    "stat_cpu_caption": "CPU",
    "stat_gpu_caption": "GPU",
    "stat_fan_caption": "Fan output",
    "stat_battery_caption": "Battery",
    "stat_gpu_asleep": "asleep",

    # 工具提示
    "curve_point_tooltip": "Temp: {temp}°C, Speed: {speed}%",
    # 添加缺失的翻译键并提供有用的提示
    "canvas_tooltip": "Double-click empty space to add a point.\nRight-click a point to delete it.\nSafety floors: 80°C→40%, 85°C→60%, 90°C→80%.\nHollow ring: EMA-smoothed operating point.\nDotted crosshairs: live CPU/GPU sensor position.",
    "legend_cpu_curve": "CPU curve",
    "legend_gpu_curve": "GPU curve",
    "legend_ema_point": "EMA operating point",
    "legend_safety_floor": "Safety floor",
    "curve_no_room_for_point": "No free temperature slot left for another point",
    "profile_chip_tooltip": "Click to activate. Right-click for more actions.",
    "add_profile_tooltip": "Add a new profile",
    "start_on_boot_tooltip": "Automatically start with Windows via Task Scheduler.",
    "ff_checkbox_label": "GPU power feed-forward",
    "ff_checkbox_tooltip": "Auto mode only: lift the GPU fan curve by predicted GPU heat (NVML power) so fans react before the heatsink warms up.",
    "fan_mode_tooltip": "Select the fan control mode.\nBIOS: Control by hardware\nAuto: Control by temperature curve\nCustom: Fixed fan speed",
    "custom_fan_speed_tooltip": "Set a fixed fan speed for Custom mode.\nThe EC idles the fans below raw duty 57 (25%): requests of 1-24% all drive 25%, and the display shows the effective duty.",
    "charge_policy_tooltip": "Select the battery charging policy.\nBIOS: Default hardware behavior\nCustom: Limit charging to a specific threshold",
    "charge_threshold_tooltip": "Set the maximum charge level for Custom policy.",
    "reset_curve_tooltip": "Reset the currently active curve to its default points.",
    "undo_curve_tooltip": "Undo the last curve edit (Ctrl+Z)",
    "redo_curve_tooltip": "Re-apply the last undone curve edit (Ctrl+Shift+Z)",
    "editable_label_tooltip": "Double-click to enter a value manually.",
    "maximize_button_tooltip": "Maximize",
    "restore_button_tooltip": "Restore",

    # 配置文件菜单
    "profile_menu_activate": "Activate",
    "profile_menu_save": "Save current settings here",
    "profile_menu_manage": "Rename or delete…",
    "profile_overwrite_confirm_title": "Overwrite profile",
    "profile_overwrite_confirm_msg": "Replace the saved settings of '{name}' with the current settings?",

    # 主题
    "theme_mode_system": "System",
    "theme_mode_dark": "Dark",
    "theme_mode_light": "Light",
    "theme_button_tooltip": "Theme: {mode} — click to change",
    "theme_changed_status": "Theme: {mode}",

    # 状态消息
    "initializing": "Initializing...",
    "paused": "Paused (Hidden)",
    "saving_config": "Config saved.",
    "shutting_down": "Shutting down...",
    "profile_saved_message": "Settings saved to '{profile_name}'.",
    "value_not_available": "---",
    "fan_unavailable_text": "EC controls the fans",
    "fan_label_cpu": "CPU fan",
    "fan_label_gpu": "GPU fan",
    "fan_stall_warning": "Fan stall: {fans} reports 0 RPM at {duty}% duty.",
    "emergency_recovered": "Emergency fan speed (80%) is still latched. Select a fan mode to take control.",
    "config_save_error": "Could not save the config: {error}",
    "config_corrupt_recovered": "The config was corrupt; defaults were loaded. A copy was kept at {path}.",
    "curve_shared_duty_tooltip": "CPU and GPU curves share one duty: the higher target wins.",
    "fan_display_auto_format": "Curve target {target}% · EC {raw}",
    "fan_display_custom_format": "Fixed speed · EC {raw}",
    "fan_display_bios_format": "Hardware controlled",
    "battery_display_format": "Health {health}% · {cycles} cycles",
    "battery_health_tooltip": "Battery health = Windows full-charge capacity / design capacity. The EC health code is not a percentage.",
    "charge_readback_mismatch": "Charge limit readback {actual}% != requested {requested}%",
    "charge_watch_reapplied": "Battery charge setting drifted from profile; reapplied",
    "sustained_hot_alert": "Sustained high temperature ({temps}) - check airflow and cooling",
    "tray_menu_profiles": "Profiles",
    "tray_menu_charge_limit": "Charge limit",
    "power_source_off": "Off",
    "power_source_ac_label": "On AC use:",
    "power_source_battery_label": "On battery use:",

    # 状态卡片页脚
    "stat_fan_footer_format": "Target {target}% · EC {raw}",
    "stat_battery_footer_format": "Health {health}% · {cycles} cycles",

    # 错误和对话框
    "wmi_init_error_title": "WMI Error",
    "wmi_init_error_msg": "Failed to initialize WMI.\nEnsure Gigabyte drivers are installed.\n\nError: {error}",
    "wmi_error": "WMI Error",
    "temp_error": "ERR",
    "rpm_error": "ERR",
    "rename_profile_title": "Manage Profile",
    "rename_profile_label": "Enter a new name for '{old_name}':",
    "delete_profile_title": "Delete Profile",
    "delete_profile_confirm_msg": "Are you sure you want to permanently delete the profile '{name}'?",
    "add_profile_title": "Add New Profile",
    "add_profile_label": "Enter name for the new profile:",
    "add_profile_error_title": "Invalid Name",
    "add_profile_empty_name_error": "Profile name cannot be empty.",
    "add_profile_duplicate_name_error": "A profile named '{name}' already exists.",
    "elevation_error_title": "Elevation Error",
    "elevation_error_msg": "Failed to elevate privileges. Please run as administrator.",
    "task_scheduler_error_msg": "Task Scheduler failed.\nCommand: {command}\n\nError: {error}",
    "unhandled_exception_title": "Unhandled Exception",
    "single_instance_error_title": "Application Already Running",
    "single_instance_error_msg": "Another instance of {app_name} is already running.\nActivating the existing window.",
    "cli_action_done_title": "Command Applied",
    "cli_action_failed_title": "Command Failed",
    "cli_profile_not_found": "Profile '{profile}' was not found. Please create it first.",
    "cli_profile_applied": "Activated profile '{profile}'.",
    "cli_policy_applied": "Applied charge policy '{policy}'.",
    "cli_profile_and_policy_applied": "Activated '{profile}' and applied charge policy '{policy}'.",
    "cli_no_action": "No command action was provided.",

    # 托盘菜单
    "tray_menu_show_hide": "Show / Hide",
    "tray_menu_quit": "Quit",
    "tray_still_running_title": "Still running",
    "tray_still_running_msg": "Closed to the tray - fan control keeps running. Click the tray icon to reopen the window.",

    # 语言显示名称
    "lang_display_name_en": "English",
    "lang_display_name_zh": "中文",

    # UX pass: minimum-curve notice, disabled-FF reason, apply failures,
    # unit strings that were previously hardcoded
    "curve_min_points_reached": "A curve needs at least {n} points",
    "ff_disabled_not_auto": "Only available in Auto mode",
    "fan_mode_apply_failed": "Fan mode '{mode}' rejected; previous mode kept",
    "refresh_ms": "{ms} ms",
    "tray_charge_percent": "{n} %",
}

# --- 默认中文翻译 ---
DEFAULT_CHINESE_TRANSLATIONS: Dict[str, str] = {
    # 窗口和UI元素
    "window_title": "Gigahelm",
    "mode_bios": "BIOS",
    "mode_auto": "自动",
    "mode_custom": "自定义",
    "custom_speed_label": "自定义速度:",
    "charge_threshold_label": "充电上限:",
    "cpu_curve_button": "CPU",
    "gpu_curve_button": "GPU",
    "reset_curve_button": "重置",
    "controller_notice_title": "控制器通知",
    "start_on_boot_label": "开机启动",
    "notifications_label": "操作通知",
    "notifications_tooltip": "切换配置方案或充电上限时的气泡确认。故障与高温警报始终显示。",
    "ui_refresh_label": "界面刷新",
    "ui_refresh_tooltip": "仪表盘刷新温度与风扇转速的频率。",
    "percent_unit": "%",
    "rpm_unit": " RPM",
    "celsius_unit": "°C",
    "rename_button": "重命名",
    "delete_button": "删除",

    # 仪表盘卡片
    "card_fan_title": "风扇控制",
    "card_fan_desc": "选择风扇如何响应温度。",
    "card_battery_title": "电池养护",
    "card_battery_desc": "限制充电上限，延缓电池老化。",
    "curve_card_title": "风扇曲线",
    "stat_cpu_caption": "CPU",
    "stat_gpu_caption": "GPU",
    "stat_fan_caption": "风扇输出",
    "stat_battery_caption": "电池",
    "stat_gpu_asleep": "休眠中",

    # 工具提示
    "curve_point_tooltip": "温度: {temp}°C, 速度: {speed}%",
    # 添加缺失的翻译键并提供有用的提示
    "canvas_tooltip": "双击空白区域以添加点。\n右键单击一个点以删除它。\n安全下限：80°C→40%，85°C→60%，90°C→80%。\n空心圆环：EMA 平滑后的工作点。\n虚线十字线：CPU/GPU 实时传感器位置。",
    "legend_cpu_curve": "CPU 曲线",
    "legend_gpu_curve": "GPU 曲线",
    "legend_ema_point": "EMA 工作点",
    "legend_safety_floor": "安全下限",
    "curve_no_room_for_point": "已无可用于新点的空闲温度",
    "profile_chip_tooltip": "左键启用，右键更多操作。",
    "add_profile_tooltip": "添加新配置文件",
    "start_on_boot_tooltip": "通过任务计划程序随 Windows 自动启动。",
    "ff_checkbox_label": "GPU 功率前馈",
    "ff_checkbox_tooltip": "仅自动模式：根据 GPU 功率预测发热并提前抬高 GPU 风扇曲线，让风扇在散热器变热之前响应。",
    "fan_mode_tooltip": "选择风扇控制模式。\nBIOS: 由硬件控制\n自动: 根据温度曲线控制\n自定义: 固定风扇速度",
    "custom_fan_speed_tooltip": "为自定义模式设置一个固定的风扇速度。\nEC 在原始占空比 57（25%）以下无法维持风扇：1-24% 的请求都会以 25% 运行，显示值为实际生效的占空比。",
    "charge_policy_tooltip": "选择电池充电策略。\nBIOS: 默认硬件行为\n自定义: 将充电限制在特定阈值",
    "charge_threshold_tooltip": "为自定义策略设置最大充电水平。",
    "reset_curve_tooltip": "将当前活动的曲线重置为其默认点。",
    "undo_curve_tooltip": "撤销上一次曲线编辑（Ctrl+Z）",
    "redo_curve_tooltip": "重新应用已撤销的曲线编辑（Ctrl+Shift+Z）",
    "editable_label_tooltip": "双击以手动输入数值。",
    "maximize_button_tooltip": "最大化",
    "restore_button_tooltip": "还原",

    # 配置文件菜单
    "profile_menu_activate": "启用",
    "profile_menu_save": "保存当前设置到此处",
    "profile_menu_manage": "重命名或删除…",
    "profile_overwrite_confirm_title": "覆盖配置方案",
    "profile_overwrite_confirm_msg": "用当前设置替换 '{name}' 的已保存设置吗？",

    # 主题
    "theme_mode_system": "跟随系统",
    "theme_mode_dark": "深色",
    "theme_mode_light": "浅色",
    "theme_button_tooltip": "主题：{mode} — 点击切换",
    "theme_changed_status": "主题：{mode}",

    # 状态消息
    "initializing": "正在初始化...",
    "paused": "已暂停 (最小化)",
    "saving_config": "配置已保存。",
    "shutting_down": "正在关闭...",
    "profile_saved_message": "设置已保存至 '{profile_name}'。",
    "value_not_available": "---",
    "fan_unavailable_text": "EC 接管风扇控制",
    "fan_label_cpu": "CPU 风扇",
    "fan_label_gpu": "GPU 风扇",
    "fan_stall_warning": "风扇停转：{fans} 在 {duty}% 占空比下报告 0 RPM。",
    "emergency_recovered": "紧急风扇转速（80%）仍保持锁定。请选择风扇模式以重新接管。",
    "config_save_error": "无法保存配置：{error}",
    "config_corrupt_recovered": "配置文件损坏，已加载默认值。副本保存在 {path}。",
    "curve_shared_duty_tooltip": "CPU 与 GPU 曲线共用一个占空比：取较高的目标值。",
    "fan_display_auto_format": "曲线目标 {target}% · EC {raw}",
    "fan_display_custom_format": "固定转速 · EC {raw}",
    "fan_display_bios_format": "硬件控制",
    "battery_display_format": "健康 {health}% · {cycles} 次循环",
    "battery_health_tooltip": "电池健康 = Windows 满电容量 / 设计容量。EC 的健康值是状态码，不是百分比。",
    "charge_readback_mismatch": "充电上限回读 {actual}% 与请求 {requested}% 不一致",
    "charge_watch_reapplied": "电池充电设置与配置不一致，已重新应用",
    "sustained_hot_alert": "持续高温（{temps}）—— 请检查散热与风道",
    "tray_menu_profiles": "配置方案",
    "tray_menu_charge_limit": "充电上限",
    "power_source_off": "关闭",
    "power_source_ac_label": "接电源时使用：",
    "power_source_battery_label": "用电池时使用：",

    # 状态卡片页脚
    "stat_fan_footer_format": "目标 {target}% · EC {raw}",
    "stat_battery_footer_format": "健康 {health}% · {cycles} 次循环",

    # 错误和对话框
    "wmi_init_error_title": "WMI 错误",
    "wmi_init_error_msg": "WMI 初始化失败。\n请确保已安装技嘉驱动程序。\n\n错误: {error}",
    "wmi_error": "WMI 错误",
    "temp_error": "错误",
    "rpm_error": "错误",
    "rename_profile_title": "管理配置文件",
    "rename_profile_label": "为 '{old_name}' 输入新名称:",
    "delete_profile_title": "删除配置文件",
    "delete_profile_confirm_msg": "您确定要永久删除配置文件 '{name}' 吗？",
    "add_profile_title": "添加新配置文件",
    "add_profile_label": "为新配置文件输入名称:",
    "add_profile_error_title": "无效名称",
    "add_profile_empty_name_error": "配置文件名称不能为空。",
    "add_profile_duplicate_name_error": "名为 '{name}' 的配置文件已存在。",
    "elevation_error_title": "提权失败",
    "elevation_error_msg": "无法提升权限。请以管理员身份运行。",
    "task_scheduler_error_msg": "任务计划程序失败。\n命令: {command}\n\n错误: {error}",
    "unhandled_exception_title": "未处理的异常",
    "single_instance_error_title": "应用程序已在运行",
    "single_instance_error_msg": "{app_name} 的另一个实例已在运行。\n正在激活现有窗口。",
    "cli_action_done_title": "命令已应用",
    "cli_action_failed_title": "命令失败",
    "cli_profile_not_found": "未找到配置文件 '{profile}'。请先创建它。",
    "cli_profile_applied": "已激活配置文件 '{profile}'。",
    "cli_policy_applied": "已应用充电策略 '{policy}'。",
    "cli_profile_and_policy_applied": "已激活 '{profile}' 并应用充电策略 '{policy}'。",
    "cli_no_action": "未提供可执行的命令动作。",

    # 托盘菜单
    "tray_menu_show_hide": "显示 / 隐藏",
    "tray_menu_quit": "退出",
    "tray_still_running_title": "仍在运行",
    "tray_still_running_msg": "已最小化到托盘——风扇控制仍在运行。点击托盘图标可重新打开窗口。",

    # 语言显示名称
    "lang_display_name_en": "English",
    "lang_display_name_zh": "中文",

    # UX pass additions
    "curve_min_points_reached": "曲线至少需要 {n} 个点",
    "ff_disabled_not_auto": "仅在自动模式下可用",
    "fan_mode_apply_failed": "风扇模式 '{mode}' 被拒绝；已保持原模式",
    "refresh_ms": "{ms} ms",
    "tray_charge_percent": "{n} %",
}

# ==============================================================================
# 核心翻译逻辑
# ==============================================================================

_translations: Dict[str, Dict[str, str]] = {
    "en": DEFAULT_ENGLISH_TRANSLATIONS,
    "zh": DEFAULT_CHINESE_TRANSLATIONS,
}
_current_language: str = DEFAULT_LANGUAGE


def set_language(lang_code: str):
    global _current_language
    if lang_code in _translations:
        _current_language = lang_code
    else:
        _current_language = DEFAULT_LANGUAGE


def tr(key: str, **kwargs) -> str:
    # 首先尝试获取当前语言的翻译
    lang_dict = _translations.get(_current_language, {})
    translation = lang_dict.get(key)

    # 如果当前语言没有，则回退到硬编码的英文
    if translation is None:
        translation = DEFAULT_ENGLISH_TRANSLATIONS.get(key, key)

    try:
        return translation.format(**kwargs)
    except (KeyError, ValueError):
        return translation


def get_available_languages() -> Dict[str, str]:
    available = {}
    for code in sorted(_translations.keys()):
        display_name_key = f"lang_display_name_{code}"
        display_name = _translations[code].get(display_name_key, KNOWN_LANGUAGES.get(code, code.upper()))
        available[code] = display_name
    return available


def get_current_language() -> str:
    return _current_language
