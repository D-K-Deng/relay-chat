import re
import subprocess
import sys
from pathlib import Path


PICKER_AVAILABLE = sys.platform in ("win32", "darwin")


def pick_folder(language="en"):
    title = "选择 Relay Chat 工作区文件夹" if language == "zh-CN" else "Choose the Relay Chat workspace folder"
    if sys.platform == "win32":
        script = """
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '{title}'
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::Write($dialog.SelectedPath)
}
"""
        command = ["powershell.exe", "-NoProfile", "-STA", "-Command", script.replace("{title}", title)]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW}
    elif sys.platform == "darwin":
        script = """
try
    return POSIX path of (choose folder with prompt "{title}")
on error number -128
    return ""
end try
"""
        command = ["/usr/bin/osascript", "-e", script.replace("{title}", title)]
        options = {}
    else:
        raise ValueError("Enter the workspace folder path in Settings.")

    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", check=True, **options)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("Could not open the folder picker. Enter the folder path in Settings.") from error
    return result.stdout.rstrip("\r\n") or None


def folder_path(value):
    if not value.strip():
        return ""
    directory = Path(value.strip()).expanduser()
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("Choose an existing workspace folder or enter its full path.")
    return str(directory.resolve())


def save_markdown(directory, title, markdown):
    if not directory:
        raise ValueError("Choose a workspace folder in Settings before exporting.")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)[:70].strip(" .")
    stem = "Relay - " + (stem or "Conversation")
    folder = Path(directory)
    filename = folder / f"{stem}.md"
    suffix = 2
    while True:
        try:
            with filename.open("x", encoding="utf-8", newline="\n") as file:
                file.write(markdown)
            return str(filename)
        except FileExistsError:
            filename = folder / f"{stem} ({suffix}).md"
            suffix += 1
        except OSError as error:
            raise ValueError("Could not save to the workspace folder. Check its location and permissions.") from error
