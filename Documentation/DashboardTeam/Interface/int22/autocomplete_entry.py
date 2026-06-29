import re
import tkinter as tk
from tkinter import ttk
import unicodedata


class AutocompleteEntry(tk.Frame):
    """
    Cross-platform autocomplete input based on ttk.Combobox.

    This implementation avoids custom toplevel popups so it behaves better on
    Raspberry Pi window managers.
    """

    def __init__(self, master, options, width=37, max_rows=8, **kwargs):
        super().__init__(master, **kwargs)
        self.options = sorted(set(options))
        self.filtered_options = list(self.options)
        self.max_rows = max_rows
        self.var = tk.StringVar()

        self.combobox = ttk.Combobox(
            self,
            textvariable=self.var,
            values=self.options,
            width=width,
            state="normal",
        )
        self.combobox.pack(fill=tk.BOTH, expand=True)

        self.combobox.bind("<<ComboboxSelected>>", self._on_selected)
        self.combobox.bind("<Return>", self._on_return)
        self.combobox.bind("<KeyRelease>", self._on_key_release)
        self.var.trace_add("write", self._on_var_changed)

    def set_options(self, options):
        self.options = sorted(set(options))
        self.filtered_options = list(self.options)
        self.combobox.configure(values=self.options)

    def _on_selected(self, _event=None):
        self.event_generate("<<AutocompleteSelected>>")

    def _on_return(self, _event=None):
        typed = self.var.get().strip()
        if not typed:
            return None

        filtered = [opt for opt in self.options if self._matches_prefix(opt, typed)]
        if filtered:
            self.var.set(filtered[0])
            self.filtered_options = filtered
            self.event_generate("<<AutocompleteSelected>>")
            return "break"
        return None

    def _on_key_release(self, event):
        if event.keysym in ("Up", "Down", "Return", "Tab", "Escape"):
            return

        self._refresh_suggestions(open_dropdown=True)

    def _on_var_changed(self, *_args):
        # Handles programmatic inserts (virtual keyboard) as well.
        self._refresh_suggestions(open_dropdown=False)

    def _refresh_suggestions(self, open_dropdown=False):
        typed = self.var.get().strip()
        if not typed:
            self.filtered_options = list(self.options)
            self.combobox.configure(values=self.options)
            return

        self.filtered_options = [opt for opt in self.options if self._matches_prefix(opt, typed)]
        if self.filtered_options:
            self.combobox.configure(values=self.filtered_options)
            if open_dropdown and self.focus_get() == self.combobox:
                try:
                    self.combobox.event_generate("<Down>")
                except tk.TclError:
                    pass
        else:
            self.combobox.configure(values=self.options)

    def on_virtual_input(self):
        self._refresh_suggestions(open_dropdown=True)

    def hide_suggestions(self):
        # Native combobox popup closes automatically on focus change.
        return None

    def focus_input(self):
        self.combobox.focus_set()
        self.combobox.icursor(tk.END)

    def get_input_widget(self):
        return self.combobox

    def _normalize(self, value):
        normalized = unicodedata.normalize("NFKD", value)
        return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()

    def _matches_prefix(self, option, typed):
        norm_option = self._normalize(option)
        norm_typed = self._normalize(typed)
        if norm_option.startswith(norm_typed):
            return True
        words = [word for word in re.split(r"[^a-z0-9]+", norm_option) if word]
        return any(word.startswith(norm_typed) for word in words)

    def get(self):
        return self.var.get().strip()

    def set(self, value):
        self.var.set(value)
