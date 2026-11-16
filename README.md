# Gigahelm

Fan and battery control for Gigabyte/Aorus laptops — actively maintained, and a
drop-in replacement for the retired Gigabyte Control Center panels.

> Gigahelm 是一个面向技嘉笔记本的风扇与电池控制工具，持续维护中，可替代已废弃的技嘉控制中心面板。

### Highlights & Features
- **Lightweight & Efficient:** A minimal footprint application with extremely low CPU usage. In BIOS mode it is near-idle when hidden; Auto and Custom keep a 1.5 s safety poll running so the fans cannot be left unattended.
- **Advanced Fan Control:** Go beyond stock settings. Create multiple profiles with custom, smooth fan curves for both CPU and GPU, or set a fixed fan speed for specific tasks. The two fans share the higher of the CPU/GPU targets.
- **Intelligent Curve Editing:** The interactive curve canvas features **smart cascading adjustments**. Drag any point freely, and the rest of the curve will intelligently adjust to maintain a logical, monotonic profile, providing a seamless editing experience.
- **Thermal Guardrails:** Even a deliberately quiet curve cannot drop below 40% at 80 °C, 60% at 85 °C, or 80% at 90 °C. RPM is watched for stalls and reported in the status bar.
- **Battery Health Protection:** Extend your battery's lifespan by setting a custom charge limit (e.g., 80%). Ideal for users who are frequently plugged in. Health is computed from Windows full-charge / design capacity; the EC health value is a status code, not a percentage, and is never rendered as one.
- **Charge Watch & Power-Source Automation:** a 60 s watch re-reads the EC's applied charge policy/limit and reapplies the profile's values when firmware drifts (sleep, EC reset, battery pull) — a dropped limit can no longer silently charge to 100 %. On AC attach/detach the app can switch to a bound profile automatically (Battery care card → "On AC use:" / "On battery use:"), and every resume re-verifies the charge state.
- **Robust & Safe:** A crash runs the 80% emergency setter in a **separate process**; a scheduled watchdog reverts to BIOS if the app is killed or stops updating its heartbeat. Graceful exit hands the fans back to BIOS.
- **Clean & Modern UI:** A responsive, intuitive interface that provides all necessary information and controls without unnecessary clutter.

### Special Thanks
[The alfc project by s-h-a-d-o-w](https://github.com/s-h-a-d-o-w/alfc) for its support and assistance.

## Compatibility

| Model | System | 
|--------------|---------------|
| Aorus 15P XD | Windows 10/11 | 

### What is this project for? What problems does it solve?
Gigahelm is a lightweight yet powerful utility designed for Gigabyte laptops, offering granular control over fan behavior and battery charging. It serves as a high-performance alternative to manufacturer software, addressing key user needs:
- **Limited Fan Control:** Default fan curves are often a one-size-fits-all solution. This tool empowers you to create unlimited profiles with distinct fan curves, perfectly tailored for silent work, balanced daily use, or maximum cooling during intense gaming sessions.
- **Battery Longevity:** Constant charging to 100% degrades battery health over time. This application allows you to set a custom charge limit, significantly prolonging your battery's lifespan.
- **Bloatware Replacement:** It provides a streamlined, resource-friendly alternative to bulky pre-installed software, freeing up system resources while offering superior control.
- **Real-time System Monitoring:** The UI offers an at-a-glance view of critical system metrics, including CPU/GPU temperatures, fan RPMs, and detailed battery status.

### How to Use
1.  **Download & Run:** Download the latest release from the Releases page. No installation is required.
2.  **Administrator Privileges:** The application needs administrator rights to communicate with the hardware via WMI. It will automatically request elevation on startup if needed.
3.  **User Interface:**
    *   **Status cards:** A live row showing CPU/GPU temperature, fan output and battery charge limit, each with a level bar and a detail line (fan RPM, curve target, battery health).
    *   **Fan control card:**
        *   **BIOS:** Returns all fan control to the system's hardware/BIOS.
        *   **Auto:** Automatically adjusts fan speeds based on the active temperature curves.
        *   **Custom:** Locks the fans to a fixed speed percentage set by the slider.
    *   **Battery care card:**
        *   **BIOS:** Uses the manufacturer's default charging behavior (typically charges to 100%).
        *   **Custom:** Enforces the custom maximum charge threshold set by the slider (e.g., 60-100%).
    *   **Fan curves card:**
        *   **CPU/GPU switch:** Toggle between viewing and editing the fan curve for each component.
        *   **Profile chips:** Manage your settings profiles.
            *   **Left-Click:** Activate a profile.
            *   **Right-Click:** Open the actions menu — activate, save the current settings into that profile, or rename/delete it.
        *   **"+" Button:** Create a new profile based on the currently active one.
        *   **Reset:** Resets the currently selected fan curve (CPU or GPU) to its default points.
        *   **Curve Canvas:** The interactive graph for editing fan curves. Drag points to adjust the temperature-to-fan-speed mapping; the curve cascades to stay logical. Double-click empty space to add a point, right-click a point to delete it.
    *   **Theme switch:** The button next to the language selector cycles System → Dark → Light, and the choice is remembered.
    *   **Start on Boot:** The checkbox in the footer starts the application with Windows (via Task Scheduler).
    *   **Language Selector:** Change the application's display language on the fly.
4.  **Tray Icon:** The application minimizes to the system tray to run unobtrusively in the background.
    *   **Left-Click/Double-Click:** Show or hide the main window.
    *   **Right-Click:** Access a context menu to show/hide or quit the application.

### Safety model and limits
- **Control loop:** 1.5 s. Speed-ups apply immediately; speed-downs are capped at 5 percentage points per tick. A 2-point target hysteresis suppresses 1% sensor wobble without parking the fans above the curve.
- **Thermal guards:** at 80 °C the minimum duty is 40%, at 85 °C it is 60%, at 90 °C it is 80% — regardless of the curve.
- **Watchdog:** `Gigahelm BIOS Revert Watchdog` is registered automatically and runs every minute. It reverts to BIOS when no instance is running or the app's `heartbeat.state` is older than 45 s. It skips `bios` and `emergency` states. Legacy `GFBC` watchdog tasks are removed on startup.
- **Crash path:** the 80% emergency setter runs in a separate process, then `last_mode.state` is set to `emergency`. The startup task no longer restarts after a crash, and a manual relaunch keeps the 80% target until a fan mode is selected.
- **Graceful exit:** the app confirms the BIOS handback before writing `last_mode=bios`; if the handback fails, the watchdog retries after the process exits.
- **Known limits:** both fans receive the same duty (the higher CPU/GPU target); RPM is monitored for alerts but is not a closed loop; the raw duty constants are validated on the Aorus 15P family, not every Gigabyte model.

### Command Line Automation (Profiles and Battery Policy)

You can launch the EXE with command-line flags to quickly apply a profile and/or battery policy from shortcuts (`.lnk`), Start menu entries, or PowerToys Command Palette.

Supported flags:

- `--profile "<name>"`: Activate a specific profile by name.
- `--show-mode <show|notify|silent>`:
  - `show`: apply action and bring window to front.
  - `notify`: apply action in background and show tray notification.
  - `silent`: apply action in background without popups.
- `--charge-policy <bios|custom>`: Set battery charging policy.
- `--charge-threshold <60-100>`: Optional, only valid with `--charge-policy custom`.

Notes:

- If profile does not exist, the app always shows an error notification.
- If `--show-mode` is omitted when using action flags, it defaults to `notify`.
- Existing `--minimized` startup behavior remains supported.

Examples:

- `Gigahelm.exe --profile "Gaming" --show-mode notify`
- `Gigahelm.exe --profile "Quiet" --show-mode silent`
- `Gigahelm.exe --charge-policy bios --show-mode notify`
- `Gigahelm.exe --profile "PluggedIn" --charge-policy custom --charge-threshold 80 --show-mode silent`

Power-user shortcut workflow:

1. Create multiple `.lnk` shortcuts with different arguments.
2. Place them in Start Menu folders.
3. Launch by name from PowerToys Command Palette for fast profile switching.

### Scope of Application
This application is primarily designed for **Gigabyte laptops** that expose fan and battery controls through their WMI interface. While it may work on other brands or models with similar WMI implementations, compatibility is not guaranteed. It is built for **Windows operating systems**.

### Target Audience
-   **Gigabyte Laptop Owners:** Seeking a more powerful, efficient, and reliable alternative to the stock control software.
-   **Power Users & Gamers:** Who demand precise control over their system's thermal performance and acoustics.
-   **Users Concerned with Battery Health:** Who want to proactively manage and extend their laptop's battery lifespan.
-   **Minimalists:** Who prefer clean, focused, and resource-friendly utilities.

---

### Advanced Setup: Using Without Gigabyte Control Center

This section is for advanced users who wish to completely uninstall the official Gigabyte Control Center (GCC) but still use this application. The official software is responsible for registering the necessary WMI provider (`acpimof.dll`) with Windows. Without it, this tool cannot communicate with the hardware. The following steps manually register this provider.

**DISCLAIMER: Modifying the Windows Registry is risky and can cause system instability if done incorrectly. It is highly recommended to back up your registry before proceeding.**

1.  **Obtain WMI Provider:** Find the Gigabyte Control Center installer package and extract its contents. Locate the `acpimof.dll` file within the extracted files.
2.  **Place the DLL:** Copy the `acpimof.dll` file to the `C:\Windows\SysWOW64` directory.
3.  **Open Registry Editor:** Press `Win + R`, type `regedit`, and press Enter.
4.  **Navigate to the Key:** In the Registry Editor, navigate to the following path:
    `Computer\HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\WmiAcpi`
5.  **Create String Value:** In the right-hand pane, right-click on an empty space, select `New` -> `String Value`.
6.  **Name the Value:** Name the new value exactly `MofImagePath`.
7.  **Set the Value Data:** Double-click `MofImagePath` and set its value data to the full path of the DLL: `C:\Windows\SysWOW64\acpimof.dll`.
8.  **Reboot:** Restart your computer for the changes to take effect.
9.  **Verify:** After rebooting, run Gigahelm. If it starts without any WMI initialization errors, the configuration was successful.

---

# Gigahelm

### 亮点与特性
- **轻量高效:** 一个占用极小的应用程序，拥有极低的CPU使用率。BIOS 模式下最小化后接近空闲；自动与自定义模式会保留 1.5 秒的安全轮询，避免风扇无人监管。
- **高级风扇控制:** 超越原生设置。为CPU和GPU创建多个配置文件和自定义的平滑风扇曲线，或为特定任务设置固定的风扇转速。两个风扇共用 CPU/GPU 目标中的较高值。
- **智能曲线编辑:** 交互式曲线画布拥有 **“智能联动调整”** 特性。您可以自由拖动任何一个点，曲线上的其他点会自动智能调整，以始终维持逻辑正确的单调曲线，提供无与伦比的流畅编辑体验。
- **温度安全下限:** 即使曲线设置得很安静，80°C 时也不会低于 40%，85°C 时不低于 60%，90°C 时不低于 80%。程序还会监测风扇转速并在状态栏报告停转。
- **电池健康保护:** 通过设置自定义充电上限（例如，80%）来延长电池的生命周期，尤其适用于经常连接电源使用的用户。健康度由 Windows 满电容量/设计容量计算；EC 的健康值是状态码而非百分比，不会被显示成百分比。
- **健壮与安全:** 崩溃时会在**独立进程**中设置 80% 紧急转速；计划任务看门狗会在程序被强杀或心跳停止时把风扇交还 BIOS。正常退出也会交还 BIOS。
- **简洁的现代界面:** 界面响应迅速、直观，提供所有必要的信息和控制选项，没有任何多余的元素。

### 特别感谢
[s-h-a-d-o-w 的 alfc 项目](https://github.com/s-h-a-d-o-w/alfc) 提供的支持与帮助。  

## 兼容性

| 型号 | 系统 | 
|--------------|---------------|
| Aorus 15P XD | Windows 10/11 | 

### 这个项目有什么用？解决什么问题？
Gigahelm 是一个为技嘉笔记本设计的轻量而强大的工具，提供对风扇行为和电池充电的精细化控制。它是一个高性能的官方软件替代品，旨在解决用户的核心痛点：
- **风扇控制受限:** 默认的风扇策略往往是“一刀切”的方案。本工具让您能创建不限数量的配置文件，每个文件都可以有独特的风扇曲线，完美适应静音办公、均衡日用或高强度游戏下的最大散热需求。
- **延长电池寿命:** 长期将电池充电到100%会随时间推移损害电池健康。本应用允许您设定一个自定义的充电上限，从而显著延长电池的生命周期。
- **替代臃肿软件:** 它提供了一个精简、资源友好的替代方案，取代了臃肿的预装软件，在提供更强控制力的同时，释放宝贵的系统资源。
- **实时系统监控:** UI界面让您能一目了然地看到关键系统指标，包括CPU/GPU温度、风扇RPM转速以及详细的电池策略状态。

### 如何使用
1.  **下载与运行:** 从 Releases 页面 下载最新版本的程序。无需安装，直接运行。
2.  **管理员权限:** 本程序需要管理员权限才能通过WMI与硬件通信。如果需要，它会在启动时自动请求提权。
3.  **用户界面:**
    *   **状态卡片:** 顶部一排实时显示CPU/GPU温度、风扇输出与电池充电上限，每张卡片带有进度条和详情行（风扇转速、曲线目标、电池健康）。
    *   **风扇控制卡片:**
        *   **BIOS:** 将所有风扇控制权交还给系统硬件/BIOS。
        *   **自动:** 根据当前激活的温度曲线自动调节风扇转速。
        *   **自定义:** 将风扇锁定在滑块设定的固定转速百分比。
    *   **电池养护卡片:**
        *   **BIOS:** 使用制造商默认的充电行为（通常会充到100%）。
        *   **自定义:** 强制执行由滑块设定的自定义充电上限（例如 60-100%）。
    *   **风扇曲线卡片:**
        *   **CPU/GPU 切换:** 切换查看和编辑对应组件的风扇曲线。
        *   **配置文件标签:** 管理您的配置方案。
            *   **左键单击:** 激活一个配置文件。
            *   **右键单击:** 打开操作菜单 —— 启用、把当前设置保存到该配置，或重命名/删除它。
        *   **“+” 按钮:** 基于当前配置创建一个新的配置文件。
        *   **重置:** 将当前选中的曲线（CPU或GPU）恢复为默认设定。
        *   **曲线画布:** 用于编辑风扇曲线的交互式图表。拖动控制点即可调整温度与转速的对应关系，曲线会自动联动以保持逻辑正确；在空白处双击添加点，右键单击控制点删除。
    *   **主题切换:** 语言选择器旁的按钮按 跟随系统 → 深色 → 浅色 循环，选择会被记住。
    *   **开机启动:** 页脚的复选框通过“任务计划程序”实现开机自启。
    *   **语言选择器:** 在标题栏可以随时切换程序的显示语言。
4.  **托盘图标:** 程序会最小化到系统托盘，在后台安静运行。
    *   **左键单击/双击:** 显示或隐藏主窗口。
    *   **右键单击:** 访问上下文菜单，以显示/隐藏窗口或退出程序。

### 安全模型与限制
- **控制周期:** 1.5 秒。升速立即生效；降速每个周期最多 5 个百分点。2 个百分点的目标迟滞用于抑制 1% 的传感器抖动，但不会让风扇永久停在曲线之上。
- **温度下限:** 80°C 时最低 40%，85°C 时最低 60%，90°C 时最低 80%，与曲线设置无关。
- **看门狗:** 自动注册 `Gigahelm BIOS Revert Watchdog`，每分钟运行一次。没有实例运行，或 `heartbeat.state` 超过 45 秒未更新时，把风扇交还 BIOS；`bios` 与 `emergency` 状态会跳过。启动时会删除旧的 `GFBC` 看门狗任务。
- **崩溃路径:** 80% 紧急设置器在独立进程中运行，成功后把 `last_mode.state` 写为 `emergency`。启动任务不再在崩溃后自动重启；手动重新启动后也会保持 80% 转速，直到用户选择风扇模式。
- **正常退出:** 确认 BIOS 移交成功后才写入 `last_mode=bios`；如果移交失败，进程退出后由看门狗重试。
- **已知限制:** 两个风扇使用同一个占空比（取 CPU/GPU 较高目标）；转速只用于告警，不构成闭环；原始占空比常量在 Aorus 15P 系列上验证，不保证所有技嘉型号。

### 适用范围
此应用程序主要为支持通过WMI接口进行风扇和电池控制的**技嘉笔记本电脑**设计。尽管它可能也适用于具有相似WMI实现的其他品牌或型号，但这并非官方支持或保证。本程序仅在**Windows操作系统**上运行。

### 适用人群
-   **技嘉笔记本用户:** 正在寻找比官方控制软件更强大、高效、可靠的替代方案的用户。
-   **高级用户和游戏玩家:** 渴望对系统的散热性能和噪音水平进行精确控制的用户。
-   **关注电池健康的用户:** 希望主动管理并延长其笔记本电池寿命的用户。
-   **极简主义者:** 偏爱功能专注、资源友好型工具的用户。

---

### 高级设置：在无官方软件环境下使用

本节适用于希望**完全卸载**技嘉官方控制中心（GCC）但仍要使用本程序的高级用户。官方软件负责向Windows系统注册必要的WMI硬件接口库（`acpimof.dll`）。如果缺少这一步，本工具将无法与硬件通信。以下步骤将手动完成此注册过程。

**免责声明：修改Windows注册表存在风险，操作不当可能导致系统不稳定。强烈建议在继续操作前备份您的注册表。**

1.  **获取WMI接口库：** 找到技嘉控制中心的安装包并解压其内容。在解压出的文件中找到 `acpimof.dll`。
2.  **放置DLL文件：** 将 `acpimof.dll` 文件复制到 `C:\Windows\SysWOW64` 目录下。
3.  **打开注册表编辑器：** 按下 `Win + R` 键，输入 `regedit`，然后按回车。
4.  **导航到指定路径：** 在注册表编辑器中，定位到以下路径：
    `计算机\HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\WmiAcpi`
5.  **创建字符串值：** 在右侧窗格的空白处单击右键，选择 `新建` -> `字符串值`。
6.  **命名键值：** 将新创建的值精确命名为 `MofImagePath`。
7.  **设置键值数据：** 双击 `MofImagePath`，并将其“数值数据”设置为DLL文件的完整路径：`C:\Windows\SysWOW64\acpimof.dll`。
8.  **重启电脑：** 重启计算机以使更改生效。
9.  **验证：** 重启后，运行 Gigahelm。如果程序启动时没有出现任何WMI初始化错误，则代表配置成功。
