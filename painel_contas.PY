"""Painel de contas com estatísticas, taxa de acerto, reuso de e-mail e seleção fixa."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path

import assistente as core

ARQUIVO = Path(__file__).resolve().parent / 'situacao_contas.json'
ROTULOS = ('Criada', 'Suspensa', 'Com erro')


def horario_conta(dados):
    for campo, evento in (('criada_em', 'criação'), ('tentativa_em', 'tentativa')):
        valor = dados.get(campo)
        if not isinstance(valor, str):
            continue
        try:
            horario = datetime.fromisoformat(valor).astimezone()
        except (ValueError, OSError, OverflowError):
            continue
        return f"{horario:%d/%m/%y %H:%M:%S} ({evento})"
    return 'Sem horário registrado'

FUNDO = '#0f0b17'
CARTAO = '#1a1425'
CARTAO2 = '#171126'
BORDA = '#2c2240'
TEXTO = '#f4f0fa'
APAGADO = '#a99cc0'
ROXO = '#9b5de5'
ROXO_CLARO = '#c77dff'
VERDE = '#34d399'
AMARELO = '#fbbf24'
VERMELHO = '#f87171'
AZUL = '#60a5fa'
HOVER = '#3a2c55'


def carregar_situacoes():
    if not ARQUIVO.exists():
        return {}
    try:
        dados = json.loads(ARQUIVO.read_text(encoding='utf-8'))
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def gravar_situacoes(dados):
    temporario = ARQUIVO.with_suffix('.tmp')
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporario, ARQUIVO)


def listar_registros():
    situacoes = carregar_situacoes()
    registros = []
    quebrados = 0
    if core.PASTA.exists():
        for caminho in sorted(core.PASTA.glob('*.json')):
            if caminho.name == 'fila_codigo.json':
                continue
            try:
                dados = json.loads(caminho.read_text(encoding='utf-8'))
                if not isinstance(dados, dict) or not dados.get('order_id'):
                    raise ValueError('sem ID')
                situacao = situacoes.get(str(dados['order_id']), 'Não verificada')
                registros.append((dados, situacao))
            except (OSError, ValueError, TypeError):
                quebrados += 1
    return registros, quebrados


def _carregar_todos():
    todos = {}
    if core.PASTA.exists():
        for caminho in core.PASTA.glob('*.json'):
            try:
                dados = json.loads(caminho.read_text(encoding='utf-8'))
                if isinstance(dados, dict) and dados.get('order_id'):
                    todos[str(dados['order_id'])] = dados
            except (OSError, ValueError, TypeError):
                continue
    return todos


def _raiz_em_memoria(pedido_id, todos):
    atual = str(pedido_id)
    for _ in range(10):
        dados = todos.get(atual)
        if not dados:
            return atual
        pai = dados.get('pedido_origem')
        if not pai:
            return atual
        atual = str(pai)
    return atual


def mapa_usos():
    """Usos de cada e-mail, lidos da compra original da cadeia."""
    todos = _carregar_todos()
    resultado = {}
    for pedido in todos:
        raiz = todos.get(_raiz_em_memoria(pedido, todos)) or {}
        try:
            usos = int(raiz.get('usos') or 1)
        except (TypeError, ValueError):
            usos = 1
        resultado[pedido] = max(1, usos)
    return resultado


class PainelContas:
    def __init__(self, parent, abrir_perfil, obter_chave=None):
        self.obter_chave = obter_chave
        self.abrir_perfil = abrir_perfil
        self.janela = tk.Toplevel(parent)
        self.janela.title('Contas — estatísticas')
        self.janela.geometry('1020x700')
        self.janela.minsize(880, 600)
        self.janela.configure(bg=FUNDO)

        cab = tk.Frame(self.janela, bg=FUNDO)
        cab.pack(fill='x', padx=28, pady=(24, 4))
        tk.Label(cab, text='Estatísticas', bg=FUNDO, fg=TEXTO,
                 font=('Segoe UI', 20, 'bold')).pack(anchor='w')
        tk.Label(cab, text='Atualiza sozinho a cada 1,5 s. A seleção não se perde.',
                 bg=FUNDO, fg=APAGADO, font=('Segoe UI', 9)).pack(anchor='w', pady=(2, 0))

        barra = tk.Frame(self.janela, bg=FUNDO)
        barra.pack(fill='x', padx=28, pady=(12, 6))
        self.numeros = {}
        self.mini = {}
        for chave, rotulo, cor in (('total', 'TOTAL', TEXTO),
                                   ('criadas', 'CRIADAS', VERDE),
                                   ('suspensas', 'SUSPENSAS', AMARELO),
                                   ('erros', 'ERROS', VERMELHO),
                                   ('producao', 'PRODUÇÃO', AZUL),
                                   ('taxa', 'TAXA DE ACERTO', VERDE)):
            cartao = tk.Frame(barra, bg=CARTAO, highlightbackground=BORDA, highlightthickness=1)
            cartao.pack(side='left', expand=True, fill='both', padx=5, pady=4)
            tk.Frame(cartao, bg=cor, width=4).pack(side='left', fill='y')
            miolo = tk.Frame(cartao, bg=CARTAO)
            miolo.pack(side='left', fill='both', expand=True, padx=(10, 14), pady=12)
            tk.Label(miolo, text=rotulo, bg=CARTAO, fg=APAGADO,
                     font=('Segoe UI', 8, 'bold')).pack(anchor='w')
            valor = tk.Label(miolo, text='0' if chave != 'taxa' else '—', bg=CARTAO, fg=cor,
                             font=('Segoe UI', 20, 'bold'))
            valor.pack(anchor='w')
            self.numeros[chave] = valor
            mini = tk.Label(miolo, text='' if chave != 'taxa' else 'criadas ÷ (criadas + erros)',
                            bg=CARTAO, fg=APAGADO, font=('Segoe UI', 7))
            mini.pack(anchor='w')
            self.mini[chave] = mini

        filtro = tk.Frame(self.janela, bg=FUNDO)
        filtro.pack(fill='x', padx=28, pady=(6, 2))
        tk.Label(filtro, text='LOG DE OPERAÇÕES', bg=FUNDO, fg=APAGADO,
                 font=('Segoe UI', 9, 'bold')).pack(side='left')
        self.filtro = tk.StringVar(value='Todos')
        caixa = ttk.Combobox(filtro, textvariable=self.filtro, width=14, state='readonly',
                             values=('Todos', 'Criada', 'Suspensa', 'Com erro'))
        caixa.pack(side='right')
        caixa.bind('<<ComboboxSelected>>', lambda _e: self.atualizar())

        area = tk.Frame(self.janela, bg=FUNDO)
        area.pack(fill='both', expand=True, padx=28, pady=(4, 8))
        colunas = ('pedido', 'usuario', 'situacao', 'usos', 'hora')
        self.tabela = ttk.Treeview(area, columns=colunas, show='headings', selectmode='extended')
        for coluna, titulo, largura in (('pedido', 'Pedido', 250),
                                        ('usuario', 'Usuário', 210),
                                        ('situacao', 'Situação', 120),
                                        ('usos', 'Usos do e-mail', 110),
                                        ('hora', 'Data e hora', 190)):
            self.tabela.heading(coluna, text=titulo)
            self.tabela.column(coluna, width=largura, anchor='w')
        self.tabela.tag_configure('par', background=CARTAO2, foreground=TEXTO)
        self.tabela.tag_configure('impar', background=CARTAO, foreground=TEXTO)
        self.tabela.tag_configure('Criada', foreground=VERDE)
        self.tabela.tag_configure('Suspensa', foreground=AMARELO)
        self.tabela.tag_configure('Com erro', foreground=VERMELHO)
        self.tabela.tag_configure('Não verificada', foreground=APAGADO)
        rolagem = ttk.Scrollbar(area, orient='vertical', command=self.tabela.yview)
        self.tabela.configure(yscrollcommand=rolagem.set)
        rolagem.pack(side='right', fill='y')
        self.tabela.pack(side='left', fill='both', expand=True)
        estilo = ttk.Style(self.janela)
        try: estilo.theme_use('clam')
        except tk.TclError: pass
        estilo.configure('Treeview', background=CARTAO, fieldbackground=CARTAO,
                         foreground=TEXTO, rowheight=26, borderwidth=0, font=('Consolas', 9))
        estilo.configure('Treeview.Heading', background=BORDA, foreground=TEXTO,
                         font=('Segoe UI', 9, 'bold'), borderwidth=0)
        estilo.map('Treeview', background=[('selected', ROXO)], foreground=[('selected', '#ffffff')])

        rodape = tk.Frame(self.janela, bg=FUNDO)
        rodape.pack(fill='x', padx=28, pady=(0, 10))
        def botao(texto, comando, principal=False):
            b = tk.Button(rodape, text=texto, command=comando,
                          bg=ROXO if principal else CARTAO,
                          fg='#ffffff' if principal else TEXTO,
                          activebackground=ROXO_CLARO if principal else HOVER,
                          activeforeground='#ffffff', relief='flat', padx=12, pady=7,
                          font=('Segoe UI', 9, 'bold' if principal else 'normal'), bd=0,
                          highlightthickness=0, cursor='hand2')
            b.pack(side='left', padx=(0, 7))
            cor_normal, cor_hover = (ROXO, ROXO_CLARO) if principal else (CARTAO, HOVER)
            b.bind('<Enter>', lambda _e: b.config(bg=cor_hover), add='+')
            b.bind('<Leave>', lambda _e: b.config(bg=cor_normal), add='+')
            return b
        botao('Abrir selecionadas', self.abrir, principal=True)
        botao('Copiar usuários', self.copiar_usuarios)
        botao('Reusar e-mail', self.reusar_email)
        botao('Marcar criada', lambda: self.marcar('Criada'))
        botao('Marcar suspensa', lambda: self.marcar('Suspensa'))
        botao('Marcar erro', lambda: self.marcar('Com erro'))
        botao('Excluir', self.excluir)
        botao('Excluir todas', self.excluir_todas)
        botao('Atualizar', self.atualizar)

        self.aviso = tk.StringVar(value='Use Ctrl ou Shift para selecionar várias contas e clique em Abrir selecionadas.')
        self.aviso_registros = tk.StringVar()
        for aviso in (self.aviso, self.aviso_registros):
            tk.Label(self.janela, textvariable=aviso, bg=FUNDO, fg=APAGADO,
                     font=('Segoe UI', 9)).pack(anchor='w', padx=28, pady=(0, 4))

        self.atualizar()
        self.janela.after(1500, self._laco)

    def _laco(self):
        if not self.janela.winfo_exists():
            return
        self.atualizar()
        self.janela.after(1500, self._laco)

    def atualizar(self):
        registros, quebrados = listar_registros()
        usos = mapa_usos()

        selecionados = set()
        for iid in self.tabela.selection():
            valores = self.tabela.item(iid, 'values')
            if valores:
                selecionados.add(str(valores[0]))
        topo = self.tabela.yview()[0]

        filtro = self.filtro.get()
        self.registros = registros
        for item in self.tabela.get_children():
            self.tabela.delete(item)

        contagem = {'Criada': 0, 'Suspensa': 0, 'Com erro': 0}
        iid_por_pedido = {}
        for indice, (dados, situacao) in enumerate(registros):
            pedido = str(dados.get('order_id', ''))
            if situacao in contagem:
                contagem[situacao] += 1
            if filtro != 'Todos' and situacao != filtro:
                continue
            marcas = [situacao] if situacao in ROTULOS else []
            marcas.append('par' if indice % 2 == 0 else 'impar')
            iid = str(indice)
            iid_por_pedido[pedido] = iid
            limite = dados.get("max_codes") or "?"
            usos_txt = f"{usos.get(pedido, 1)}/{limite}"
            self.tabela.insert('', 'end', iid=iid,
                               values=(pedido, str(dados.get('usuario', '')),
                                       situacao, usos_txt, horario_conta(dados)),
                               tags=tuple(marcas))

        # Devolve seleção e rolagem sem quebrar nunca.
        try:
            voltar = [iid_por_pedido[p] for p in selecionados if p in iid_por_pedido]
            if voltar:
                self.tabela.selection_set(voltar)
                self.tabela.focus(voltar[0])
            self.tabela.yview_moveto(topo)
        except tk.TclError:
            pass

        total = len(registros)
        processadas = contagem['Criada'] + contagem['Suspensa'] + contagem['Com erro']
        base = contagem['Criada'] + contagem['Com erro']
        self.numeros['total'].config(text=str(total))
        self.numeros['criadas'].config(text=str(contagem['Criada']))
        self.numeros['suspensas'].config(text=str(contagem['Suspensa']))
        self.numeros['erros'].config(text=str(contagem['Com erro']))
        self.numeros['producao'].config(text=str(processadas))
        if base:
            taxa = 100 * contagem['Criada'] / base
            cor = VERDE if taxa >= 80 else (AMARELO if taxa >= 50 else VERMELHO)
            self.numeros['taxa'].config(text=f'{taxa:.0f}%', fg=cor)
            self.mini['taxa'].config(text=f"{contagem['Criada']} criadas de {base} concluídas")
        else:
            self.numeros['taxa'].config(text='—', fg=APAGADO)
            self.mini['taxa'].config(text='criadas ÷ (criadas + erros)')
        por_id = {str(dados['order_id']): dados for dados, _ in registros}
        filhos = {}
        for pedido in por_id:
            raiz = _raiz_em_memoria(pedido, por_id)
            filhos[raiz] = filhos.get(raiz, 0) + 1
        faltantes = sum(max(0, usos.get(raiz, 1) - total_raiz)
                        for raiz, total_raiz in filhos.items() if raiz in por_id)
        avisos = []
        if quebrados:
            avisos.append(f'{quebrados} arquivo(s) de conta não puderam ser lidos.')
        if faltantes:
            avisos.append(f'{faltantes} uso(s) de e-mail sem registro de conta nesta pasta.')
        self.aviso_registros.set(' '.join(avisos))

    def selecionado(self):
        escolha = self.tabela.selection()
        if not escolha:
            messagebox.showwarning('Selecione', 'Escolha pelo menos uma conta na lista.', parent=self.janela)
            return None
        valores = self.tabela.item(escolha[-1], 'values')
        pedido = str(valores[0])
        for dados, _ in self.registros:
            if str(dados.get('order_id')) == pedido:
                return dados
        return None

    # ------------------------------------------------------------------
    # Reuso: um clique cria os pedidos e já abre o cadastro completo
    # ------------------------------------------------------------------
    def reusar_email(self):
        """Cria contas novas com o e-mail do pedido selecionado e inicia
        o cadastro normal em janelas próprias, automaticamente."""
        dados = self.selecionado()
        if dados is None:
            return
        raiz_id = core._pedido_raiz(str(dados.get('order_id')))
        raiz = core._ler_pedido(raiz_id)
        if not raiz:
            messagebox.showerror('Erro', 'Não consegui ler o pedido de compra original.', parent=self.janela)
            return
        email = str(raiz.get('email', ''))
        try:
            usos = int(raiz.get('usos') or 1)
        except (TypeError, ValueError):
            usos = 1
        limite = raiz.get('max_codes')
        if limite is not None and usos >= limite:
            messagebox.showinfo('E-mail esgotado',
                                f'O e-mail {email} já foi usado {usos} vezes (limite {limite}).\n'
                                'Compre um e-mail novo para continuar.',
                                parent=self.janela)
            return
        n = 1
        if not messagebox.askyesno(
            'Confirmar reuso',
            f'Criar {n} conta(s) nova(s) com o e-mail {email}?\n\n'
            'Não haverá nova compra. Cada conta terá nome, usuário e senha próprios\n'
            'e o cadastro vai rodar completo, numa janela para cada uma.',
            parent=self.janela):
            return
        chave = self.obter_chave() if self.obter_chave else os.environ.get('TOLESS_TOKEN', '').strip()
        if not chave:
            chave = simpledialog.askstring('Chave da API',
                                           'Digite a chave da API Toless OTP (não aparece):',
                                           show='•', parent=self.janela)
            if not chave:
                return
        self.aviso.set(f'Criando {n} pedido(s) de reuso para {email} e abrindo os cadastros...')

        def tarefa():
            criados = []
            for _ in range(n):
                try:
                    novo = core.criar_pedido_reuso(chave, raiz_id)
                    criados.append(str(novo['order_id']))
                    print('Pedido de reuso criado:', novo['order_id'], '| Usuário:', novo['usuario'])
                except Exception as exc:
                    print('Reuso interrompido:', str(exc)[:200])
                    break
            if not criados:
                print('Nenhum pedido de reuso criado; nenhuma janela foi aberta.')
                return
            meu = Path(core.__file__).resolve()
            ambiente = os.environ.copy()
            ambiente['TOLESS_TOKEN'] = chave
            ambiente['PYTHONUNBUFFERED'] = '1'
            for i, pedido_id in enumerate(criados):
                flags = subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
                try:
                    subprocess.Popen([sys.executable, str(meu), '--retomar', pedido_id],
                                     cwd=str(meu.parent), env=ambiente, creationflags=flags)
                    print(f'Janela de reuso {i + 1}/{len(criados)} aberta ({pedido_id}). Cadastro em andamento.')
                except Exception as exc:
                    print('Falha ao abrir janela:', str(exc)[:200])
                time.sleep(3)
            try:
                self.janela.after(0, lambda: self.aviso.set(
                    f'{len(criados)} cadastro(s) de reuso lançado(s) para {email}. '
                    'O código vem da compra original, uma conta por vez.'))
                self.janela.after(0, self.atualizar)
            except Exception:
                pass

        threading.Thread(target=tarefa, daemon=True).start()

    def marcar(self, situacao):
        dados = self.selecionado()
        if dados is None:
            return
        situacoes = carregar_situacoes()
        situacoes[str(dados['order_id'])] = situacao
        gravar_situacoes(situacoes)
        self.atualizar()
        self.aviso.set(f'Marcado como {situacao} manualmente.')

    def copiar_usuarios(self):
        registros, _ = listar_registros()
        usuarios = list(dict.fromkeys(str(dados['usuario']).strip()
                                    for dados, situacao in registros
                                    if situacao == 'Criada' and dados.get('usuario')))
        if not usuarios:
            self.aviso.set('Nenhum usuário criado disponível para copiar.')
            return
        self.janela.clipboard_clear()
        self.janela.clipboard_append('\n'.join(usuarios))
        self.aviso.set(f'{len(usuarios)} usuário(s) criado(s) copiado(s).')

    def abrir(self):
        ids = {str(self.tabela.item(iid, 'values')[0]) for iid in self.tabela.selection()}
        dados = [conta for conta, _ in self.registros if str(conta['order_id']) in ids]
        if not dados:
            messagebox.showwarning('Selecione', 'Escolha pelo menos uma conta na lista.', parent=self.janela)
            return
        self.abrir_perfil(dados)

    def excluir(self):
        dados = self.selecionado()
        if dados is None:
            return
        if not messagebox.askyesno('Excluir',
                f'Excluir a conta "{dados.get("usuario", "?")}"?\n\n'
                'O registro (nome, usuário, senha, e-mail) será apagado.', parent=self.janela):
            return
        apagar_navegador = messagebox.askyesno('Navegador salvo',
            'Apagar também o perfil do navegador (cookies e sessão)?', parent=self.janela)
        self._remover(dados, apagar_navegador)
        self.atualizar()

    def excluir_todas(self):
        registros, _ = listar_registros()
        if not registros:
            messagebox.showinfo('Nada para excluir', 'Não há contas na lista.', parent=self.janela)
            return
        if not messagebox.askyesno('Excluir todas',
                f'Excluir TODAS as {len(registros)} contas?\n\n'
                'Os registros serão apagados desta instalação.', parent=self.janela):
            return
        apagar_navegador = messagebox.askyesno('Navegadores salvos',
            'Apagar também os perfis de navegador (cookies e sessões)?', parent=self.janela)
        for dados, _ in registros:
            self._remover(dados, apagar_navegador)
        self.atualizar()

    def _remover(self, dados, apagar_navegador=False):
        pedido = str(dados.get('order_id', ''))
        seguro = re.sub(r'[^a-zA-Z0-9_-]', '_', pedido)
        try:
            situacoes = carregar_situacoes()
            situacoes.pop(pedido, None)
            gravar_situacoes(situacoes)
        except (OSError, ValueError):
            pass
        try:
            caminho = core.PASTA / (seguro + '.json')
            if caminho.is_file():
                caminho.unlink()
        except OSError as exc:
            self.aviso.set(f'Falha ao apagar {seguro}: {str(exc)[:100]}')
        if apagar_navegador:
            try:
                pasta = core.pasta_perfil(dados)
                if pasta.is_dir():
                    shutil.rmtree(pasta, ignore_errors=True)
            except Exception:
                pass
