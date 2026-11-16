#!/usr/bin/env python3
"""Painel local para iniciar ou retomar o assistente, sem comandos no terminal."""
import importlib.util
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

os.chdir(Path(__file__).resolve().parent)
import assistente as core
from assistente import Toless, TolessError


class ChaveLocal:
    def __init__(self):
        pasta = os.environ.get('LOCALAPPDATA')
        self.arquivo = Path(pasta) / 'TolessOTP' / 'api-key.dpapi' if os.name == 'nt' and pasta else None

    @staticmethod
    def _proteger(texto, salvar):
        if salvar:
            operacao = ('$bytes = [Text.Encoding]::ASCII.GetBytes($texto); '
                        '$cifrado = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, "CurrentUser"); '
                        '[Console]::WriteLine([Convert]::ToBase64String($cifrado))')
        else:
            operacao = ('$bytes = [Convert]::FromBase64String($texto); '
                        '$aberto = [Security.Cryptography.ProtectedData]::Unprotect($bytes, $null, "CurrentUser"); '
                        '[Console]::WriteLine([Text.Encoding]::ASCII.GetString($aberto))')
        sistema = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        powershell = sistema / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        comando = ("$ErrorActionPreference = 'Stop'; Add-Type -AssemblyName System.Security; "
                   "$texto = [Console]::In.ReadToEnd(); " + operacao)
        resultado = subprocess.run(
            [str(powershell), '-NoProfile', '-NonInteractive', '-Command', comando],
            input=texto.encode('ascii'), capture_output=True, check=True, timeout=10,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        valor = resultado.stdout.decode('ascii').strip()
        if not valor:
            raise ValueError('Chave local indisponível.')
        return valor

    def ler(self):
        if self.arquivo is None or not self.arquivo.is_file():
            return ''
        return self._proteger(self.arquivo.read_text(encoding='ascii').strip(), salvar=False)

    def gravar(self, chave):
        if self.arquivo is None:
            return
        cifrada = self._proteger(chave, salvar=True)
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='ascii', dir=self.arquivo.parent, delete=False) as arquivo:
            temporario = Path(arquivo.name)
        try:
            temporario.write_text(cifrada, encoding='ascii')
            temporario.replace(self.arquivo)
        finally:
            temporario.unlink(missing_ok=True)


class Fluxo:
    def __init__(self, fila): self.fila = fila
    def write(self, texto):
        if texto: self.fila.put(('log', str(texto)))
        return len(texto)
    def flush(self): pass

class Painel:
    def __init__(self, root):
        self.root = root
        root.title('Assistente de cadastro — Toless OTP')
        root.geometry('940x810'); root.minsize(780, 670)
        root.configure(bg='#f2f2f2')
        self.fila = queue.Queue(); self.ocupado = False; self.encerrar = None
        self.chave_local = ChaveLocal()
        try:
            self.chave_salva = self.chave_local.ler()
        except (OSError, ValueError, subprocess.SubprocessError):
            self.chave_salva = ''
            self.fila.put(('log', 'Não foi possível carregar a chave salva. Informe a chave novamente.\n'))
        self.chave = tk.StringVar(value=self.chave_salva or os.environ.get('TOLESS_TOKEN', ''))
        self.produto = tk.StringVar(); self.produtos = {}; self.compra_pendente = None
        self.pedido = tk.StringVar(); self.status = tk.StringVar(value='Informe sua chave para começar.')
        estilo = ttk.Style(root)
        try: estilo.theme_use('clam')
        except tk.TclError: pass
        estilo.configure('TFrame', background='#f2f2f2')
        estilo.configure('Card.TFrame', background='#ffffff')
        estilo.configure('Card.TLabel', background='#ffffff', foreground='#222222', font=('Segoe UI', 10))
        estilo.configure('Sub.TLabel', background='#ffffff', foreground='#666666', font=('Segoe UI', 9))
        estilo.configure('Title.TLabel', background='#ffffff', foreground='#111111', font=('Segoe UI', 11, 'bold'))
        estilo.configure('TButton', font=('Segoe UI', 10), padding=(12, 7), background='#e5e5e5', foreground='#111111', borderwidth=0)
        estilo.map('TButton', background=[('disabled','#d9d9d9'),('active','#c9c9c9')], foreground=[('disabled','#666666')])
        estilo.configure('Primary.TButton', font=('Segoe UI', 10, 'bold'), padding=(13, 8), background='#333333', foreground='#ffffff', borderwidth=0)
        estilo.map('Primary.TButton', background=[('disabled','#777777'),('active','#555555')], foreground=[('disabled','#eeeeee')])
        estilo.configure('TEntry', padding=7, fieldbackground='#ffffff', foreground='#111111')
        estilo.configure('TCombobox', padding=6, fieldbackground='#ffffff', foreground='#111111', background='#e5e5e5', arrowcolor='#ffffff')
        estilo.configure('Card.TCheckbutton', background='#ffffff', foreground='#ede2f9', font=('Segoe UI', 9))
        estilo.map('Card.TCheckbutton', background=[('active','#ffffff')], foreground=[('active','#ffffff')])
        cab = tk.Frame(root, bg='#111111', padx=24, pady=16); cab.pack(fill='x')
        faixa = tk.Frame(cab, bg='#111111'); faixa.pack(fill='x')
        tk.Label(faixa, text='✦  ASSISTENTE DE CADASTRO', fg='#ffffff', bg='#111111', font=('Segoe UI', 17, 'bold')).pack(side='left', anchor='w')
        self.luz = tk.Label(faixa, text='●', fg='#777777', bg='#111111', font=('Segoe UI', 14)); self.luz.pack(side='right')
        tk.Label(cab, text='Conexão, pedidos e acompanhamento em um só lugar', fg='#dddddd', bg='#111111', font=('Segoe UI', 10)).pack(anchor='w', pady=(3,0))
        self.faixa_animada = tk.Canvas(root, height=3, highlightthickness=0, bd=0, bg='#111111'); self.faixa_animada.pack(fill='x')
        self._animacao_pos = 0; self._animacao_passo = 0
        self._animar()
        base = ttk.Frame(root, padding=(20,12)); base.pack(fill='both', expand=True)
        def cartao(titulo, subtitulo):
            bloco = ttk.Frame(base, style='Card.TFrame', padding=(16,10)); bloco.pack(fill='x', pady=(0,8))
            ttk.Label(bloco, text=titulo, style='Title.TLabel').pack(anchor='w')
            ttk.Label(bloco, text=subtitulo, style='Sub.TLabel').pack(anchor='w', pady=(2,6))
            return bloco
        caixa = cartao('01   Conexão com a Toless OTP', 'No Windows, a chave é salva criptografada para o seu usuário.')
        linha = ttk.Frame(caixa, style='Card.TFrame'); linha.pack(fill='x')
        ttk.Label(linha, text='Chave da API', style='Card.TLabel').pack(side='left')
        entrada = ttk.Entry(linha, textvariable=self.chave, show='•'); entrada.pack(side='left', fill='x', expand=True, padx=(12,10))
        self.mostrar = tk.BooleanVar(value=False)
        ttk.Checkbutton(linha, text='Mostrar', style='Card.TCheckbutton', variable=self.mostrar, command=lambda: entrada.configure(show='' if self.mostrar.get() else '•')).pack(side='left')
        novo = cartao('02   Nova compra', 'Consulte os produtos e confira o preço. A compra exige confirmação.')
        linha = ttk.Frame(novo, style='Card.TFrame'); linha.pack(fill='x')
        ttk.Label(linha, text='Produto', style='Card.TLabel').pack(side='left')
        self.seletor = ttk.Combobox(linha, textvariable=self.produto, state='readonly', width=24)
        self.seletor.pack(side='left', fill='x', expand=True, padx=(12,10))
        self.catalogo = ttk.Button(linha, text='Consultar produtos', command=self.consultar); self.catalogo.pack(side='left', padx=(0,8))
        self.comprar = ttk.Button(linha, text='Comprar e iniciar', style='Primary.TButton', command=self.novo); self.comprar.pack(side='left')
        lote = ttk.Frame(novo, style='Card.TFrame')
        lote.pack(fill='x', pady=(8, 0))
        ttk.Label(lote, text='Quantidade', style='Card.TLabel').pack(side='left')
        self.quantidade = tk.StringVar(value='1')
        ttk.Spinbox(lote, from_=1, to=30, textvariable=self.quantidade, width=5).pack(side='left', padx=10)
        self.lote = ttk.Button(lote, text='Iniciar lote', command=self.varias)
        self.lote.pack(side='left')
        ret = cartao('03   Retomar pedido', 'Selecione no histórico Toless ou informe o ID. Nenhuma compra será feita.')
        linha = ttk.Frame(ret, style='Card.TFrame'); linha.pack(fill='x')
        ttk.Label(linha, text='ID do pedido', style='Card.TLabel').pack(side='left')
        self.combo = ttk.Combobox(linha, textvariable=self.pedido, width=24); self.combo.pack(side='left', fill='x', expand=True, padx=(12,10))
        self.atualizar = ttk.Button(linha, text='Atualizar lista', command=self.listar); self.atualizar.pack(side='left', padx=(0,8))
        self.retomar = ttk.Button(linha, text='Retomar pedido', style='Primary.TButton', command=self.abrir_salvo); self.retomar.pack(side='left')
        progresso = ttk.Frame(base, style='Card.TFrame', padding=(16,10)); progresso.pack(fill='both', expand=True)
        ttk.Label(progresso, text='Acompanhamento', style='Title.TLabel').pack(anchor='w')
        ttk.Label(progresso, text='Veja as etapas e mensagens do assistente aqui.', style='Sub.TLabel').pack(anchor='w', pady=(2,6))
        area = ttk.Frame(progresso, style='Card.TFrame'); area.pack(fill='both', expand=True)
        barra = ttk.Scrollbar(area); barra.pack(side='right', fill='y')
        self.log = tk.Text(area, height=6, wrap='word', state='disabled', bg='#ffffff', fg='#222222', insertbackground='#ffffff', relief='flat', padx=10, pady=8, font=('Consolas', 9), yscrollcommand=barra.set)
        self.log.pack(side='left', fill='both', expand=True); barra.config(command=self.log.yview)
        rodape = ttk.Frame(base); rodape.pack(fill='x', pady=(8,0))
        ttk.Label(rodape, textvariable=self.status, wraplength=520, background='#f2f2f2', foreground='#555555', font=('Segoe UI', 9)).pack(side='left', fill='x', expand=True)
        self.parar = ttk.Button(rodape, text='Parar acompanhamento', command=self.fechar_browser, state='disabled'); self.parar.pack(side='right')
        self.contas_btn = ttk.Button(rodape, text='Painel de contas', style='Primary.TButton', command=self.abrir_painel_contas)
        self.contas_btn.pack(side='right', padx=(0,8))
        self.config_btn = ttk.Button(rodape, text='Configurações', command=self.abrir_configuracoes)
        self.config_btn.pack(side='right', padx=(0,8))
        ttk.Button(rodape, text='Proxies', command=self.abrir_proxies).pack(side='right', padx=(0,8))
        ttk.Button(rodape, text='Extensões', command=self.abrir_extensoes).pack(side='right', padx=(0,8))
        self.root.after(100, self.processar)
        root.protocol('WM_DELETE_WINDOW', self.fechar)

    def abrir_extensoes(self):
        """Gerencia as extensões carregadas em todos os navegadores do assistente."""
        pasta_extensoes = Path(__file__).resolve().parent / 'extensoes'
        pasta_extensoes.mkdir(parents=True, exist_ok=True)
        janela = tk.Toplevel(self.root)
        janela.title('Extensões do navegador')
        janela.geometry('560x470'); janela.transient(self.root); janela.grab_set()
        janela.configure(bg='#f2f2f2')
        tk.Label(janela, text='EXTENSÕES DO NAVEGADOR', bg='#111111', fg='white',
                 font=('Segoe UI', 14, 'bold'), padx=18, pady=14).pack(fill='x')
        tk.Label(janela,
                 text='Estas extensões são carregadas em todos os navegadores abertos pelo assistente '
                      '(cadastro único, várias janelas e abrir perfil salvo).',
                 bg='#f2f2f2', fg='#444444', wraplength=510, justify='left',
                 font=('Segoe UI', 9), padx=18, pady=12).pack(anchor='w')
        caixa = tk.Frame(janela, bg='#f2f2f2')
        caixa.pack(fill='both', expand=True, padx=18)
        barra_lista = tk.Scrollbar(caixa)
        barra_lista.pack(side='right', fill='y')
        lista = tk.Listbox(caixa, bg='white', fg='#111111', relief='solid', bd=1,
                           yscrollcommand=barra_lista.set, font=('Segoe UI', 10))
        lista.pack(side='left', fill='both', expand=True)
        barra_lista.config(command=lista.yview)

        def recarregar():
            lista.delete(0, 'end')
            pastas = sorted([p for p in pasta_extensoes.iterdir() if p.is_dir()]) if pasta_extensoes.is_dir() else []
            if not pastas:
                lista.insert('end', '  (nenhuma extensão instalada)')
            for p in pastas:
                marca = '[ok] ' if (p / 'manifest.json').is_file() else '[sem manifest.json] '
                lista.insert('end', '  ' + marca + p.name)

        def adicionar():
            origem = filedialog.askdirectory(parent=janela, title='Escolha a pasta da extensão (contém manifest.json)')
            if not origem:
                return
            origem = Path(origem)
            if not (origem / 'manifest.json').is_file():
                messagebox.showwarning('manifest.json não encontrado',
                    'A pasta escolhida não contém um manifest.json.\n'
                    'Entre um nível a mais (a pasta raiz da extensão) e escolha de novo.',
                    parent=janela)
                return
            destino = pasta_extensoes / origem.name
            if destino.exists():
                if not messagebox.askyesno('Já existe', f'"{origem.name}" já está instalada. Substituir?', parent=janela):
                    return
                shutil.rmtree(destino, ignore_errors=True)
            try:
                shutil.copytree(origem, destino)
            except OSError as exc:
                messagebox.showerror('Erro ao copiar', str(exc), parent=janela)
                return
            recarregar()
            messagebox.showinfo('Extensão instalada',
                f'"{origem.name}" instalada.\nOs próximos navegadores abertos pelo assistente a carregam automaticamente.',
                parent=janela)

        def remover():
            escolha = lista.curselection()
            if not escolha:
                return
            texto = lista.get(escolha[0]).strip()
            if texto.startswith('('):
                return
            nome = re.sub(r'^\[ok\] |^\[sem manifest.json\] ', '', texto)
            destino = pasta_extensoes / nome
            if destino.is_dir() and messagebox.askyesno('Remover', f'Remover "{nome}"?', parent=janela):
                shutil.rmtree(destino, ignore_errors=True)
                recarregar()

        def abrir_pasta():
            if os.name == 'nt':
                os.startfile(str(pasta_extensoes))

        botoes = tk.Frame(janela, bg='#f2f2f2')
        botoes.pack(fill='x', padx=18, pady=10)
        tk.Button(botoes, text='Adicionar extensão...', command=adicionar, bg='#333333', fg='white',
                  activebackground='#555555', activeforeground='white', padx=12, pady=7,
                  font=('Segoe UI', 10, 'bold')).pack(side='right')
        tk.Button(botoes, text='Remover selecionada', command=remover, bg='#e5e5e5', fg='#111111',
                  activebackground='#c9c9c9', padx=10, pady=7).pack(side='right', padx=(0, 8))
        tk.Button(botoes, text='Abrir pasta', command=abrir_pasta, bg='#e5e5e5', fg='#111111',
                  activebackground='#c9c9c9', padx=10, pady=7).pack(side='right', padx=(0, 8))
        tk.Label(janela, text='A extensão precisa estar como pasta descompactada, com o manifest.json na raiz dela.',
                 bg='#f2f2f2', fg='#666666', font=('Segoe UI', 8)).pack(anchor='w', padx=18, pady=(0, 12))
        recarregar()

    def abrir_configuracoes(self):
        """Guarda apenas rascunhos locais de foto e bio para uso manual."""
        caminho = Path(__file__).resolve().parent / 'configuracoes_perfil.json'
        try:
            dados = json.loads(caminho.read_text(encoding='utf-8')) if caminho.exists() else {}
            if not isinstance(dados, dict): dados = {}
        except (OSError, ValueError):
            dados = {}
        janela = tk.Toplevel(self.root)
        janela.title('Configurações — rascunho de perfil')
        janela.geometry('620x690'); janela.minsize(510, 610)
        janela.configure(bg='#f2f2f2'); janela.transient(self.root); janela.grab_set()
        tk.Label(janela, text='PERFIL E VEXIONFLOW', bg='#111111', fg='white',
                 font=('Segoe UI', 14, 'bold'), padx=18, pady=14).pack(fill='x')
        tk.Label(janela, text='Selecione as etapas que a VexionFlow deve executar após cada cadastro concluído.',
                 bg='#f2f2f2', fg='#444444', wraplength=510, justify='left',
                 font=('Segoe UI', 9), padx=18, pady=12).pack(anchor='w')
        corpo = tk.Frame(janela, bg='#f2f2f2', padx=18); corpo.pack(fill='both', expand=True)
        tk.Label(corpo, text='Foto de perfil (arquivo local, apenas referência)', bg='#f2f2f2', fg='#111111').pack(anchor='w')
        foto = tk.StringVar(value=str(dados.get('foto_perfil', '')))
        linha = tk.Frame(corpo, bg='#f2f2f2'); linha.pack(fill='x', pady=(5,14))
        tk.Entry(linha, textvariable=foto, relief='solid', bd=1).pack(side='left', fill='x', expand=True)
        def escolher_foto():
            selecionada = filedialog.askopenfilename(parent=janela, title='Escolha uma foto',
                filetypes=[('Imagens', '*.png *.jpg *.jpeg *.webp *.bmp'), ('Todos os arquivos', '*.*')])
            if selecionada: foto.set(selecionada)
        tk.Button(linha, text='Escolher…', command=escolher_foto, bg='#e5e5e5', fg='#111111').pack(side='left', padx=(8,0))
        tk.Label(corpo, text='Executável do Google Chrome/Chromium instalado', bg='#f2f2f2', fg='#111111').pack(anchor='w')
        navegador = tk.StringVar(value=str(dados.get('navegador_executavel', '')))
        linha_navegador = tk.Frame(corpo, bg='#f2f2f2'); linha_navegador.pack(fill='x', pady=(5,12))
        tk.Entry(linha_navegador, textvariable=navegador, relief='solid', bd=1).pack(side='left', fill='x', expand=True)
        def escolher_navegador():
            selecionado = filedialog.askopenfilename(parent=janela, title='Escolha Chrome ou Chromium',
                filetypes=[('Executável', '*.exe'), ('Todos os arquivos', '*.*')])
            if selecionado: navegador.set(selecionado)
        tk.Button(linha_navegador, text='Escolher…', command=escolher_navegador, bg='#e5e5e5', fg='#111111').pack(side='left', padx=(8,0))
        tk.Label(corpo, text='Biografia (rascunho para copiar manualmente)', bg='#f2f2f2', fg='#111111').pack(anchor='w')
        bio = tk.Text(corpo, height=5, wrap='word', bg='white', fg='#111111', relief='solid', bd=1)
        bio.pack(fill='both', expand=True, pady=(5,12)); bio.insert('1.0', str(dados.get('biografia', '')))
        vexion = dados.get('vexionflow', {})
        if not isinstance(vexion, dict):
            vexion = {}
        executar = tk.BooleanVar(value=bool(vexion.get('automatica')))
        fechar_janela = tk.BooleanVar(value=bool(vexion.get('fechar_ao_concluir')))
        ttk.Checkbutton(corpo, text='Configurar automaticamente após criar a conta', variable=executar).pack(anchor='w', pady=(0, 5))
        ttk.Checkbutton(corpo, text='Fechar janela ao concluir o cadastro e as etapas escolhidas',
                        variable=fechar_janela).pack(anchor='w', pady=(0, 5))
        opcoes = ttk.Frame(corpo)
        opcoes.pack(fill='x')
        etapas = {}
        for i, (etapa, rotulo) in enumerate(core.VEXION_ETAPAS.items()):
            variavel = etapas[etapa] = tk.BooleanVar(value=etapa in vexion.get('etapas', []))
            ttk.Checkbutton(opcoes, text=rotulo, variable=variavel).grid(row=i // 2, column=i % 2, sticky='w', padx=(0, 16))
        ttk.Label(corpo, text='Link de conexão da Vexion (opcional se já salvo nos parâmetros)').pack(anchor='w', pady=(8, 0))
        link = tk.StringVar(value=vexion.get('link', ''))
        ttk.Entry(corpo, textvariable=link).pack(fill='x', pady=(3, 4))
        ttk.Button(corpo, text='Editar parâmetros da VexionFlow',
                   command=lambda: self.editar_padroes_vexion(janela)).pack(anchor='w', pady=(3, 0))

        def salvar_config():
            escolhidas = [etapa for etapa, variavel in etapas.items() if variavel.get()]
            endereco = link.get().strip()
            if executar.get() and not escolhidas:
                messagebox.showwarning('VexionFlow', 'Selecione pelo menos uma etapa.', parent=janela)
                return
            if endereco and not core.link_vexion_valido(endereco):
                messagebox.showwarning('Link da Vexion', 'Cole um link de conexão válido da Vexion.', parent=janela)
                return
            saida = {**dados, 'foto_perfil': foto.get().strip(), 'biografia': bio.get('1.0', 'end-1c').strip(),
                     'navegador_executavel': navegador.get().strip(),
                     'vexionflow': {'automatica': executar.get(), 'etapas': escolhidas, 'link': endereco,
                                    'fechar_ao_concluir': fechar_janela.get()}}
            try:
                temporario = caminho.with_suffix('.tmp')
                temporario.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temporario, caminho)
            except OSError as exc:
                messagebox.showerror('Erro ao salvar', str(exc), parent=janela); return
            messagebox.showinfo('Salvo', 'Configurações salvas.', parent=janela)
            janela.destroy()
        tk.Button(janela, text='Salvar configurações', command=salvar_config, bg='#222222', fg='white',
                  activebackground='#555555', activeforeground='white', padx=14, pady=7).pack(anchor='e', padx=18, pady=14)

    def editar_padroes_vexion(self, parent):
        arquivos = [Path(pasta) / 'padrao.js' for pasta in core._pastas_de_extensoes()
                    if Path(pasta).name.casefold() == 'vexionflow-mobile']
        arquivo = next((pasta for pasta in arquivos if pasta.is_file()), None)
        if arquivo is None:
            messagebox.showerror('VexionFlow', 'Não encontrei o padrao.js da VexionFlow Mobile.', parent=parent)
            return
        try:
            texto = arquivo.read_text(encoding='utf-8-sig')
            marcador = 'globalThis.VexionPadrao ='
            prefixo, codigo = texto.split(marcador, 1)
            dados = json.loads(codigo.strip().removesuffix(';'))
            if not isinstance(dados, dict):
                raise ValueError
        except (OSError, ValueError):
            messagebox.showerror('VexionFlow', 'Não consegui ler os parâmetros existentes.', parent=parent)
            return

        janela = tk.Toplevel(parent)
        janela.title('Parâmetros da VexionFlow')
        janela.geometry('610x500')
        janela.transient(parent)
        janela.grab_set()
        janela.bind('<Destroy>', lambda evento: parent.grab_set() if evento.widget == janela and parent.winfo_exists() else None)
        notebook = ttk.Notebook(janela)
        notebook.pack(fill='both', expand=True, padx=12, pady=12)
        abas = {}
        for titulo in ('Conta e perfil', 'Story e link', 'Destaque e conexão'):
            aba = ttk.Frame(notebook, padding=14)
            aba.columnconfigure(1, weight=1)
            notebook.add(aba, text=titulo)
            abas[titulo] = aba

        campos = (
            ('Conta e perfil', 'conta', 'profissional', 'Conta profissional', True),
            ('Conta e perfil', 'conta', 'tipo', 'Tipo de conta', 'criador'),
            ('Conta e perfil', 'conta', 'categoria', 'Categoria', 'Blog pessoal'),
            ('Conta e perfil', 'conta', 'aceitarConviteAppUnico', 'Aceitar convite do app', True),
            ('Conta e perfil', 'perfil', 'biografia', 'Biografia', ''),
            ('Conta e perfil', 'perfil', 'foto', 'Foto', ''),
            ('Story e link', 'story', 'arquivo', 'Imagem ou vídeo do Story', ''),
            ('Story e link', 'story', 'ajuste', 'Fundo do Story', 'blur'),
            ('Story e link', 'link', 'endereco', 'Endereço do adesivo', ''),
            ('Story e link', 'link', 'texto', 'Texto do adesivo', ''),
            ('Story e link', 'link', 'estilo', 'Estilo do adesivo', 'azul'),
            ('Story e link', 'link', 'tamanho', 'Tamanho (%)', 100),
            ('Story e link', 'link', 'posicaoX', 'Posição X (%)', 50),
            ('Story e link', 'link', 'posicaoY', 'Posição Y (%)', 80),
            ('Destaque e conexão', 'destaque', 'guardar', 'Guardar Story no Destaque', True),
            ('Destaque e conexão', 'destaque', 'titulo', 'Título do Destaque', ''),
            ('Destaque e conexão', 'destaque', 'capa', 'Capa do Destaque', ''),
            ('Destaque e conexão', 'vexion', 'link', 'Link de conexão', ''),
            ('Destaque e conexão', 'seguranca', 'intervaloEtapasMs', 'Intervalo entre etapas (ms)', 8000),
        )
        midias = {'foto', 'arquivo', 'capa'}
        alternativas = {'ajuste': ('blur', 'black', 'cover'), 'estilo': ('azul', 'preto', 'colorido', 'celular')}
        entradas = {}
        for titulo, grupo, chave, rotulo, padrao in campos:
            aba = abas[titulo]
            linha = aba.grid_size()[1]
            atual = dados.get(grupo, {}).get(chave, padrao)
            ttk.Label(aba, text=rotulo).grid(row=linha, column=0, sticky='nw', padx=(0, 10), pady=5)
            if isinstance(padrao, bool):
                var = tk.BooleanVar(value=bool(atual))
                ttk.Checkbutton(aba, variable=var).grid(row=linha, column=1, sticky='w', pady=5)
                entradas[grupo, chave] = var.get
                continue
            if chave == 'biografia':
                bio = tk.Text(aba, height=4, width=25, wrap='word')
                bio.insert('1.0', str(atual))
                bio.grid(row=linha, column=1, sticky='ew', pady=5)
                entradas[grupo, chave] = lambda widget=bio: widget.get('1.0', 'end-1c')
                continue
            var = tk.StringVar(value=str(atual))
            if chave in alternativas:
                entrada = ttk.Combobox(aba, textvariable=var, values=alternativas[chave], state='readonly')
            else:
                entrada = ttk.Entry(aba, textvariable=var)
            entrada.grid(row=linha, column=1, sticky='ew', pady=5)
            entradas[grupo, chave] = var.get
            if chave in midias:
                def escolher(campo=var):
                    caminho = filedialog.askopenfilename(parent=janela)
                    if caminho:
                        campo.set(caminho)
                ttk.Button(aba, text='Escolher', command=escolher).grid(row=linha, column=2, padx=(7, 0))

        def salvar():
            novos = {grupo: dict(valores) if isinstance(valores, dict) else valores for grupo, valores in dados.items()}
            copiar = []
            try:
                for _, grupo, chave, rotulo, padrao in campos:
                    valor = entradas[grupo, chave]()
                    if type(padrao) is int:
                        try:
                            valor = int(valor)
                        except ValueError:
                            raise ValueError(f'Informe um número inteiro em {rotulo}.') from None
                    if chave in midias and valor and not valor.startswith('padrao-assets/'):
                        origem = Path(valor)
                        formatos = {'.png', '.jpg', '.jpeg', '.webp'}
                        if chave == 'arquivo':
                            formatos.update({'.mp4', '.mov', '.webm'})
                        if not origem.is_file() or origem.suffix.lower() not in formatos:
                            raise ValueError(f'Escolha um arquivo compatível em {rotulo}.')
                        valor = f'padrao-assets/painel-{chave}-{uuid.uuid4().hex[:12]}{origem.suffix.lower()}'
                        copiar.append((origem, arquivo.parent / valor))
                    if grupo == 'vexion' and valor and not core.link_vexion_valido(valor):
                        raise ValueError('Informe um link de conexão válido da Vexion.')
                    novos.setdefault(grupo, {})[chave] = valor
                for origem, destino in copiar:
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(origem, destino)
                temporario = arquivo.with_name(f'.padrao-{uuid.uuid4().hex}.tmp')
                try:
                    temporario.write_text(prefixo + marcador + ' ' + json.dumps(novos, ensure_ascii=False, indent=2) + ';\n', encoding='utf-8')
                    temporario.replace(arquivo)
                finally:
                    temporario.unlink(missing_ok=True)
            except (OSError, ValueError) as exc:
                messagebox.showerror('VexionFlow', str(exc), parent=janela)
                return
            messagebox.showinfo('VexionFlow', 'Parâmetros salvos. Reabra as janelas do navegador para aplicar.', parent=janela)
            janela.destroy()

        ttk.Button(janela, text='Salvar parâmetros', command=salvar).pack(anchor='e', padx=12, pady=(0, 12))

    def abrir_proxies(self):
        from proxy_manager import open_manager
        open_manager(self.root)

    def abrir_painel_contas(self):
        from importlib.machinery import SourceFileLoader
        caminho = Path(__file__).with_name('painel_contas.PY')
        if not caminho.is_file():
            caminho = caminho.with_suffix('.py')
        loader = SourceFileLoader('painel_contas', str(caminho))
        spec = importlib.util.spec_from_file_location(loader.name, caminho, loader=loader)
        modulo = importlib.util.module_from_spec(spec)
        loader.exec_module(modulo)
        modulo.PainelContas(self.root, self.abrir_perfil_salvo, lambda: self.chave.get().strip())

    def abrir_perfil_salvo(self, contas):
        if self.ocupado:
            messagebox.showwarning('Operação em andamento', 'Encerre a operação atual antes de abrir os perfis.')
            return
        perfis = {core.pasta_perfil(dados): dados for dados in contas}
        disponiveis = [dados for pasta, dados in perfis.items() if pasta.is_dir()]
        faltantes = len(perfis) - len(disponiveis)
        if faltantes:
            messagebox.showwarning('Perfis ausentes', f'{faltantes} conta(s) não têm perfil salvo nesta pasta.')
        if not disponiveis or not self.navegador_disponivel():
            return
        parar = self.encerrar = threading.Event()

        def tarefa():
            proxies = core.proxies_para_janelas(len(disponiveis))
            self.fila.put(('perfis', len(disponiveis)))
            with ThreadPoolExecutor(max_workers=len(disponiveis)) as executor:
                tarefas = {
                    executor.submit(core.abrir_sessao, dados, parar, proxy_lote=proxy): dados['order_id']
                    for dados, proxy in zip(disponiveis, proxies or [None] * len(disponiveis))
                }
                for futuro in as_completed(tarefas):
                    try:
                        futuro.result()
                    except Exception as exc:
                        self.fila.put(('log', f'Perfil {tarefas[futuro]}: {exc}\n'))

        self.iniciar_tarefa(tarefa, 'Abrindo perfis salvos...')

    def _animar(self):
        """Faixa de luz e indicador pulsante, executados sem bloquear a janela."""
        largura = max(self.faixa_animada.winfo_width(), 1)
        self.faixa_animada.delete('brilho')
        self._animacao_pos = (self._animacao_pos + 5) % (largura + 180)
        x = self._animacao_pos - 180
        self.faixa_animada.create_rectangle(x, 0, x + 180, 3, fill='#b47af5', outline='', tags='brilho')
        cores = ('#555555', '#999999', '#777777', '#999999')
        self.luz.configure(fg=cores[(self._animacao_passo // 6) % len(cores)])
        self._animacao_passo += 1
        self.root.after(70, self._animar)

    def logar(self, texto):
        self.log.configure(state='normal'); self.log.insert('end', texto); self.log.see('end'); self.log.configure(state='disabled')
    def listar(self):
        chave = self.validar()
        if not chave:
            return

        def tarefa():
            resposta = Toless(chave).request('/orders?limit=20&offset=0')
            pedidos = [str(item['id']) for item in resposta['orders']]
            self.fila.put(('pedidos', pedidos))
            self.fila.put(('log', f'{len(pedidos)} pedido(s) recentes.\n'))

        self.iniciar_tarefa(tarefa, 'Consultando histórico...')

    def validar(self):
        chave = self.chave.get().strip()
        if not chave.startswith('tl_live_') or len(chave) <= len('tl_live_'):
            messagebox.showwarning('Chave necessária', 'Informe uma chave Toless OTP começando com tl_live_.')
            return None
        self.salvar_chave(chave)
        return chave

    def salvar_chave(self, chave):
        if chave == self.chave_salva or not chave.startswith('tl_live_') or len(chave) <= len('tl_live_'):
            return
        if not chave.isascii() or any(caractere.isspace() for caractere in chave):
            return
        try:
            self.chave_local.gravar(chave)
        except (OSError, ValueError, subprocess.SubprocessError):
            messagebox.showwarning('Chave local', 'Não foi possível salvar a chave neste usuário do Windows.')
        else:
            self.chave_salva = chave

    def iniciar_tarefa(self, tarefa, nome):
        if self.ocupado: return
        self.ocupado = True; self.status.set(nome)
        for b in (self.catalogo, self.comprar, self.retomar, self.atualizar, self.lote):
            b.configure(state='disabled')
        def executar():
            try: tarefa()
            except Exception as exc: self.fila.put(('erro', str(exc)))
            finally: self.fila.put(('fim', ''))
        threading.Thread(target=executar, daemon=True).start()
    def processar(self):
        try:
            while True:
                tipo, valor = self.fila.get_nowait()
                if tipo == 'log': self.logar(valor)
                elif tipo == 'erro':
                    self.logar('\nErro: ' + valor + '\n'); self.status.set('Ocorreu um erro. Veja os detalhes acima.')
                elif tipo == 'perfis':
                    self.parar.configure(text='Encerrar perfis', state='normal')
                    self.status.set(f'{valor} perfil(is) em execução.')
                elif tipo == 'lote': self.parar.configure(text='Parar lote', state='normal')
                elif tipo == 'navegador': self.parar.configure(text='Encerrar navegador', state='normal'); self.status.set('Navegador em execução.')
                elif tipo == 'acompanhar': self.parar.configure(text='Parar acompanhamento', state='normal'); self.status.set('Acompanhando pedido Toless...')
                elif tipo == 'produtos':
                    self.produtos = valor
                    self.seletor['values'] = list(valor)
                    self.produto.set(next(iter(valor), ''))
                elif tipo == 'pedidos': self.combo['values'] = valor
                elif tipo == 'compra_rejeitada': self.compra_pendente = None
                elif tipo == 'comprado':
                    self.compra_pendente = None
                    self.pedido.set(str(valor))
                elif tipo == 'fim':
                    self.ocupado=False; self.encerrar=None; self.parar.configure(state='disabled')
                    for b in (self.catalogo, self.comprar, self.retomar, self.atualizar, self.lote):
                        b.configure(state='normal')
                    if not self.status.get().startswith('Ocorreu'): self.status.set('Operação finalizada. Confira o resultado acima.')
        except queue.Empty: pass
        self.root.after(100, self.processar)
    def consultar(self):
        chave = self.validar()
        if not chave:
            return
        self.produtos = {}
        self.produto.set('')
        self.seletor['values'] = ()

        def tarefa():
            api = Toless(chave)
            saldo = api.request('/balance')
            resposta = api.request('/products')
            produtos = {}
            for item in resposta['products']:
                if item['in_stock']:
                    valor = self.reais(item['price_cents'])
                    rotulo = f"{item['name']} · {item['max_codes']} código(s) · {valor} · {item['id']}"
                    produtos[rotulo] = item
            self.fila.put(('produtos', produtos))
            self.fila.put(('log', f"Saldo: {self.reais(saldo['balance_cents'])} · {len(produtos)} produto(s) em estoque.\n"))

        self.iniciar_tarefa(tarefa, 'Consultando saldo e produtos...')

    @staticmethod
    def reais(centavos):
        return f'R$ {centavos // 100},{centavos % 100:02d}'

    def mostrar_pedido(self, ordem):
        self.fila.put(('log', f"Pedido {ordem['id']} · {ordem['email']}\n"))
        self.fila.put(('log', 'Use o e-mail no cadastro e solicite o código na plataforma.\n'))

    def monitorar(self, api, pedido, parar, ordem_inicial=None):
        self.fila.put(('acompanhar', ''))
        api.acompanhar(pedido, parar, lambda texto: self.fila.put(('log', texto)), ordem_inicial)

    def navegador_disponivel(self):
        if importlib.util.find_spec('playwright') is not None:
            return True
        messagebox.showerror('Playwright necessário', 'Instale o Playwright antes de iniciar um cadastro.')
        return False

    def varias(self):
        chave = self.validar()
        item = self.produtos.get(self.produto.get())
        if not chave or not self.navegador_disponivel():
            return
        if self.compra_pendente is not None:
            messagebox.showwarning('Compra pendente', 'Recupere a compra anterior antes de iniciar um lote.')
            return
        try:
            quantidade = int(self.quantidade.get())
            if not 1 <= quantidade <= 30:
                raise ValueError
        except ValueError:
            messagebox.showwarning('Quantidade inválida', 'Informe de 1 a 30 janelas.')
            return
        if item is None:
            messagebox.showwarning('Produto necessário', 'Selecione um produto em estoque.')
            return
        capacidade = item['max_codes']
        if not isinstance(capacidade, int) or capacidade < 1:
            messagebox.showwarning('Capacidade inválida', 'Atualize o catálogo de produtos.')
            return
        compras = (quantidade + capacidade - 1) // capacidade
        total = self.reais(compras * item['price_cents'])
        mensagem = (
            f'{quantidade} janelas, com até {capacidade} código(s) por e-mail.\nOs e-mails rodam em paralelo e os códigos do mesmo e-mail são solicitados um por vez.\n\n'
            f'{compras} compra(s) · Total: {total}. Continuar?'
        )
        if messagebox.askyesno('Confirmar lote', mensagem):
            parar = self.encerrar = threading.Event()

            def tarefa():
                self.fila.put(('lote', ''))
                core.lancar_lote(quantidade, item['id'], chave,
                                 confirmado=(item['price_cents'], capacidade), encerrar=parar)

            self.iniciar_tarefa(tarefa, 'Iniciando lote de cadastros...')

    def novo(self):
        chave = self.validar()
        if not chave:
            return
        if not self.navegador_disponivel():
            return
        item = self.produtos.get(self.produto.get())
        if self.compra_pendente is None:
            if item is None:
                messagebox.showwarning('Produto necessário', 'Consulte os produtos e selecione um item em estoque.')
                return
            preco = self.reais(item['price_cents'])
            if not messagebox.askyesno('Confirmar compra', f"Comprar {item['name']} por {preco}?"):
                return
            self.compra_pendente = (chave, item['id'], str(uuid.uuid4()))
        else:
            if chave != self.compra_pendente[0]:
                messagebox.showwarning('Compra pendente', 'Use a mesma chave da compra anterior para recuperar o resultado.')
                return
            if not messagebox.askyesno('Recuperar compra', 'Repetir a solicitação anterior com a mesma chave de idempotência?'):
                return
        _, produto_id, idempotencia = self.compra_pendente
        api = Toless(chave)
        parar = self.encerrar = threading.Event()

        def tarefa():
            try:
                ordem = api.comprar(produto_id, idempotencia)
            except TolessError as exc:
                if exc.code in ('unauthorized', 'insufficient_balance', 'daily_limit_reached',
                               'ip_not_allowed', 'browser_not_allowed', 'account_blocked',
                               'product_not_found', 'out_of_stock', 'purchase_failed', 'api_disabled'):
                    self.fila.put(('compra_rejeitada', ''))
                raise
            self.fila.put(('comprado', ordem['id']))
            dados = core.dados_do_pedido(ordem)
            self.fila.put(('navegador', ''))
            core.navegador(chave, dados, encerrar=parar)

        self.iniciar_tarefa(tarefa, 'Enviando compra ao Toless OTP...')

    def abrir_salvo(self):
        chave = self.validar()
        pedido = self.pedido.get().strip()
        if not chave:
            return
        if not pedido.isascii() or not pedido.isdecimal():
            messagebox.showwarning('Pedido necessário', 'Informe o ID numérico do pedido Toless.')
            return
        api = Toless(chave)
        parar = self.encerrar = threading.Event()

        def tarefa():
            ordem = api.request(f'/orders/{pedido}')['order']
            dados = core._ler_pedido(f'toless-{pedido}')
            if dados is not None and importlib.util.find_spec('playwright') is not None:
                self.fila.put(('navegador', ''))
                core.navegador(chave, dados, encerrar=parar)
            else:
                self.mostrar_pedido(ordem)
                self.monitorar(api, pedido, parar, ordem)

        self.iniciar_tarefa(tarefa, 'Consultando pedido...')

    def fechar_browser(self):
        if self.encerrar:
            self.encerrar.set()
            self.status.set('Encerrando operação...')

    def fechar(self):
        if self.ocupado and not messagebox.askyesno('Fechar', 'Uma operação está em andamento. Deseja sair? Se estiver comprando, confira o histórico antes de tentar novamente.'):
            return
        self.salvar_chave(self.chave.get().strip())
        if self.encerrar: self.encerrar.set()
        self.root.destroy()

def main():
    root=tk.Tk(); painel=Painel(root)
    sys.stdout=Fluxo(painel.fila); sys.stderr=Fluxo(painel.fila)
    root.mainloop()

if __name__ == '__main__': main()
