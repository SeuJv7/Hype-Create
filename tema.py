"""Tema roxo escuro para as janelas Tkinter/CustomTkinter."""
import tkinter as tk
from tkinter import ttk

BG = "#0f0b17"
CARD = "#1a1425"
PANEL = "#120d1c"
TEXT = "#f4f0fa"
MUTED = "#a99cc0"
ACCENT = "#9b5de5"
ACCENT_LIGHT = "#c77dff"
BORDER = "#2c2240"

def aplicar(widget):
    """Aplica a paleta escura aos estilos ttk (janelas tk clássicas)."""
    style = ttk.Style(widget)
    try: style.theme_use("clam")
    except tk.TclError: pass
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI", 10))
    style.configure("Sub.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
    style.configure("Title.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI", 11, "bold"))
    style.configure("TButton", background=BORDER, foreground=TEXT, borderwidth=0, padding=(10, 6), font=("Segoe UI", 9))
    style.map("TButton", background=[("disabled", "#241b36"), ("active", "#46335f"), ("pressed", ACCENT)], foreground=[("disabled", MUTED)])
    style.configure("Primary.TButton", background=ACCENT, foreground="#ffffff", borderwidth=0, padding=(12, 7), font=("Segoe UI", 9, "bold"))
    style.map("Primary.TButton", background=[("disabled", "#46335f"), ("active", ACCENT_LIGHT), ("pressed", "#8148c5")], foreground=[("disabled", "#ddd5e7")])
    style.configure("TEntry", fieldbackground=PANEL, foreground=TEXT, insertcolor=TEXT, padding=6)
    style.configure("TCombobox", fieldbackground=PANEL, foreground=TEXT, background=BORDER, arrowcolor=TEXT, padding=5)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)])
    style.configure("TCheckbutton", background=CARD, foreground=TEXT)
    style.map("TCheckbutton", background=[("active", CARD)], foreground=[("active", ACCENT_LIGHT)])
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=BORDER, foreground=TEXT, padding=(12, 7))
    style.map("TNotebook.Tab", background=[("selected", ACCENT), ("active", "#46335f")], foreground=[("selected", "#ffffff")])
    style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT, rowheight=25, borderwidth=0)
    style.configure("Treeview.Heading", background=BORDER, foreground=TEXT, font=("Segoe UI", 9, "bold"))
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])
    style.configure("TScrollbar", background=BORDER, troughcolor=BG, arrowcolor=TEXT)
    try: widget.configure(bg=BG)
    except tk.TclError: pass
    return style

def animar_faixa(widget, intervalo=70, largura_bloco=150):
    """Anima uma faixa violeta discreta sem bloquear a interface."""
    estado={"x":-largura_bloco,"fase":0}
    def tick():
        try:
            if not widget.winfo_exists(): return
            largura=max(widget.winfo_width(),1)
            widget.delete("movimento")
            estado["x"]=(estado["x"]+5)%(largura+largura_bloco)-largura_bloco
            widget.create_rectangle(estado["x"],0,estado["x"]+largura_bloco,4,fill=ACCENT,outline="",tags="movimento")
            widget.create_rectangle(estado["x"]+largura_bloco*.28,0,estado["x"]+largura_bloco*.72,4,fill=ACCENT_LIGHT,outline="",tags="movimento")
            estado["fase"]+=1
            widget.after(intervalo,tick)
        except tk.TclError: return
    tick()

def hover(botao, normal, destaque):
    botao.bind("<Enter>",lambda _e: botao.configure(bg=destaque),add="+")
    botao.bind("<Leave>",lambda _e: botao.configure(bg=normal),add="+")
