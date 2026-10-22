"""A Windows notification that opens a URL when clicked: a review waiting on the page. Never raises, never
waits: a missing notification is no reason to stop a review."""

import contextlib
import os
import subprocess
import sys
from xml.sax.saxutils import escape, quoteattr

# PowerShell's own app id, which Windows lets show toasts without registering an app of our own.
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
SCRIPT = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:FARTSTRIBER_TOAST)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:FARTSTRIBER_TOAST_APP).Show($toast)
"""


def toast_xml(title: str, text: str, url: str) -> str:
    return (
        f'<toast activationType="protocol" launch={quoteattr(url)}><visual><binding template="ToastGeneric">'
        f"<text>{escape(title)}</text><text>{escape(text)}</text></binding></visual></toast>"
    )


def toast(title: str, text: str, url: str) -> None:
    if sys.platform != "win32":
        return
    env = os.environ | {"FARTSTRIBER_TOAST": toast_xml(title, text, url), "FARTSTRIBER_TOAST_APP": APP_ID}
    with contextlib.suppress(OSError):
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
