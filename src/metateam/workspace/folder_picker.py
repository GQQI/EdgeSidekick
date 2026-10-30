"""Native OS folder picker (server runs on the same machine as the user)."""

from __future__ import annotations

import subprocess
import sys
from typing import Optional


def pick_folder(*, title: str = "选择工作区文件夹") -> Optional[str]:
    """Open a system folder dialog and return an absolute path, or None if cancelled."""
    if sys.platform == "win32":
        return _pick_windows(title)
    if sys.platform == "darwin":
        return _pick_macos(title) or _pick_tk(title)
    return _pick_linux(title) or _pick_tk(title)


def _pick_windows(title: str) -> Optional[str]:
    # WinForms dialog via PowerShell STA. A hidden TopMost owner keeps the
    # picker above the browser/Electron window when not using the desktop IPC.
    safe = title.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$form = New-Object System.Windows.Forms.Form; "
        "$form.TopMost = $true; "
        "$form.ShowInTaskbar = $false; "
        "$form.Opacity = 0; "
        "$form.Width = 1; $form.Height = 1; "
        "$form.StartPosition = 'Manual'; "
        "$form.Location = New-Object System.Drawing.Point(-32000, -32000); "
        "$form.Show(); $form.Activate(); "
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
        f"$d.Description = '{safe}'; "
        "$d.ShowNewFolderButton = $true; "
        "$ok = $d.ShowDialog($form); "
        "$form.Close(); $form.Dispose(); "
        "if ($ok -eq [System.Windows.Forms.DialogResult]::OK) { "
        "  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "  Write-Output $d.SelectedPath "
        "}"
    )
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except Exception:
        return None
    path = (proc.stdout or "").strip().splitlines()
    if not path:
        return None
    chosen = path[-1].strip().strip('"')
    return chosen or None


def _pick_macos(title: str) -> Optional[str]:
    """Native macOS folder dialog via AppleScript (works off the main thread)."""
    safe = title.replace("\\", "\\\\").replace('"', '\\"')
    script = f'POSIX path of (choose folder with prompt "{safe}")'
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    chosen = (proc.stdout or "").strip()
    # AppleScript sometimes returns path with trailing slash
    return chosen.rstrip("/") or None


def _pick_linux(title: str) -> Optional[str]:
    """Prefer zenity/kdialog when available."""
    safe = title
    candidates = [
        ["zenity", "--file-selection", "--directory", f"--title={safe}"],
        ["kdialog", "--getexistingdirectory", ".", safe],
    ]
    for cmd in candidates:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
        except FileNotFoundError:
            continue
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        chosen = (proc.stdout or "").strip()
        if chosen:
            return chosen
    return None


def _pick_tk(title: str) -> Optional[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        path = filedialog.askdirectory(title=title, mustexist=True)
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return path or None
