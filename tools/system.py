import os
import re
import shutil
import subprocess
import time
import psutil
import datetime

from config import log

# Commands that require user confirmation before execution
CRITICAL_COMMAND_PATTERNS = [
    "rm -rf", "rm -r", "rmdir", "shred", "dd if=", "mkfs",
    "fdisk", "parted", "cfdisk", "wipefs",
    "chmod 777", "chown -R root",
    "sudo rm", "sudo dd", "sudo mkfs", "sudo fdisk",
    "systemctl stop", "systemctl disable", "systemctl mask",
    "pkill", "killall", "kill -9",
    ":(){:|:&};:", "wget.*sh|bash", "curl.*sh|bash",
    "format", "truncate -s 0",
]

# Pending confirmation storage (tool_name -> pending command)
_pending_confirmation: dict = {}
_current_working_directory: str = None


def _detect_terminal() -> list:
    """Detect available terminal emulator on any Linux distro."""
    candidates = [
        ["kitty"],
        ["alacritty"],
        ["wezterm"],
        ["foot"],
        ["gnome-terminal"],
        ["xfce4-terminal"],
        ["konsole"],
        ["lxterminal"],
        ["mate-terminal"],
        ["tilix"],
        ["rxvt-unicode"],
        ["xterm"],
    ]
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return cmd
    return []


# Verified safe read-only/utility commands that can run without confirmation
SAFE_COMMANDS = {
    "ls", "pwd", "echo", "cat", "grep", "ps", "git", "df", "free", 
    "uptime", "whoami", "uname", "ping", "find", "mkdir", "touch", 
    "cp", "mv", "head", "tail", "wc", "du", "diff", "python", "python3"
}


def _is_critical_command(cmd: str) -> bool:
    """
    Check if a command is critical/destructive or contains dangerous chaining.
    Returns True if the command is critical/unverified (requires confirmation),
    or False if it is a simple, verified safe command.
    """
    cmd_strip = cmd.strip()
    if not cmd_strip:
        return False

    # Check for chaining, redirecting or nesting that could bypass checks (S01)
    chaining_meta = [";", "&", "|", "`", "$", ">", "<", "\n", "\r"]
    for char in chaining_meta:
        if char in cmd_strip:
            return True

    # Check base executable
    parts = cmd_strip.split()
    if not parts:
        return True
    base_exe = os.path.basename(parts[0]).lower()

    if base_exe not in SAFE_COMMANDS:
        return True

    # Check for known dangerous patterns inside command arguments
    cmd_lower = cmd_strip.lower()
    for pattern in CRITICAL_COMMAND_PATTERNS:
        if pattern in cmd_lower:
            return True

    return False


def get_system_health() -> dict:
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory().percent
        battery_info = "unknown"
        if hasattr(psutil, "sensors_battery"):
            battery = psutil.sensors_battery()
            if battery:
                battery_info = f"{battery.percent}%"
        return {"cpu_percent": cpu, "ram_percent": ram, "battery": battery_info}
    except Exception as e:
        return {"error": str(e)}


def get_current_time() -> dict:
    now = datetime.datetime.now()
    return {
        "time": now.strftime("%I:%M %p"),
        "day_of_week": now.strftime("%A"),
        "date": now.strftime("%B %d, %Y"),
    }


def run_shell_command(command: str, require_confirmation: bool = False, confirmed: bool = False) -> dict:
    """
    Runs a shell command on the user's Linux system and returns stdout/stderr output.
    For read-only/safe commands: run directly.
    For commands flagged as critical/destructive: returns a confirmation request.

    Args:
        command: The shell command string to execute.
        require_confirmation: Set True to force confirmation even for non-critical commands.
        confirmed: Set True if the user has already confirmed the command via confirm_critical_action.
    """
    # C2: Sanitize actual newline/carriage-return characters to prevent injection
    command = command.replace('\n', ' ').replace('\r', ' ').strip()

    # Safety check — always require confirmation for dangerous patterns
    is_critical = _is_critical_command(command) or require_confirmation
    if is_critical and not confirmed:
        # Check if there is already a pending shell command
        if "shell" in _pending_confirmation:
            # TTL check (I04): Expire pending critical commands after 60 seconds
            ts = _pending_confirmation.get("shell_ts", 0.0)
            if time.monotonic() - ts > 60.0:
                _pending_confirmation.pop("shell", None)
                _pending_confirmation.pop("shell_ts", None)
            else:
                return {
                    "status": "ERROR_PENDING_ACTION",
                    "message": (
                        f"⚠️ There is already a pending critical command waiting for confirmation:\n"
                        f"  `{_pending_confirmation['shell']}`\n\n"
                        f"Please resolve or cancel that command first before running another critical action."
                    ),
                }
        # Store pending command and ask for confirmation
        _pending_confirmation["shell"] = command
        _pending_confirmation["shell_ts"] = time.monotonic()
        return {
            "status": "CONFIRMATION_REQUIRED",
            "message": (
                f"⚠️ This command is potentially destructive or critical:\n"
                f"  `{command}`\n\n"
                f"Please tell the user what this command does and ask them to confirm with: "
                f"'yes do it' or 'confirm' to proceed, or 'no' / 'cancel' to abort."
            ),
            "command": command,
        }

    # Clear pending if confirmed
    _pending_confirmation.pop("shell", None)
    _pending_confirmation.pop("shell_ts", None)

    global _current_working_directory
    if _current_working_directory is None:
        _current_working_directory = os.getcwd()

    try:
        # Wrap command to extract the final working directory
        wrapped_command = f"{command}\necho \"___PWD___:$(pwd)\""
        cmd_args = ["/bin/bash", "-c", wrapped_command]

        proc = subprocess.run(
            cmd_args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=_current_working_directory,
            env={**os.environ, "TERM": "xterm-256color"},
        )
        
        stdout = proc.stdout
        stderr = proc.stderr
        
        # Parse and strip the ___PWD___ token to keep return context clean
        new_pwd = None
        cleaned_stdout_lines = []
        if stdout:
            for line in stdout.splitlines():
                if line.startswith("___PWD___:"):
                    new_pwd = line.replace("___PWD___:", "").strip()
                else:
                    cleaned_stdout_lines.append(line)
            stdout = "\n".join(cleaned_stdout_lines)

        if new_pwd and os.path.isdir(new_pwd):
            _current_working_directory = new_pwd

        output = stdout.strip()
        err = stderr.strip()
        return {
            "returncode": proc.returncode,
            "stdout": output[:4000] if output else "",
            "stderr": err[:1000] if err else "",
            "success": proc.returncode == 0,
            "command": command,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after 30 seconds: {command}"}
    except Exception as e:
        return {"error": f"Failed to run command: {str(e)}", "command": command}


def confirm_critical_action(confirmed: bool) -> dict:
    """
    Confirms or cancels a pending critical/destructive shell command.
    Call this after the user says 'yes', 'confirm', 'do it', 'no', or 'cancel'.

    Args:
        confirmed: True = user approved, False = user cancelled.
    """
    ts = _pending_confirmation.get("shell_ts", 0.0)
    if "shell" in _pending_confirmation and time.monotonic() - ts > 60.0:
        _pending_confirmation.pop("shell", None)
        _pending_confirmation.pop("shell_ts", None)
        return {"status": "The pending action has expired (TTL 60s). Please request the command again."}

    pending = _pending_confirmation.get("shell")
    if not pending:
        return {"status": "No pending critical action to confirm."}

    if not confirmed:
        _pending_confirmation.pop("shell", None)
        _pending_confirmation.pop("shell_ts", None)
        return {"status": "Action cancelled. The critical command was NOT executed.", "command": pending}

    # Execute the confirmed command
    _pending_confirmation.pop("shell", None)
    _pending_confirmation.pop("shell_ts", None)
    return run_shell_command(pending, confirmed=True)


def open_terminal(command: str = "") -> dict:
    """
    Opens a terminal emulator window. Works on any Linux distro/desktop environment.
    Optionally runs a command inside the terminal.

    Args:
        command: Optional shell command to run inside the new terminal window.
                 If empty, just opens the terminal at home directory.
    """
    term_cmd = _detect_terminal()
    if not term_cmd:
        return {"error": "No terminal emulator found. Please install one (e.g., xterm, gnome-terminal, konsole)."}

    term = term_cmd[0]
    try:
        if command:
            # Each terminal has its own flag for running a command
            if term == "konsole":
                full_cmd = ["konsole", "--noclose", "-e", "bash", "-c", command]
            elif term == "gnome-terminal":
                full_cmd = ["gnome-terminal", "--", "bash", "-c", f"{command}; exec bash"]
            elif term in ("xfce4-terminal", "mate-terminal", "lxterminal"):
                full_cmd = [term, "--command", f"bash -c '{command}; exec bash'"]
            elif term in ("alacritty", "kitty", "foot"):
                full_cmd = [term, "-e", "bash", "-c", f"{command}; exec bash"]
            elif term == "tilix":
                full_cmd = ["tilix", "-e", f"bash -c '{command}; exec bash'"]
            else:
                full_cmd = [term, "-e", f"bash -c '{command}; exec bash'"]
        else:
            full_cmd = term_cmd

        subprocess.Popen(full_cmd, env={**os.environ}, start_new_session=True)
        return {
            "success": True,
            "terminal": term,
            "command_in_terminal": command or "(interactive shell)",
        }
    except Exception as e:
        return {"error": f"Failed to open terminal: {str(e)}"}


def open_application(app_name: str) -> dict:
    """
    Opens a system application by name on any Linux distro.
    Uses xdg-open for files/URLs, or directly launches by executable name.
    Works on GNOME, KDE, XFCE, i3, sway, and all other desktops.

    Args:
        app_name: Application name or executable (e.g. 'firefox', 'nautilus', 'dolphin',
                  'vscode', 'code', 'gimp', 'vlc', 'obs', 'discord', 'spotify', 'steam').
    """
    app_lower = app_name.lower().strip()

    # Common name aliases → executable name
    aliases = {
        "vs code": "code", "vscode": "code", "visual studio code": "code",
        "file manager": None,  # handled below by DE detection
        "files": "nautilus",
        "dolphin": "dolphin", "nautilus": "nautilus", "thunar": "thunar",
        "nemo": "nemo", "pcmanfm": "pcmanfm",
        "text editor": None,  # handled below
        "gedit": "gedit", "kate": "kate", "mousepad": "mousepad", "pluma": "pluma",
        "terminal": None,  # use open_terminal instead
        "firefox": "firefox", "chromium": "chromium", "google-chrome": "google-chrome-stable",
        "chrome": "google-chrome-stable",
        "vlc": "vlc", "mpv": "mpv",
        "gimp": "gimp", "inkscape": "inkscape", "krita": "krita",
        "obs": "obs", "obs studio": "obs",
        "discord": "discord",
        "spotify": "spotify",
        "steam": "steam",
        "calculator": None,  # handled below
        "settings": None,    # handled below
    }

    # DE-aware fallbacks for generic app names
    de = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

    if app_lower in ("file manager", "files"):
        if "kde" in de:
            exe = "dolphin"
        elif "xfce" in de:
            exe = "thunar"
        elif "mate" in de:
            exe = "caja"
        elif "lxde" in de or "lxqt" in de:
            exe = "pcmanfm"
        else:
            exe = "nautilus"
    elif app_lower in ("text editor", "editor"):
        if "kde" in de:
            exe = "kate"
        elif "xfce" in de:
            exe = "mousepad"
        elif "mate" in de:
            exe = "pluma"
        else:
            exe = "gedit"
    elif app_lower in ("calculator",):
        if "kde" in de:
            exe = "kcalc"
        elif "xfce" in de:
            exe = "galculator"
        else:
            exe = "gnome-calculator"
    elif app_lower in ("settings", "system settings"):
        if "kde" in de:
            exe = "systemsettings"
        else:
            exe = "gnome-control-center"
    elif app_lower == "terminal":
        term = _detect_terminal()
        exe = term[0] if term else "xterm"
    else:
        exe = aliases.get(app_lower, app_lower)

    if exe and shutil.which(exe):
        try:
            subprocess.Popen([exe], start_new_session=True)
            return {"success": True, "launched": exe, "app_name": app_name}
        except Exception as e:
            return {"error": f"Failed to launch '{exe}': {str(e)}"}

    # Last resort: try xdg-open
    try:
        expanded_path = os.path.expanduser(app_name)
        subprocess.Popen(["xdg-open", expanded_path], start_new_session=True)
        return {"success": True, "launched": f"xdg-open {expanded_path}"}
    except Exception as e:
        return {"error": f"Application '{app_name}' not found or could not be launched: {str(e)}"}
