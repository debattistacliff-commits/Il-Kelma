"""Keep the original window and optional screen point together."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    hwnd: int
    point: tuple[int, int] | None = None
    pid: int | None = None
    focus_point: tuple[int, int] | None = None  # centre of the text box that had the cursor

    def valid(self, desktop):
        return bool(self.hwnd and desktop.exists(self.hwnd)
                    and (self.pid is None or desktop.process(self.hwnd) == self.pid))

    def focus(self, desktop):
        if not self.valid(desktop) or not desktop.activate(self.hwnd):
            return False
        if self.point is not None:
            if desktop.window_at(self.point) != self.hwnd:
                return False
            desktop.focus_at(self.point)
        elif self.focus_point is not None and hasattr(desktop, "restore_focus"):
            desktop.restore_focus(self.focus_point)
        if hasattr(desktop, "is_foreground"):
            return desktop.is_foreground(self.hwnd)
        return desktop.foreground() == self.hwnd


def remember(desktop, point=None):
    hwnd = desktop.foreground() if point is None else desktop.window_at(point)
    if hwnd and point is None and hasattr(desktop, "root"):
        hwnd = desktop.root(hwnd)
    focus_point = desktop.focused_box() if point is None and hasattr(desktop, "focused_box") else None
    return Target(hwnd, point, desktop.process(hwnd) if hwnd else None, focus_point)
