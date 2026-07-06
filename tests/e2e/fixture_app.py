"""Deterministic Tk fixture app for e2e tests (DESIGN §12).
Big high-contrast text so Vision OCR is reliable at any scale.
Run standalone: uv run python tests/e2e/fixture_app.py
"""
from __future__ import annotations

import tkinter as tk

FONT = ("Helvetica", 28, "bold")


def main() -> None:
    root = tk.Tk()
    root.title("Hands Fixture")
    root.geometry("640x420+120+120")
    root.configure(bg="white")

    count = tk.IntVar(value=0)
    counter = tk.Label(root, text="COUNT 0", font=FONT, bg="white", fg="black")
    counter.pack(pady=16)

    def bump() -> None:
        count.set(count.get() + 1)
        counter.config(text=f"COUNT {count.get()}")

    tk.Button(root, text="INCREMENT", font=FONT, command=bump,
              height=2).pack(pady=8)

    entry = tk.Entry(root, font=FONT, width=18)
    entry.pack(pady=8)
    echo = tk.Label(root, text="ECHO", font=FONT, bg="white", fg="black")
    echo.pack(pady=8)
    entry.bind("<KeyRelease>",
               lambda e: echo.config(text=f"ECHO {entry.get()}"))

    root.mainloop()


if __name__ == "__main__":
    main()
