#!/usr/bin/env python3
"""Cria várias contas ao mesmo tempo usando o fluxo completo do assistente."""
import os, threading, time, tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path

import assistente as core
import tema

PASTA = Path(__file__).resolve().parent


def rodar_uma(chave, dados, relatar, concluidas):
    """Roda o fluxo completo do assistente para um pedido, numa thread própria."""
    tag = f"[{dados['usuario']}]"
    encerrar = threading.Event()
    try:
        relatar(f'{tag} iniciando cadastro no navegador isolado')
        core.navegador(chave, dados, encerrar=encerrar)
        concluidas.append(dados['usuario'])
        relatar(f'{tag} janela encerrada')
    except Exception as exc:
        relatar(f'{tag} erro: {str(exc)[:180]}')


def rodar_lote(chave, slug, n, relatar, ao_terminar):
    relatar(f'Comprando {n} e-mail(s), um por conta (com prioridade)...')
    pedidos = []
    for i in range(n):
        try:
            pedidos.append(core.comprar_dados(chave, slug))
        except Exception as exc:
            relatar(f'Compra {i + 1} falhou: {str(exc)[:150]}')
            break
        time.sleep(1)
    if not pedidos:
        relatar('Nenhuma compra concluída; lote abortado.')
        ao_terminar(0)
        return
    relatar(f'{len(pedidos)} pedido(s) comprado(s). Abrindo os navegadores...')
    concluidas = []
    threads = []
    for i, dados in enumerate(pedidos):
        t = threading.Thread(target=rodar_uma, args=(chave, dados, relatar, concluidas), daemon=True)
        t.start()
        threads.append(t)
        relatar(f'Lançada conta {i + 1} de {len(pedidos)}')
        time.sleep(3)
    for t in threads:
        t.join()
    relatar(f'Fim do lote: {len(concluidas)} conta(s) conduzidas até o fim.')
    ao_terminar(len(concluidas))


class JanelaLote(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Criação simultânea')
        self.geometry('560x440')
        self.configure(bg=tema.BG)
        tema.aplicar(self)
        tk.Label(self, text='⚡  CRIAÇÃO SIMULTÂNEA', bg=tema.PANEL, fg=tema.TEXT,
                 font=('Segoe UI', 13, 'bold'), padx=16, pady=12).pack(fill='x')
        tk.Label(self, text='Cada conta compra o próprio e-mail (com prioridade) e roda no '
                            'próprio Chrome isolado, com fingerprint próprio, usando o mesmo '
                            'fluxo do painel.',
                 bg=tema.BG, fg=tema.MUTED, wraplength=520, justify='left',
                 font=('Segoe UI', 9), padx=16, pady=8).pack(anchor='w')
        corpo = tk.Frame(self, bg=tema.BG, padx=16); corpo.pack(fill='x')
        linha = tk.Frame(corpo, bg=tema.BG); linha.pack(fill='x', pady=4)
        tk.Label(linha, text='Contas simultâneas:', bg=tema.BG, fg=tema.TEXT).pack(side='left')
        self.quantidade = tk.StringVar(value='5')
        ttk.Spinbox(linha, from_=1, to=20, textvariable=self.quantidade, width=6).pack(side='left', padx=(8, 0))
        linha_p = tk.Frame(corpo, bg=tema.BG); linha_p.pack(fill='x', pady=4)
        tk.Label(linha_p, text='Produto:', bg=tema.BG, fg=tema.TEXT).pack(side='left')
        self.produto = tk.StringVar(value='ig_gmail_otp')
        ttk.Entry(linha_p, textvariable=self.produto, width=20).pack(side='left', padx=(8, 0))
        linha_pri = tk.Frame(corpo, bg=tema.BG); linha_pri.pack(fill='x', pady=4)
        tk.Label(linha_pri, text='Prioridade:', bg=tema.BG, fg=tema.TEXT).pack(side='left')
        self.prioridade = tk.StringVar(value='25')
        ttk.Entry(linha_pri, textvariable=self.prioridade, width=6).pack(side='left', padx=(8, 0))
        quadro = tk.Frame(self, bg=tema.BG, padx=16); quadro.pack(fill='both', expand=True, pady=(8, 0))
        area = ttk.Frame(quadro); area.pack(fill='both', expand=True)
        self.log = tk.Text(area, height=8, wrap='word', state='disabled', bg='#24212e',
                           fg='#f4f0fa', relief='flat', padx=10, pady=8, font=('Consolas', 9))
        barra = ttk.Scrollbar(area, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=barra.set)
        barra.pack(side='right', fill='y'); self.log.pack(side='left', fill='both', expand=True)
        self.status = tk.Label(self, text='Pronto.', bg=tema.BG, fg=tema.TEXT,
                               anchor='w', justify='left', wraplength=520, padx=16)
        self.status.pack(fill='x', side='bottom', pady=(0, 10))
        self.botao = ttk.Button(self, text='Criar em paralelo', style='Primary.TButton', command=self.iniciar)
        self.botao.pack(pady=8)

    def relatar(self, msg):
        def aplicar():
            self.log.configure(state='normal')
            self.log.insert('end', time.strftime('%H:%M:%S  ') + msg + '\n')
            self.log.see('end')
            self.log.configure(state='disabled')
            self.status.config(text=msg)
        self.after(0, aplicar)

    def iniciar(self):
        chave = os.environ.get('INSTACASTER_API_KEY', '').strip()
        if not chave:
            chave = simpledialog.askstring('Chave da API', 'Digite a chave da API Instacaster:',
                                           show='•', parent=self)
        if not chave:
            return
        try:
            n = max(1, min(20, int(self.quantidade.get())))
        except ValueError:
            messagebox.showwarning('Quantidade', 'Digite um número.', parent=self)
            return
        try:
            os.environ['INSTACASTER_PRIORIDADE'] = str(max(0, int(self.prioridade.get())))
        except ValueError:
            os.environ['INSTACASTER_PRIORIDADE'] = '25'
        slug = self.produto.get().strip() or 'ig_gmail_otp'
        if not messagebox.askyesno('Confirmar', f'Serão feitas {n} compras de {slug}. Continuar?'):
            return
        self.botao.config(state='disabled')
        os.environ['INSTACASTER_API_KEY'] = chave
        def tarefa():
            try:
                rodar_lote(chave, slug, n, self.relatar,
                           lambda total: self.after(0, lambda: self.botao.config(state='normal')))
            except Exception as exc:
                self.relatar('Erro geral: ' + str(exc)[:200])
                self.after(0, lambda: self.botao.config(state='normal'))
        threading.Thread(target=tarefa, daemon=True).start()


if __name__ == '__main__':
    JanelaLote().mainloop()
