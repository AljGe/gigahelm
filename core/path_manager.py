import os

class PathManager:
    """
    Centralized path manager that builds and stores every file and directory
    path the application needs. This class is the single source of truth for
    all canonical paths and is injected by main.py at startup.
    """
    def __init__(self, base_dir: str, executable_path: str, main_script_path: str):
        """
        Initialize the path manager with the application's canonical paths.

        Args:
            base_dir (str): Application root directory (BASE_DIR).
            executable_path (str): Real path of the launcher .exe or python.exe.
            main_script_path (str): Real path of the main script main.py (only valid in script mode).
        """
        self.base_dir = base_dir
        self.executable_path = executable_path
        self.main_script_path = main_script_path

        # --- Core config files ---
        self.control_config = os.path.join(self.base_dir, 'control_config.json')
        self.languages = os.path.join(self.base_dir, 'languages.json')

        # --- GUI files ---
        self.style_qss = os.path.join(self.base_dir, 'gui', 'style.qss')
        
        # --- Icon ---
        self.app_icon = os.path.join(self.base_dir, 'app_icon.ico')

        # --- Task Scheduler template ---
        self.task_template = os.path.join(self.base_dir, 'task_template.xml')

    def is_running_as_script(self) -> bool:
        """Determine from the executable name whether we run as a script."""
        return os.path.basename(self.executable_path).lower() in ('python.exe', 'pythonw.exe')