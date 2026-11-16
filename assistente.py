#!/usr/bin/env python3
"""Assistente local: Toless OTP + navegador visível. Uma conta por execução."""
import argparse, calendar, datetime as dt, getpass, json, os, random, re, secrets, string, subprocess, sys, time, unicodedata, hashlib
from pathlib import Path
import uuid
import threading
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

class TolessError(RuntimeError):
    def __init__(self, status, code, path=''):
        self.status = status
        self.code = code
        rota = f' em {path}' if path else ''
        super().__init__(f'Toless OTP — HTTP {status} ({code}){rota}.')


class SemRedirecionamento(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Toless:
    BASE = 'https://toless-otp.tech/api/v1'
    POWERSHELL = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$requisicao = [Console]::In.ReadToEnd() | ConvertFrom-Json
$headers = @{ Authorization = "Bearer $($requisicao.token)"; Accept = 'application/json' }
$opcoes = @{
    Uri = $requisicao.url
    Method = 'GET'
    Headers = $headers
    UseBasicParsing = $true
    MaximumRedirection = 0
    TimeoutSec = $requisicao.timeout
}
if ($null -ne $requisicao.body) {
    $opcoes.Method = 'POST'
    $opcoes.ContentType = 'application/json'
    $opcoes.Body = $requisicao.body
    $headers['Idempotency-Key'] = $requisicao.idempotency
}
try {
    $resposta = Invoke-WebRequest @opcoes
    $resultado = @{ status = [int]$resposta.StatusCode; body = [string]$resposta.Content }
} catch {
    $resposta = $_.Exception.Response
    if ($null -eq $resposta) {
        $resultado = @{ status = 0; body = '' }
    } else {
        $resultado = @{ status = [int]$resposta.StatusCode; body = [string]$_.ErrorDetails.Message }
    }
}
[Console]::WriteLine(($resultado | ConvertTo-Json -Compress))
'''

    def __init__(self, token):
        self.token = token
        self.opener = build_opener(SemRedirecionamento())

    def request(self, path, product_id=None, idempotency_key=None):
        if os.name == 'nt':
            return self._request_windows(path, product_id, idempotency_key)
        headers = {'Authorization': f'Bearer {self.token}', 'Accept': 'application/json'}
        data = None
        if product_id is not None:
            headers['Content-Type'] = 'application/json'
            headers['Idempotency-Key'] = idempotency_key
            data = json.dumps({'product_id': product_id}).encode('utf-8')
        request = Request(self.BASE + path, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=100 if data is not None else 15) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                payload = json.load(exc)
                code = payload.get('code', 'http_error')
            except (ValueError, AttributeError):
                code = 'http_error'
            raise TolessError(exc.code, code) from None
        except (URLError, TimeoutError):
            raise RuntimeError('Falha de conexão com o Toless OTP.') from None

    def _request_windows(self, path, product_id, idempotency_key):
        timeout = 15 if product_id is None else 100
        entrada = {
            'url': self.BASE + path,
            'token': self.token,
            'body': None if product_id is None else json.dumps({'product_id': product_id}),
            'idempotency': idempotency_key,
            'timeout': timeout,
        }
        sistema = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        executavel = sistema / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        try:
            processo = subprocess.run(
                [str(executavel), '-NoProfile', '-NonInteractive', '-Command', self.POWERSHELL],
                input=json.dumps(entrada).encode('utf-8'), capture_output=True,
                timeout=timeout + 15, check=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            resposta = json.loads(processo.stdout.decode('utf-8-sig'))
        except (OSError, subprocess.SubprocessError, ValueError):
            raise RuntimeError(f'Falha de comunicação com o Toless OTP em {path}.') from None
        status = resposta['status']
        if status == 0:
            raise RuntimeError(f'Falha de conexão com o Toless OTP em {path}.')
        try:
            dados = json.loads(resposta['body'])
        except ValueError:
            if 200 <= status < 300:
                raise RuntimeError(f'O Toless OTP retornou uma resposta fora do formato JSON em {path}.') from None
            raise TolessError(status, 'resposta_sem_json', path) from None
        if not 200 <= status < 300:
            codigo = dados.get('code', 'http_error') if isinstance(dados, dict) else 'http_error'
            raise TolessError(status, codigo, path)
        return dados

    def comprar(self, product_id, idempotency_key):
        return self.request('/orders', product_id, idempotency_key)['order']

    def acompanhar(self, pedido, parar, mostrar, ordem_inicial=None):
        inicio = time.monotonic()
        vistos = 0
        while not parar.is_set() and time.monotonic() - inicio < 1200:
            if ordem_inicial is not None:
                ordem, ordem_inicial = ordem_inicial, None
            else:
                try:
                    ordem = self.request(f'/orders/{pedido}')['order']
                except TolessError as exc:
                    if exc.status != 429:
                        raise
                    parar.wait(10)
                    continue
            codigos = ordem.get('codes', [])
            for item in codigos[vistos:]:
                mostrar(f'Pedido {pedido} · Código: {item["code"]}\n')
            vistos = max(vistos, len(codigos))
            if ordem['status'] in ('completed', 'refunded', 'failed'):
                mostrar(f'Pedido {pedido} · {ordem["status"]}\n')
                return
            parar.wait(5)
        if not parar.is_set():
            mostrar(f'Pedido {pedido} · Tempo de consulta encerrado. Use Retomar pedido para consultar novamente.\n')

CADASTRO = 'https://www.instagram.com/accounts/signup/email/'
PASTA = Path('dados_locais')
NOMES = ['Ana', 'Beatriz', 'Camila', 'Carolina', 'Clara', 'Fernanda', 'Gabriela', 'Isabela', 'Júlia', 'Larissa', 'Letícia', 'Luana', 'Mariana', 'Natália', 'Rafaela', 'Vitória']
SOBRENOMES = ['Almeida', 'Barbosa', 'Cardoso', 'Costa', 'Lima', 'Martins', 'Oliveira', 'Pereira', 'Rocha', 'Santos', 'Silva', 'Souza']

MACBOOKS = [
    ('air13_m2', 'MacBook Air 13 M2', 'Apple M2', 8, 1470, 956),
    ('air13_m3', 'MacBook Air 13 M3', 'Apple M3', 8, 1470, 956),
    ('air15_m3', 'MacBook Air 15 M3', 'Apple M3', 8, 1710, 1112),
    ('pro14_m2pro', 'MacBook Pro 14 M2 Pro', 'Apple M2 Pro', 10, 1512, 982),
    ('pro14_m3pro', 'MacBook Pro 14 M3 Pro', 'Apple M3 Pro', 11, 1512, 982),
    ('pro16_m2max', 'MacBook Pro 16 M2 Max', 'Apple M2 Max', 12, 1728, 1117),
    ('pro16_m3max', 'MacBook Pro 16 M3 Max', 'Apple M3 Max', 12, 1728, 1117),
]

SITUACOES = Path(__file__).resolve().parent / 'situacao_contas.json'
_SITUACOES_LOCK = threading.Lock()

SCRIPT_FINGERPRINT = """
(() => {
  const cfg = __CFG__;
  const def = (alvo, prop, valor) => {
    try { Object.defineProperty(alvo, prop, { get: () => valor, configurable: true }); } catch (e) {}
  };
  def(Navigator.prototype, 'hardwareConcurrency', cfg.hardwareConcurrency);
  def(Navigator.prototype, 'deviceMemory', cfg.deviceMemory);
  def(Navigator.prototype, 'platform', cfg.platform);
  def(Navigator.prototype, 'vendor', cfg.vendor);
  def(Navigator.prototype, 'maxTouchPoints', cfg.maxTouchPoints);
  def(Navigator.prototype, 'languages', cfg.languages);
  try { Object.defineProperty(navigator, 'webdriver', { get: () => false, configurable: true }); } catch (e) {}
  const patchGL = (proto) => {
    if (!proto) return;
    const original = proto.getParameter;
    proto.getParameter = function (param) {
      if (param === 37445) return cfg.webglVendor;
      if (param === 37446) return cfg.webglRenderer;
      return original.call(this, param);
    };
  };
  patchGL(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
  patchGL(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);

  let s = cfg.seed;
  const rnd = () => { s = (s * 1664525 + 1013904223) % 4294967296; return s / 4294967296; };

  const marcado = new WeakSet();
  const toDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function (...args) {
    if (!marcado.has(this)) {
      marcado.add(this);
      try {
        const c = this.getContext('2d');
        if (c) {
          const d = c.getImageData(0, 0, this.width, this.height);
          if (d.data.length >= 4) {
            const p = Math.floor(rnd() * (d.data.length / 4)) * 4;
            d.data[p] = (d.data[p] + 1) & 255;
            c.putImageData(d, 0, 0);
          }
        }
      } catch (e) {}
    }
    return toDataURL.apply(this, args);
  };

  if (window.AudioBuffer) {
    const origem = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function (canal) {
      const arr = origem.call(this, canal);
      if (!this.__marcado) {
        this.__marcado = true;
        for (let i = 0; i < arr.length; i += 1000) arr[i] += (rnd() - 0.5) * 1e-7;
      }
      return arr;
    };
  }

  if (navigator.geolocation) {
    const posicao = {
      coords: { latitude: cfg.latitude, longitude: cfg.longitude, accuracy: 35 + Math.floor(rnd() * 40), altitude: null, altitudeAccuracy: null, heading: null, speed: null },
      timestamp: Date.now(),
    };
    navigator.geolocation.getCurrentPosition = function (ok, erro, opcoes) { try { ok(posicao); } catch (e) {} };
    navigator.geolocation.watchPosition = function (ok, erro, opcoes) { try { ok(posicao); } catch (e) {} return 1; };
  }
})();
"""


def marcar_situacao(order_id, situacao):
    """Grava a situação da conta no arquivo que o painel lê."""
    if not order_id:
        return
    with _SITUACOES_LOCK:
        try:
            dados = json.loads(SITUACOES.read_text(encoding='utf-8')) if SITUACOES.exists() else {}
            if not isinstance(dados, dict):
                dados = {}
        except (OSError, ValueError):
            dados = {}
        if dados.get(str(order_id)) == situacao:
            return
        dados[str(order_id)] = situacao
        try:
            temporario = SITUACOES.with_suffix('.tmp')
            temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporario, SITUACOES)
            print('Painel atualizado: situação =', situacao)
        except OSError as exc:
            print('Não foi possível registrar a situação:', str(exc)[:120])


def _situacao_por_pagina(page):
    """Lê a página depois do código: 'Criada', 'Suspensa' ou None (ainda carregando)."""
    try:
        url = (page.url or '').lower()
    except Exception:
        url = ''
    if '/suspended' in url or 'challenge' in url:
        return 'Suspensa'
    if _esta_na_pagina_inicial_instagram(page):
        return 'Criada'
    try:
        texto = page.inner_text('body', timeout=1500).lower()
    except Exception:
        texto = ''
    if 'suspend' in texto or 'suspens' in texto or 'desativad' in texto:
        return 'Suspensa'
    return None


def _situacao_gravada(order_id):
    """Situação já registrada para o pedido, ou None se nenhuma."""
    if not order_id:
        return None
    try:
        dados = json.loads(SITUACOES.read_text(encoding='utf-8')) if SITUACOES.exists() else {}
        return dados.get(str(order_id)) if isinstance(dados, dict) else None
    except (OSError, ValueError):
        return None


def _versao_chrome(executavel, padrao=131):
    caminho = Path(executavel)
    partes = caminho.parts
    if 'Contents' in partes:
        i = partes.index('Contents')
        info = Path(*partes[:i], 'Contents', 'Info.plist')
        try:
            import plistlib
            with info.open('rb') as f:
                versao = str(plistlib.load(f).get('CFBundleShortVersionString', '') or '')
            m = re.match(r'(\d+)\.', versao)
            if m:
                return int(m.group(1))
        except Exception:
            pass
    try:
        saida = subprocess.run([str(caminho), '--version'], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r'(\d+)\.', saida)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return padrao


def gerar_identidade(executavel=None):
    if executavel is None:
        try:
            executavel = _caminho_chromium_real()
        except Exception:
            executavel = None
    versao = _versao_chrome(executavel) if executavel else 131
    nome_navegador = Path(executavel).stem if executavel else 'Google Chrome'
    _, rotulo, chip, nucleos, largura, altura = random.choice(MACBOOKS)
    ident = {
        'id': f"{dt.datetime.now():%Y%m%d-%H%M%S}-{secrets.randbelow(0xffff):04x}",
        'criado': dt.datetime.now().isoformat(timespec='seconds'),
        'navegador': nome_navegador,
        'versao_navegador': versao,
        'preset': rotulo,
        'chip': chip,
        'user_agent': (f'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                       f'(KHTML, like Gecko) Chrome/{versao}.0.0.0 Safari/537.36'),
        'viewport': {'width': largura, 'height': altura - 90},
        'screen': {'width': largura, 'height': altura},
        'device_scale_factor': 2,
        'hardware_concurrency': nucleos,
        'device_memory': 8,
        'platform': 'MacIntel',
        'vendor': 'Google Inc.',
        'max_touch_points': 0,
        'languages': ['pt-BR', 'pt', 'en-US', 'en'],
        'locale': 'pt-BR',
        'timezone': 'America/Sao_Paulo',
        'geolocation': {'latitude': -23.5505 + random.uniform(-0.15, 0.15),
                        'longitude': -46.6333 + random.uniform(-0.15, 0.15)},
        'webgl_vendor': 'Google Inc. (Apple)',
        'webgl_renderer': f'ANGLE (Apple, ANGLE Metal Renderer: {chip}, Unspecified Version)',
        'seed': secrets.randbelow(2**31 - 1) + 1,
    }
    return validar_identidade(ident)


def validar_identidade(ident):
    if 'Macintosh' not in ident['user_agent']:
        raise ValueError('Fingerprint inválido: user agent fora do macOS.')
    if ident['platform'] != 'MacIntel':
        raise ValueError('Fingerprint inválido: platform precisa ser MacIntel.')
    if ident['device_scale_factor'] != 2:
        raise ValueError('Fingerprint inválido: tela retina esperada.')
    if not 8 <= ident['hardware_concurrency'] <= 16:
        raise ValueError('Fingerprint inválido: contagem de núcleos fora do MacBook.')
    if 'Apple' not in ident['webgl_renderer']:
        raise ValueError('Fingerprint inválido: renderer sem chip Apple.')
    if not 1 <= int(ident['seed']) < 2**31:
        raise ValueError('Fingerprint inválido: seed fora do intervalo.')
    return ident


def _configuracao_js(ident):
    return json.dumps({
        'hardwareConcurrency': ident['hardware_concurrency'],
        'deviceMemory': ident['device_memory'],
        'platform': ident['platform'],
        'vendor': ident['vendor'],
        'maxTouchPoints': ident['max_touch_points'],
        'languages': ident['languages'],
        'webglVendor': ident['webgl_vendor'],
        'webglRenderer': ident['webgl_renderer'],
        'seed': ident['seed'],
        'latitude': ident['geolocation']['latitude'],
        'longitude': ident['geolocation']['longitude'],
    }, ensure_ascii=False)


def _pastas_de_extensoes():
    """Todas as pastas com manifest.json, incluindo subpastas de extensoes/."""
    raiz = Path(__file__).resolve().parent
    pastas = []
    def incluir(pasta):
        if (pasta / 'manifest.json').is_file():
            caminho = str(pasta.resolve())
            if caminho not in pastas:
                pastas.append(caminho)
                return True
        return False
    for nome in ('extensao_contas', 'extensao_contas_extra', 'extensoes', 'extensoes_extra'):
        incluir(raiz / nome)
    pasta_multi = raiz / 'extensoes'
    if pasta_multi.is_dir():
        for sub in sorted(pasta_multi.iterdir()):
            if not sub.is_dir():
                continue
            if not incluir(sub):
                # A extensão pode estar um nível abaixo da subpasta.
                for subsub in sorted(sub.iterdir()):
                    if subsub.is_dir():
                        incluir(subsub)
    return pastas


def salvar(dados):
    PASTA.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = PASTA / (re.sub(r'[^a-zA-Z0-9_-]', '_', str(dados['order_id'])) + '.json')
    fd, nome_tmp = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    temporario = Path(nome_tmp)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False, indent=2)
        temporario.replace(path)
    finally:
        temporario.unlink(missing_ok=True)
    return path


def _ler_pedido(pedido_id):
    """Lê o arquivo de um pedido pelo ID, ou None se não existir."""
    seguro = re.sub(r'[^a-zA-Z0-9_-]', '_', str(pedido_id))
    caminho = PASTA / (seguro + '.json')
    if not caminho.exists():
        return None
    try:
        return json.loads(caminho.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _pedido_raiz(pedido_id):
    """Segue a cadeia de reusos até o pedido de compra original."""
    atual = str(pedido_id)
    for _ in range(10):
        dados = _ler_pedido(atual)
        if not dados:
            return atual
        pai = dados.get('pedido_origem')
        if not pai:
            return atual
        atual = str(pai)
    return atual


def perfil():
    primeiro, ultimo = secrets.choice(NOMES), secrets.choice(SOBRENOMES)
    nome = primeiro + ' ' + ultimo
    hoje = dt.date.today()
    ano = random.SystemRandom().randint(hoje.year - 45, hoje.year - 20)
    mes = secrets.randbelow(12) + 1
    dia = secrets.randbelow(calendar.monthrange(ano, mes)[1]) + 1
    nascimento = dt.date(ano, mes, dia)
    if not idade_acima_de_18(nascimento.isoformat(), hoje):
        raise RuntimeError('A data gerada não corresponde a mais de 18 anos.')
    limpa = lambda s: ''.join(c for c in unicodedata.normalize('NFKD', s.lower()) if c.isascii() and c.isalpha())
    usuario = limpa(primeiro) + '.' + limpa(ultimo) + str(secrets.randbelow(900000) + 100000)
    digitos = string.digits
    especiais = '!@#$%&*?'
    corpo = ''.join(secrets.choice(string.ascii_letters + digitos) for _ in range(secrets.randbelow(6) + 12))
    numero = ''.join(secrets.choice(digitos) for _ in range(secrets.randbelow(3) + 1))
    especial = secrets.choice(especiais)
    formato = secrets.randbelow(4)
    if formato == 0:
        senha = corpo + especial + numero
    elif formato == 1:
        senha = numero + especial + corpo
    elif formato == 2:
        senha = corpo[0].upper() + corpo[1:] + numero + especial
    else:
        senha = especial + corpo + numero
    if len(senha) < 10:
        senha = senha + especial + '9'
    return {'nome': nome, 'usuario': usuario, 'senha': senha, 'nascimento': nascimento.isoformat()}


def _digitar_humano(page, loc, valor, timeout=20000):
    """Digita quase instantaneamente, sem pausas de humanização."""
    try:
        loc.click(timeout=3000)
    except Exception:
        pass
    try:
        loc.fill('', timeout=1500)
    except Exception:
        pass
    loc.type(str(valor), delay=2, timeout=timeout)

def preencher(page, seletores, valor):
    for seletor in seletores:
        try:
            loc = page.locator(seletor).first
            if loc.count() and loc.is_visible() and loc.is_editable():
                _digitar_humano(page, loc, valor)
                return True
        except Exception: pass
    rotulos = []
    unificados = ' '.join(seletores).lower()
    if 'celular ou email' in unificados:
        rotulos = ['número de celular ou email', 'número de celular ou e-mail']
    elif 'nome completo' in unificados:
        rotulos = ['nome', 'nome completo']
    for texto in rotulos:
        try:
            por_label = page.get_by_label(re.compile(r'^\s*' + re.escape(texto) + r'\s*$', re.I), exact=False)
            for i in range(por_label.count()):
                loc = por_label.nth(i)
                if loc.is_visible() and loc.is_editable():
                    _digitar_humano(page, loc, valor); return True
        except Exception: pass
        try:
            rotulo = page.get_by_text(re.compile(r'^\s*' + re.escape(texto) + r'\s*$', re.I))
            for i in range(rotulo.count()):
                el = rotulo.nth(i)
                if not el.is_visible(): continue
                for nivel in range(1, 5):
                    grupo = el.locator('xpath=' + '/'.join(['..'] * nivel))
                    entradas = grupo.locator('input:not([type=hidden]):visible')
                    if entradas.count() == 1 and entradas.first.is_editable():
                        _digitar_humano(page, entradas.first, valor); return True
                    if entradas.count() > 1: break
        except Exception: pass
    return False


def botao(page, nomes):
    for nome in nomes:
        try:
            grupo = page.get_by_role('button', name=re.compile(nome, re.I))
            for i in range(min(grupo.count(), 20)):
                loc = grupo.nth(i)
                try:
                    if not loc.is_visible() or not loc.is_enabled():
                        continue
                    loc.scroll_into_view_if_needed(timeout=3000)
                    loc.click(timeout=4000)
                    return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def idade_acima_de_18(data, hoje=None):
    hoje = hoje or dt.date.today()
    try:
        d = dt.date.fromisoformat(data)
        try:
            limite = hoje.replace(year=hoje.year - 18)
        except ValueError:
            limite = hoje.replace(year=hoje.year - 18, day=28)
        return d < limite
    except (TypeError, ValueError, OverflowError):
        return False


def _controles_data(page):
    aliases = {'dia': ('dia', 'day'), 'mês': ('mês', 'mes', 'month'), 'ano': ('ano', 'year')}
    resultado = {}
    anteriores = getattr(page, '_controles_nascimento', {})
    for nome, valores in aliases.items():
        antigo = anteriores.get(nome)
        try:
            if antigo is not None and antigo.count() and antigo.is_visible():
                resultado[nome] = antigo
                continue
        except Exception: pass
        padrao = re.compile(r'^\s*(?:' + '|'.join(map(re.escape, valores)) + r')(?:\s|$)', re.I)
        exato = re.compile(r'^\s*(?:' + '|'.join(map(re.escape, valores)) + r')\s*$', re.I)
        grupos = [page.get_by_role('combobox', name=padrao),
                  page.get_by_role('button', name=padrao), page.get_by_label(padrao),
                  page.get_by_text(exato)]
        for atributo in ('aria-label', 'title', 'name', 'id', 'data-testid'):
            for alias in valores:
                grupos.append(page.locator(f'select[{atributo}*="{alias}" i], [role="combobox"][{atributo}*="{alias}" i]'))
        for grupo in grupos:
            try:
                for i in range(min(grupo.count(), 20)):
                    el = grupo.nth(i)
                    if not el.is_visible(): continue
                    pai = el.locator('xpath=ancestor-or-self::*[self::select or self::button or @role="combobox" or @role="button" or @aria-haspopup][1]')
                    if pai.count() and pai.first.is_visible(): el = pai.first
                    else:
                        continue
                    resultado[nome] = el
                    break
            except Exception: pass
            if nome in resultado: break
    for seletor in ('select:visible', '[role="combobox"]:visible'):
        try:
            elementos = page.locator(seletor)
            if elementos.count() == 3:
                for i, nome in enumerate(('dia', 'mês', 'ano')):
                    if nome not in resultado: resultado[nome] = elementos.nth(i)
        except Exception: pass
    page._controles_nascimento = resultado
    return resultado


def _data_normalizar(valor):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKD', str(valor)).encode('ascii','ignore').decode().lower()).strip()


def _data_confere(texto, nome, opcoes):
    texto = _data_normalizar(texto)
    aliases = {'dia': ('dia','day'), 'mês': ('mes','month'), 'ano': ('ano','year')}[nome]
    for alias in aliases:
        texto = re.sub(r'^' + alias + r'\s*[:\-]?\s*', '', texto)
    return texto in {_data_normalizar(x) for x in opcoes}


def nascimento(page, data):
    if not idade_acima_de_18(data):
        raise ValueError('Data de nascimento inválida ou não corresponde a mais de 18 anos.')
    d = dt.date.fromisoformat(data)
    meses = ['janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro']
    ingles = ['january','february','march','april','may','june','july','august','september','october','november','december']
    valores = {'dia': [str(d.day), f'{d.day:02d}'],
               'mês': [meses[d.month-1], ingles[d.month-1], str(d.month), f'{d.month:02d}'],
               'ano': [str(d.year)]}
    controles = _controles_data(page)
    if len(controles) != 3: return False
    for nome in ('dia', 'mês', 'ano'):
        loc, opcoes = controles[nome], valores[nome]
        try:
            tag = loc.evaluate('(e) => e.tagName.toLowerCase()')
            if tag == 'select':
                escolhido = False
                for campo in ('label', 'value'):
                    for opcao in opcoes:
                        try:
                            loc.select_option(**{campo: opcao}, timeout=1000)
                            atual = loc.evaluate('(e) => ({value:e.value, label:e.selectedOptions[0]?.textContent?.trim()})')
                            if _data_confere(atual.get('label',''), nome, opcoes): escolhido = True; break
                        except Exception: pass
                    if escolhido: break
                if not escolhido: return False
            else:
                texto = loc.inner_text(timeout=1000).strip()
                if _data_confere(texto, nome, opcoes): continue
                loc.click(timeout=1500); escolhido = False
                for opcao in opcoes:
                    regex = re.compile(r'^\s*' + re.escape(opcao) + r'\s*$', re.I)
                    for role in ('option', 'menuitem', 'button'):
                        grupo = page.get_by_role(role, name=regex)
                        for i in range(min(grupo.count(), 200)):
                            item = grupo.nth(i)
                            if item.is_visible():
                                item.scroll_into_view_if_needed(timeout=1500)
                                item.click(timeout=1500); escolhido = True; break
                        if escolhido: break
                    if not escolhido:
                        grupo = page.get_by_text(regex)
                        for i in range(min(grupo.count(), 200)):
                            item = grupo.nth(i)
                            if item.is_visible():
                                item.scroll_into_view_if_needed(timeout=1500)
                                item.click(timeout=1500); escolhido = True; break
                    if escolhido: break
                if not escolhido:
                    page.keyboard.press('Escape'); return False
                if not _data_confere(loc.inner_text(timeout=1000), nome, opcoes): return False
        except Exception:
            return False
    for nome, loc in controles.items():
        try:
            if loc.evaluate('(e) => e.tagName.toLowerCase()') == 'select':
                atual = loc.evaluate('(e) => ({value:e.value, label:e.selectedOptions[0]?.textContent?.trim()})')
                texto = atual.get('label','')
            else: texto = loc.inner_text(timeout=1000)
            if not _data_confere(texto, nome, valores[nome]): return False
        except Exception: return False
    return True


def verificar_usuario(page, usuario):
    seletores = ['input[name="username"]', 'input[autocomplete="username"]',
                 'input[placeholder*="usuário" i]', 'input[aria-label*="usuário" i]',
                 'input[placeholder*="username" i]']
    for seletor in seletores:
        try:
            campo = page.locator(seletor).first
            if not campo.count() or not campo.is_visible(): continue
            if campo.input_value().strip() != usuario: return 'alterado'
            if (campo.get_attribute('aria-invalid') or '').lower() == 'true': return 'indisponivel'
            ids = (campo.get_attribute('aria-describedby') or '').split()
            for ident in ids:
                el = page.locator('[id=' + json.dumps(ident) + ']').first
                if el.count() and el.is_visible():
                    texto = el.inner_text().lower()
                    if re.search(r'indispon[ií]vel|n[aã]o dispon[ií]vel|j[aá] (?:est[aá] )?(?:em uso|usado)|unavailable|already (?:taken|in use)', texto): return 'indisponivel'
                    if re.search(r'dispon[ií]vel|available', texto): return 'disponivel'
            grupo = campo.locator('xpath=ancestor::*[.//input][1]/..')
            if grupo.count():
                texto = grupo.inner_text(timeout=1000).lower()
                if re.search(r'indispon[ií]vel|n[aã]o dispon[ií]vel|j[aá] (?:est[aá] )?(?:em uso|usado)|unavailable|already (?:taken|in use)', texto): return 'indisponivel'
                if re.search(r'(?<!n[aã]o )dispon[ií]vel|available', texto): return 'disponivel'
            return 'nao_confirmado'
        except Exception: continue
    try:
        grupo = page.locator('input:not([type="hidden"]):not([type="password"])')
        correspondentes = []
        for i in range(min(grupo.count(), 100)):
            el = grupo.nth(i)
            if el.is_visible() and el.input_value().strip() == usuario:
                correspondentes.append(el)
        if len(correspondentes) == 1:
            campo = correspondentes[0]
            if (campo.get_attribute('aria-invalid') or '').lower() == 'true':
                return 'indisponivel'
            grupo = campo.locator('xpath=ancestor::*[.//input][1]/..')
            texto = grupo.inner_text(timeout=1000).lower() if grupo.count() else ''
            if re.search(r'indispon[ií]vel|n[aã]o dispon[ií]vel|j[aá] (?:est[aá] )?(?:em uso|usado)|unavailable|already (?:taken|in use)', texto):
                return 'indisponivel'
            return 'nao_confirmado'
    except Exception: pass
    return 'nao_encontrado'


def campo_codigo(page):
    seletores = ['input[name="email_confirmation_code"]', 'input[name="confirmationCode"]',
                 'input[name="verificationCode"]', 'input[autocomplete="one-time-code"]',
                 'input[aria-label*="código" i]', 'input[aria-label*="codigo" i]',
                 'input[aria-label*="code" i]', 'input[placeholder*="código" i]',
                 'input[placeholder*="codigo" i]', 'input[placeholder*="code" i]']
    for seletor in seletores:
        try:
            grupo = page.locator(seletor)
            for i in range(min(grupo.count(), 10)):
                campo = grupo.nth(i)
                if campo.is_visible() and campo.is_editable(): return campo
        except Exception: pass
    try:
        grupo = page.get_by_label(re.compile(r'(c[oó]digo|code)', re.I))
        for i in range(min(grupo.count(), 10)):
            campo = grupo.nth(i)
            if campo.is_visible() and campo.is_editable(): return campo
    except Exception: pass
    return None


# ---------------------------------------------------------------------------
# Fila de código por e-mail: garante que contas reusando o mesmo e-mail
# nunca consultem o código ao mesmo tempo (evita trocar os códigos).
# ---------------------------------------------------------------------------
def _pasta_fila():
    PASTA.mkdir(parents=True, exist_ok=True)
    return PASTA / 'fila_codigo.json'


def _fila_ler():
    try:
        return json.loads(_pasta_fila().read_text(encoding='utf-8')) if _pasta_fila().exists() else {}
    except (OSError, ValueError):
        return {}


def _fila_gravar(fila):
    temporario = _pasta_fila().with_suffix('.tmp')
    temporario.write_text(json.dumps(fila, ensure_ascii=False), encoding='utf-8')
    os.replace(temporario, _pasta_fila())


def _fila_liberar(email, meu_id):
    fila = _fila_ler()
    if fila.get(email) == meu_id:
        del fila[email]
        _fila_gravar(fila)


def _codigos_toless(ordem):
    return {str(item['code']).strip() for item in ordem.get('codes', [])
            if item.get('code') is not None and str(item['code']).strip()}


def esperar_codigo(chave, pedido, max_segundos=1200, encerrar=None):
    pedido_api = pedido.get('toless_id')
    if not pedido_api:
        raise RuntimeError('Este cadastro usa um pedido antigo. Digite o código no navegador.')
    anteriores = pedido.get('toless_codigos_excluidos')
    if pedido.get('pedido_origem') and not anteriores:
        raise RuntimeError('Reuso antigo sem histórico dos códigos anteriores. Confira o código novo no Toless e digite-o manualmente.')
    excluidos = set(anteriores or [])
    if pedido.get('codigo'):
        excluidos.add(str(pedido['codigo']).strip())
    api = Toless(chave)
    fim = time.monotonic() + max_segundos
    print('Aguardando código do Toless OTP...')
    while time.monotonic() < fim and not (encerrar and encerrar.is_set()):
        intervalo = 5
        try:
            ordem = api.request(f'/orders/{pedido_api}')['order']
            novos = _codigos_toless(ordem) - excluidos
            if len(novos) > 1:
                raise RuntimeError('O Toless retornou mais de um código novo. Confira qual pertence a este cadastro e digite-o manualmente.')
            if novos:
                pedido['codigo'] = novos.pop()
                pedido['toless_codigos_excluidos'] = sorted(excluidos)
                salvar(pedido)
                return pedido['codigo']
            if ordem['status'] in ('completed', 'refunded', 'failed'):
                print('Pedido Toless encerrado:', ordem['status'])
                return None
        except TolessError as exc:
            if exc.status != 429:
                raise
            intervalo = 10
        espera = min(intervalo, max(0, fim - time.monotonic()))
        if encerrar is None:
            time.sleep(espera)
        else:
            encerrar.wait(espera)
    return None


def pasta_perfil(dados):
    ident = str(dados['order_id'])
    nome = hashlib.sha256(ident.encode('utf-8')).hexdigest()
    variante = int(dados.get('fingerprint_variante') or 0)
    if variante:
        nome = f'{nome}-v{variante}'
    return Path('perfis_navegador') / nome


def obter_identidade(dados, renovar=False):
    arquivo = pasta_perfil(dados) / 'identidade.json'
    if arquivo.is_file() and not renovar:
        try:
            return validar_identidade(json.loads(arquivo.read_text(encoding='utf-8')))
        except Exception:
            pass
    ident = gerar_identidade()
    arquivo.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    ident['variante'] = int(dados.get('fingerprint_variante') or 0)
    arquivo.write_text(json.dumps(ident, indent=2, ensure_ascii=False), encoding='utf-8')
    return ident


def trocar_fingerprint(dados):
    dados['fingerprint_variante'] = int(dados.get('fingerprint_variante') or 0) + 1
    salvar(dados)
    ident = obter_identidade(dados, renovar=True)
    print('Novo fingerprint:', ident['preset'], '|', ident['chip'], '|', ident['hardware_concurrency'], 'núcleos')
    print('Perfil isolado novo:', pasta_perfil(dados))
    return ident


def _caminho_chromium_real():
    import shutil
    candidatos = []
    configuracao = Path(__file__).resolve().parent / 'configuracoes_perfil.json'
    try:
        dados_cfg = json.loads(configuracao.read_text(encoding='utf-8')) if configuracao.exists() else {}
        salvo = dados_cfg.get('navegador_executavel', '') if isinstance(dados_cfg, dict) else ''
        if salvo: candidatos.append(os.path.expandvars(os.path.expanduser(str(salvo))))
    except (OSError, ValueError):
        pass
    ambiente = os.environ.get('CHROMIUM_EXECUTABLE', '').strip()
    if ambiente: candidatos.insert(0, os.path.expandvars(os.path.expanduser(ambiente)))
    if os.name == 'nt':
        bases = [os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)'), os.environ.get('LOCALAPPDATA')]
        for base in filter(None, bases):
            candidatos.extend((os.path.join(base, 'Google', 'Chrome', 'Application', 'chrome.exe'),
                               os.path.join(base, 'Chromium', 'Application', 'chrome.exe')))
        nomes = ('chrome.exe', 'chromium.exe')
    elif sys.platform == 'darwin':
        candidatos.extend(('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                           '/Applications/Chromium.app/Contents/MacOS/Chromium'))
        nomes = ('google-chrome', 'chromium', 'chromium-browser')
    else:
        nomes = ('chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable')
    for nome in nomes:
        achado = shutil.which(nome)
        if achado: candidatos.append(achado)
    for candidato in candidatos:
        if candidato and Path(candidato).is_file():
            return str(Path(candidato).resolve())
    raise RuntimeError('Não encontrei Google Chrome/Chromium instalado. Instale um navegador Chromium ou selecione o executável em Configurações.')


def _proxy_do_processo():
    texto = os.environ.get('INSTACASTER_PROXY', '').strip()
    if not texto:
        return None
    servidor = texto if '://' in texto else 'http://' + texto
    usuario = os.environ.get('INSTACASTER_PROXY_USUARIO', '').strip() or None
    senha = os.environ.get('INSTACASTER_PROXY_SENHA', '').strip() or None
    proxy = {'server': servidor}
    if usuario:
        proxy['username'] = usuario
    if senha:
        proxy['password'] = senha
    return proxy


def _proxy_do_pedido(dados):
    proxy = dados.get('proxy') if isinstance(dados, dict) else None
    if isinstance(proxy, dict) and proxy.get('server'):
        return dict(proxy)
    return None


def _opcoes_navegador(ident, dados=None, proxy_lote=None):
    """Chromium instalado, extensões carregadas, proxy e o fingerprint sorteado."""
    pastas = _pastas_de_extensoes()
    args = [
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-features=Translate,MediaRouter',
    ]
    if pastas:
        lista = ','.join(pastas)
        args.extend([f'--disable-extensions-except={lista}', f'--load-extension={lista}'])
        print('Pastas de extensões encontradas:', len(pastas), '->', lista)
    else:
        print('Nenhuma pasta de extensão encontrada (extensao_contas/extensoes).')
    opcoes = {
        'args': args,
        'ignore_default_args': ['--enable-automation', '--no-sandbox'],
        'user_agent': ident['user_agent'],
        'viewport': ident['viewport'],
        'screen': ident['screen'],
        'device_scale_factor': ident['device_scale_factor'],
        'locale': ident['locale'],
        'timezone_id': ident['timezone'],
        'geolocation': ident['geolocation'],
        'permissions': ['geolocation'],
        'color_scheme': 'light',
    }
    if pastas:
        opcoes['channel'] = 'chromium'
        print('Navegador selecionado: Chromium do Playwright, com extensões.')
    else:
        opcoes['executable_path'] = _caminho_chromium_real()
        print('Navegador real selecionado:', opcoes['executable_path'])
    print('Fingerprint:', ident['preset'], '|', ident['chip'], '|', ident['hardware_concurrency'], 'núcleos |', ident['screen']['width'], 'x', ident['screen']['height'])
    proxy = proxy_lote or _proxy_do_pedido(dados)
    if proxy:
        print('Navegador usando proxy do lote:' if proxy_lote else 'Navegador usando proxy deste pedido:', proxy['server'])
        opcoes['proxy'] = proxy
    else:
        proxy = _proxy_do_processo()
        if proxy:
            print('Navegador usando proxy deste processo:', proxy['server'])
            opcoes['proxy'] = proxy
        else:
            try:
                from proxy_manager import get_browser_proxy
            except ModuleNotFoundError as exc:
                if exc.name != 'proxy_manager':
                    raise
            else:
                proxy = get_browser_proxy()
            if proxy:
                print('Navegador usando proxy configurada:', proxy['server'])
                opcoes['proxy'] = proxy
            else:
                print('Nenhuma proxy ativa; navegador usando conexão direta.')
    return opcoes


def _abrir_contexto(p, dados, proxy_lote=None):
    ident = obter_identidade(dados)
    caminho = pasta_perfil(dados)
    caminho.mkdir(mode=0o700, parents=True, exist_ok=True)
    opcoes = _opcoes_navegador(ident, dados, proxy_lote)
    if opcoes.get('channel') == 'chromium' and not Path(p.chromium.executable_path).is_file():
        raise RuntimeError('Instale o Chromium para carregar as extensões: py -m playwright install chromium')
    try:
        contexto = p.chromium.launch_persistent_context(str(caminho), headless=False, **opcoes)
    except TypeError:
        for chave in ('screen', 'device_scale_factor', 'color_scheme', 'ignore_default_args'):
            opcoes.pop(chave, None)
        contexto = p.chromium.launch_persistent_context(str(caminho), headless=False, **opcoes)
    contexto.add_init_script(SCRIPT_FINGERPRINT.replace('__CFG__', _configuracao_js(ident)))
    return contexto, ident


VEXION_ETAPAS = {
    'professional': 'Conta profissional',
    'app': 'Convite em Apps e sites',
    'bio': 'Biografia',
    'photo': 'Foto de perfil',
    'story': 'Story com link',
    'highlight': 'Destaque',
    'vexion': 'Conectar à Vexion',
}


def link_vexion_valido(link):
    return re.fullmatch(r'https://(?:[a-z0-9-]+\.)*phempresa\.com\.br/conectar/[A-Za-z0-9_-]+/?', link) is not None


def _configuracao_vexion():
    arquivo = Path(__file__).resolve().parent / 'configuracoes_perfil.json'
    try:
        dados = json.loads(arquivo.read_text(encoding='utf-8')).get('vexionflow', {})
    except (OSError, ValueError, AttributeError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _executar_vexion(page, config, encerrar=None):
    etapas = config.get('etapas', [])
    link = config.get('link', '')
    if 'vexion' in etapas and link and not link_vexion_valido(link):
        raise ValueError('Revise o link de conexão da Vexion no painel.')
    seletor = '#vexionstorys-root'
    page.locator(seletor).wait_for(state='attached', timeout=15000)
    page.evaluate("window.postMessage({type: 'VEXION_CONFIGURE_OPEN_DIRECT'}, location.origin)")
    page.locator(f'{seletor} .vx-configure').wait_for(state='visible', timeout=10000)
    page.locator(seletor).evaluate(
        """(root, config) => {
            for (const etapa of config.opcoes) {
                root.querySelector('.vx-config-' + etapa).checked = config.etapas.includes(etapa);
            }
            const campo = root.querySelector('.vx-config-vexion-link');
            campo.value = config.link;
            campo.dispatchEvent(new Event('input', { bubbles: true }));
        }""", {'opcoes': list(VEXION_ETAPAS), 'etapas': etapas, 'link': link if 'vexion' in etapas else ''})
    page.locator(f'{seletor} .vx-config-run').click(timeout=10000)
    print('VexionFlow: configuração iniciada.')
    fim = time.monotonic() + 600
    import playwright.sync_api
    while time.monotonic() < fim and not page.is_closed():
        if encerrar is not None and encerrar.is_set():
            return
        try:
            estado = page.locator(f'{seletor} .vx-config-status').evaluate(
                "elemento => elemento.dataset.kind", timeout=1000)
        except playwright.sync_api.Error:
            # A extensão recarrega a própria aba durante algumas etapas.
            if page.is_closed():
                break
        else:
            if estado == 'success':
                print('VexionFlow: configuração concluída.')
                return True
            if estado == 'error':
                print('VexionFlow: configuração parou. Confira a mensagem da extensão na janela.')
                return
        page.wait_for_timeout(250)
    if not page.is_closed():
        print('VexionFlow: tempo de espera atingido. Confira a etapa atual na janela.')


def _foto_configurada():
    arquivo = Path(__file__).resolve().parent / 'configuracoes_perfil.json'
    try:
        dados = json.loads(arquivo.read_text(encoding='utf-8')) if arquivo.exists() else {}
        valor = str(dados.get('foto_perfil', '')).strip() if isinstance(dados, dict) else ''
    except (OSError, ValueError):
        valor = ''
    if not valor:
        return None
    caminho = Path(os.path.expandvars(os.path.expanduser(valor)))
    if not caminho.is_absolute():
        caminho = Path(__file__).resolve().parent / caminho
    return caminho.resolve()


def _selecionar_foto_apos_cadastro(page, encerrar=None):
    """Seleciona a foto interceptando o seletor de arquivos, sem abrir a janela do Windows."""
    foto = _foto_configurada()
    if foto is None:
        print('Foto automática não configurada; pulando seleção de foto.')
        return False
    if not foto.is_file():
        print('A foto configurada não foi encontrada:', str(foto))
        return False

    def abrir_conversao_para_revisao():
        try:
            aba = page.context.new_page()
            aba.goto('https://www.instagram.com/accounts/convert_to_professional_account',
                     wait_until='domcontentloaded', timeout=60000)
            print('A página de conversão para conta profissional foi aberta em outra aba.')
            _concluir_conversao_profissional(aba, encerrar=encerrar)
        except Exception as exc:
            print('Não consegui abrir a página de conversão em outra aba:', str(exc)[:250])
            print('Depois de salvar a foto, abra manualmente: https://www.instagram.com/accounts/convert_to_professional_account')

    try:
        page.goto('https://www.instagram.com/accounts/edit/', wait_until='domcontentloaded', timeout=60000)
        try:
            import extension_bridge
            resultado = extension_bridge.mark_editor()
            if resultado and resultado.get('marked'):
                print('Página de edição detectada: pedido marcado como Criada no painel.')
        except Exception as exc:
            print('Não foi possível registrar a detecção no painel:', str(exc)[:180])
        limite = time.monotonic() + 60
        while time.monotonic() < limite and not page.is_closed() and not (encerrar and encerrar.is_set()):
            entrada = page.locator('input[type="file"]')
            if entrada.count():
                entrada.first.set_input_files(str(foto), timeout=10000)
                print('Foto selecionada. Revise o recorte e clique em Salvar no próprio Instagram.')
                abrir_conversao_para_revisao()
                return True
            botao_foto = page.get_by_role('button', name=re.compile(r'mudar foto|change photo|edit picture', re.I))
            try:
                if botao_foto.count() and botao_foto.first.is_visible():
                    with page.expect_file_chooser(timeout=4000) as escolha:
                        botao_foto.first.click(timeout=3000)
                    escolha.value.set_files(str(foto))
                    print('Foto selecionada. Revise o recorte e clique em Salvar no próprio Instagram.')
                    abrir_conversao_para_revisao()
                    return True
            except Exception:
                pass
            page.wait_for_timeout(800)
        print('Não consegui abrir o seletor de foto automaticamente. O editor está disponível para concluir manualmente.')
    except Exception as exc:
        print('Não foi possível selecionar a foto automaticamente:', str(exc)[:300])
    return False


def _clicar_opcao_exata(page, nome, timeout_ms=12000, encerrar=None):
    limite = time.monotonic() + timeout_ms / 1000
    padrao = re.compile(r'^\s*' + re.escape(nome) + r'\s*$', re.I)
    while time.monotonic() < limite and not page.is_closed() and not (encerrar and encerrar.is_set()):
        try:
            grupo = page.get_by_text(padrao, exact=True)
            for i in range(min(grupo.count(), 20)):
                texto = grupo.nth(i)
                try:
                    if not texto.is_visible():
                        continue
                    alvo = texto.locator('xpath=ancestor-or-self::*[self::label or @role="radio" or @role="button" or @aria-checked or @tabindex="0"][1]')
                    if alvo.count() and alvo.first.is_visible():
                        alvo.first.click(timeout=3000)
                    else:
                        texto.click(timeout=3000)
                    page.wait_for_timeout(500)
                    return True
                except Exception:
                    continue
        except Exception:
            pass
        page.wait_for_timeout(300)
    return False


def _clicar_botao_fluxo(page, nome, timeout_ms=20000, encerrar=None):
    limite = time.monotonic() + timeout_ms / 1000
    padrao = re.compile(r'^\s*' + re.escape(nome) + r'\s*$', re.I)
    while time.monotonic() < limite and not page.is_closed() and not (encerrar and encerrar.is_set()):
        try:
            grupo = page.get_by_role('button', name=padrao)
            for i in range(min(grupo.count(), 20)):
                item = grupo.nth(i)
                try:
                    if item.is_visible() and item.is_enabled():
                        item.click(timeout=4000)
                        page.wait_for_timeout(1000)
                        return True
                except Exception:
                    continue
            grupo = page.get_by_text(padrao, exact=True)
            for i in range(min(grupo.count(), 20)):
                item = grupo.nth(i)
                try:
                    if item.is_visible() and item.is_enabled():
                        item.click(timeout=4000)
                        page.wait_for_timeout(1000)
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        page.wait_for_timeout(300)
    return False


def _concluir_conversao_profissional(page, encerrar=None):
    passos = (
        ('opção Criador de conteúdo', lambda: _clicar_opcao_exata(page, 'Criador de conteúdo', encerrar=encerrar)),
        ('primeiro Avançar', lambda: _clicar_botao_fluxo(page, 'Avançar', encerrar=encerrar)),
        ('segundo Avançar', lambda: _clicar_botao_fluxo(page, 'Avançar', encerrar=encerrar)),
        ('opção Blog pessoal', lambda: _clicar_opcao_exata(page, 'Blog pessoal', encerrar=encerrar)),
        ('primeiro Concluir', lambda: _clicar_botao_fluxo(page, 'Concluir', encerrar=encerrar)),
        ('Continuar', lambda: _clicar_botao_fluxo(page, 'Continuar', encerrar=encerrar)),
        ('Concluir final', lambda: _clicar_botao_fluxo(page, 'Concluir', encerrar=encerrar)),
    )
    for nome, executar in passos:
        if not executar():
            print('Não encontrei ou não consegui acionar ' + nome + '. Continue manualmente na aba de conversão.')
            return False
        print('Etapa concluída: ' + nome + '.')
    if encerrar and encerrar.is_set():
        return False
    if _clicar_botao_fluxo(page, 'Agora não', timeout_ms=5000, encerrar=encerrar):
        print('Convite para conectar ao Facebook dispensado.')
    print('Fluxo de conta profissional concluído. Confira a confirmação exibida pelo Instagram.')
    return True


def _esta_na_pagina_inicial_instagram(page):
    try:
        return bool(re.match(r'^https://www\.instagram\.com/?(?:[?#].*)?$', page.url, re.I))
    except Exception:
        return False


def abrir_sessao(dados, encerrar, proxy_lote=None):
    from playwright.sync_api import Error, sync_playwright
    caminho = pasta_perfil(dados)
    if not caminho.is_dir():
        raise RuntimeError('Não existe perfil salvo para este pedido nesta instalação.')
    with sync_playwright() as p:
        contexto, _ident = _abrir_contexto(p, dados, proxy_lote)
        fechado = threading.Event()
        contexto.on('close', lambda _: fechado.set())
        pagina = None
        try:
            pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
            pagina.goto('https://www.instagram.com/', wait_until='domcontentloaded', timeout=60000)
            print('Perfil aberto. A sessão pode exigir novo login ou verificação.')
            while not encerrar.is_set() and not fechado.is_set() and not pagina.is_closed():
                pagina.wait_for_timeout(250)
        except Error:
            if not fechado.is_set() and (pagina is None or not pagina.is_closed()):
                raise
        finally:
            if not fechado.is_set():
                contexto.close()



def navegador(chave, dados, encerrar=None, aguardar_turno=None, proxy_lote=None, ao_finalizar=None):
    """Executa o cadastro e registra a situação final no painel.
    Não sobrescreve Criada/Suspensa com erro se a conta já foi classificada."""
    try:
        _navegador_executa(chave, dados, encerrar=encerrar, aguardar_turno=aguardar_turno,
                            proxy_lote=proxy_lote, ao_finalizar=ao_finalizar)
    except Exception:
        if _situacao_gravada(dados.get('order_id')) not in ('Criada', 'Suspensa'):
            marcar_situacao(dados.get('order_id'), 'Com erro')
        raise


def _navegador_executa(chave, dados, encerrar=None, aguardar_turno=None, proxy_lote=None, ao_finalizar=None):
    if not idade_acima_de_18(dados.get('nascimento')):
        raise ValueError('Data de nascimento salva inválida ou não corresponde a mais de 18 anos.')
    from playwright.sync_api import Error, sync_playwright
    print('\nAbrindo navegador visível. Não feche até terminar.')
    print('Confira os dados e resolva manualmente desafios ou campos não encontrados.')
    with sync_playwright() as p:
        browser, ident = _abrir_contexto(p, dados, proxy_lote)
        page = browser.pages[0] if browser.pages else browser.new_page()
        print('Perfil de navegador separado salvo localmente para este pedido:', pasta_perfil(dados))
        print('Fingerprint em uso:', ident['id'], '|', ident['preset'])
        if aguardar_turno and not aguardar_turno(page):
            if not page.is_closed():
                browser.close()
            return
        dados['tentativa_em'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
        salvar(dados)
        page.goto(CADASTRO, wait_until='domcontentloaded', timeout=60000)
        inicio = time.monotonic(); enviou_form = False; enviou_data = False; tentou_codigo = False
        tentativa_envio = False; hora_preenchimento = None
        finalizado = False; inicio_codigo = None
        configuracao_ok = True
        config = _configuracao_vexion()
        relato = set(); ultima_etapa_email = 0
        campos = {
            'e-mail': (['input[name="emailOrPhone"]', 'input[name="email"]', 'input[type="email"]', 'input[autocomplete="email"]', 'input[placeholder*="celular ou email" i]', 'input[placeholder*="celular ou e-mail" i]', 'input[aria-label*="celular ou email" i]', 'input[placeholder*="email" i]', 'input[placeholder*="e-mail" i]'], dados['email']),
            'nome': (['input[name="fullName"]', 'input[name="full_name"]', 'input[autocomplete="name"]', 'input[placeholder*="nome completo" i]', 'input[aria-label*="nome completo" i]'], dados['nome']),
            'usuário': (['input[name="username"]', 'input[autocomplete="username"]', 'input[placeholder*="nome de usuário" i]', 'input[aria-label*="nome de usuário" i]', 'input[placeholder*="username" i]'], dados['usuario']),
            'senha': (['input[name="password"]', 'input[type="password"]', 'input[placeholder="Senha" i]', 'input[aria-label="Senha" i]'], dados['senha'])}
        feitos = set()
        while time.monotonic() - inicio < 600:
            if page.is_closed() or (encerrar and encerrar.is_set()):
                break
            time.sleep(1)
            if not enviou_form:
                for nome, (seletores, valor) in campos.items():
                    if nome not in feitos and preencher(page, seletores, valor):
                        feitos.add(nome)
                        print('Preenchido:', nome)
                data_ok = nascimento(page, dados['nascimento'])
                if data_ok and 'data' not in relato:
                    print('Data de nascimento preenchida.'); relato.add('data')
                if len(feitos) == 4 and data_ok:
                    if hora_preenchimento is None: hora_preenchimento = time.monotonic()
                    estado = verificar_usuario(page, dados['usuario'])
                    if estado in ('indisponivel', 'alterado'):
                        if estado not in relato:
                            print('Nome de usuário indisponível ou alterado. Corrija manualmente e envie o formulário.'); relato.add(estado)
                    elif estado == 'nao_encontrado':
                        if estado not in relato: print('Não consegui verificar o campo do usuário; confira e envie manualmente.'); relato.add(estado)
                    elif not tentativa_envio and time.monotonic() - hora_preenchimento >= 3:
                        print('Usuário disponível segundo a página.' if estado == 'disponivel' else 'Disponibilidade não confirmada; verifique a mensagem na página.')
                        if botao(page, [r'^(enviar|avançar|próximo|continuar|cadastrar-se|cadastre-se|sign up|submit|send|next|continue)$']):
                            tentativa_envio = True; enviou_form = True; enviou_data = True
                            print('Botão Enviar acionado uma vez.')
                elif len(feitos) == 4:
                    visiveis = 0
                    try: visiveis = max(page.locator('select:visible, [role="combobox"]:visible').count(), len(_controles_data(page)))
                    except Exception: pass
                    if visiveis > 0:
                        if 'pendente_data' not in relato:
                            print('Não consegui completar a data: selecione dia, mês e ano manualmente e avance.'); relato.add('pendente_data')
                    elif botao(page, [r'^(enviar|avançar|próximo|continuar|cadastrar-se|cadastre-se|sign up|submit|send|next|continue)$']): enviou_form = True
                elif 'e-mail' in feitos and not ({'nome','usuário','senha'} & feitos) and time.monotonic() - ultima_etapa_email > 8:
                    if botao(page, [r'^(avançar|próximo|next)$']): ultima_etapa_email = time.monotonic()
            if enviou_form and not enviou_data and nascimento(page, dados['nascimento']):
                print('Data de nascimento preenchida. Tentando avançar...')
                if botao(page, [r'^(avançar|próximo|next)$']): enviou_data = True
            campo = campo_codigo(page)
            if campo is not None and not tentou_codigo:
                tentou_codigo = True
                inicio_codigo = time.monotonic()
                print('Tela de código identificada. Consultando o pedido existente, sem nova compra.')
                try:
                    codigo = None if dados.get('pedido_origem') else dados.get('codigo')
                    if not codigo:
                        codigo = esperar_codigo(chave, dados, encerrar=encerrar)
                    if codigo:
                        inicio_codigo = time.monotonic()
                        campo.fill(str(codigo), timeout=3000)
                        if campo.input_value() != str(codigo):
                            print('O campo não confirmou o código; confira e complete manualmente.')
                        else:
                            print('Código preenchido. Tentando confirmar uma vez...')
                            if not botao(page, [r'^(enviar|avançar|próximo|continuar|confirmar|submit|send|next|continue|confirm)$']):
                                print('Não encontrei o botão de confirmação; conclua manualmente.')
                    else:
                        print('Código não disponível. Confira o pedido; não compre outro automaticamente.')
                except Exception as exc:
                    print('Não foi possível consultar ou preencher o código:', str(exc).replace(chave, '[chave ocultada]'))
            if tentou_codigo and not finalizado:
                situacao = _situacao_por_pagina(page)
                if situacao is None and inicio_codigo and time.monotonic() - inicio_codigo > 90:
                    situacao = 'Com erro'
                if situacao:
                    finalizado = True
                    if situacao == 'Criada' and not dados.get('criada_em'):
                        dados['criada_em'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
                        salvar(dados)
                    marcar_situacao(dados['order_id'], situacao)
                    if situacao == 'Criada':
                        usuario = str(dados['usuario']).strip().lstrip('@')
                        sys.stdout.write(f'Conta criada | Usuário: @{usuario}\n')
                        if config.get('automatica'):
                            try:
                                configuracao_ok = bool(_executar_vexion(page, config, encerrar=encerrar))
                            except Exception as exc:
                                configuracao_ok = False
                                print('VexionFlow: não foi possível iniciar ou acompanhar a configuração.', type(exc).__name__)
                        else:
                            _selecionar_foto_apos_cadastro(page, encerrar=encerrar)
                    else:
                        print('Resultado final da conta:', situacao)
                    break
        if not finalizado and _situacao_gravada(dados.get('order_id')) not in ('Criada', 'Suspensa'):
            marcar_situacao(dados.get('order_id'), 'Com erro')
        criada = _situacao_gravada(dados.get('order_id')) == 'Criada'
        if ao_finalizar is not None:
            ao_finalizar(criada and bool(dados.get('codigo')))
        if not page.is_closed() and criada and configuracao_ok and config.get('fechar_ao_concluir'):
            browser.close()
            return
        if encerrar is None:
            if not page.is_closed():
                print('Finalize o que faltar no navegador. Pressione Enter neste terminal para encerrá-lo.')
                input()
        else:
            print('Janela aberta. Feche esta janela ou use o botão de encerrar no painel.')
            try:
                while not page.is_closed() and not encerrar.is_set():
                    page.wait_for_timeout(250)
            except Error:
                if not page.is_closed():
                    raise
        if not page.is_closed():
            browser.close()


def dados_do_pedido(ordem):
    id_local = f"toless-{ordem['id']}"
    salvo = _ler_pedido(id_local)
    if salvo:
        return salvo
    dados = perfil()
    dados.update({'order_id': id_local, 'toless_id': ordem['id'], 'email': ordem['email'],
                  'produto': ordem['product_id'], 'fingerprint_variante': 0,
                  'max_codes': ordem['max_codes'],
                  'usos': 1, 'contas': []})
    salvar(dados)
    print('\nPedido Toless:', ordem['id'], '| E-mail:', dados['email'])
    return dados


def comprar_dados(chave, produto, idempotencia=None):
    ordem = Toless(chave).comprar(produto, idempotencia or str(uuid.uuid4()))
    return dados_do_pedido(ordem)


def _preparar_reuso():
    dados = perfil()
    dados['order_id'] = f'reuso-{uuid.uuid4().hex}'
    dados['fingerprint_variante'] = 0
    return dados


def criar_pedido_reuso(chave, pedido_origem, dados=None):
    """Cria um pedido novo (nome, usuário, senha e fingerprint próprios)
    reaproveitando o e-mail do pedido de compra original, sem nova compra.
    O limite vem do pedido Toless."""
    raiz_id = _pedido_raiz(pedido_origem)
    origem = _ler_pedido(raiz_id)
    if not origem:
        raise RuntimeError('Pedido de origem não encontrado em dados_locais.')
    if not origem.get('email'):
        raise RuntimeError('O pedido de origem não tem e-mail registrado.')
    pedido_api = origem.get('toless_id')
    if not pedido_api:
        raise RuntimeError('O reuso requer um pedido Toless OTP.')
    ordem = Toless(chave).request(f'/orders/{pedido_api}')['order']
    if ordem['status'] != 'active':
        raise RuntimeError('O pedido Toless precisa estar ativo para receber outro código.')
    usados = ordem['codes_received']
    anteriores = _codigos_toless(ordem)
    usos = int(origem.get('usos') or 1)
    limite = ordem['max_codes']
    if usados < usos:
        raise RuntimeError('Aguarde o código do cadastro anterior antes de reusar este e-mail.')
    if usos >= limite:
        raise RuntimeError(f'O e-mail já foi usado {usos} vezes (limite {limite}). Compre um e-mail novo.')
    if not anteriores or len(ordem.get('codes', [])) < usados:
        raise RuntimeError('O Toless não retornou o histórico completo de códigos deste e-mail. Tente novamente.')
    if dados is None:
        dados = _preparar_reuso()
    dados.update({
                  'email': origem['email'], 'produto': origem.get('produto', ''),
                  'fingerprint_variante': 0,
                  'pedido_origem': str(origem['order_id']), 'toless_id': pedido_api,
                  'max_codes': limite,
                  'toless_codigos_excluidos': sorted(anteriores),
                  'usos': usos + 1, 'contas': []})
    origem['usos'] = usos + 1
    origem['max_codes'] = limite
    origem.setdefault('contas', []).append(dados['usuario'])
    salvar(dados)
    salvar(origem)
    print(f"Reuso: e-mail {origem['email']} agora com uso {usos + 1} de {limite}.")
    print('Pedido novo:', dados['order_id'], '| Usuário:', dados['usuario'])
    return dados


def proxies_para_janelas(quantidade):
    try:
        from proxy_manager import get_browser_proxies
    except ModuleNotFoundError as exc:
        if exc.name != 'proxy_manager':
            raise
        return []
    else:
        return get_browser_proxies(quantidade)


def lancar_lote(quantidade, produto, chave=None, *, confirmado=None, encerrar=None):
    chave = chave or os.environ.get('TOLESS_TOKEN', '').strip()
    if not chave:
        raise RuntimeError('Informe a chave Toless para iniciar o lote.')
    import playwright.sync_api

    catalogo = Toless(chave).request('/products')['products']
    item = next((p for p in catalogo if p['id'] == produto and p['in_stock']), None)
    if item is None:
        raise RuntimeError('Produto fora de estoque. Atualize o catálogo.')
    capacidade = item['max_codes']
    if not isinstance(capacidade, int) or capacidade < 1:
        raise RuntimeError('O Toless não informou uma quantidade válida de códigos.')
    if confirmado and (item['price_cents'], capacidade) != confirmado:
        raise RuntimeError('O preço ou a capacidade do produto mudou. Atualize o catálogo e confirme novamente.')

    proxies = proxies_para_janelas(quantidade)
    if proxies:
        print(f'{len(proxies)} proxies distintas reservadas para as janelas do lote.')

    encerrar = encerrar if encerrar is not None else threading.Event()
    grupos = []
    for inicio in range(0, quantidade, capacidade):
        if encerrar.is_set():
            return
        raiz = comprar_dados(chave, produto)
        tamanho = min(capacidade, quantidade - inicio)
        if raiz['max_codes'] < tamanho:
            raise RuntimeError('O pedido retornou menos códigos que o catálogo. Lote interrompido.')
        grupos.append([raiz] + [_preparar_reuso() for _ in range(tamanho - 1)])

    def executar(dados, raiz, anterior, terminou, indice):
        sucesso = False

        def aguardar_turno(page):
            if anterior is not None:
                print(f'Janela {indice}/{quantidade} aberta. Aguardando a vez deste Gmail.')
                while not anterior['evento'].is_set():
                    if encerrar.is_set() or page.is_closed():
                        return False
                    page.wait_for_timeout(250)
                if not anterior['sucesso']:
                    return False
                if encerrar.is_set() or page.is_closed():
                    return False
                criar_pedido_reuso(chave, raiz['order_id'], dados)
            if encerrar.is_set() or page.is_closed():
                return False
            print(f'Cadastro {indice}/{quantidade} iniciado.')
            return True

        def liberar_proximo(resultado):
            nonlocal sucesso
            sucesso = resultado
            terminou['sucesso'] = resultado
            terminou['evento'].set()

        try:
            proxy = proxies[indice - 1] if proxies else None
            navegador(chave, dados, encerrar=encerrar, aguardar_turno=aguardar_turno,
                      proxy_lote=proxy, ao_finalizar=liberar_proximo)
            return sucesso
        except Exception as exc:
            print(f'Cadastro {indice}/{quantidade}: {exc}')
            return sucesso
        finally:
            if not terminou['evento'].is_set():
                liberar_proximo(False)

    tarefas = []
    with ThreadPoolExecutor(max_workers=quantidade) as executor:
        for grupo in grupos:
            anterior = None
            for dados in grupo:
                terminou = {'evento': threading.Event(), 'sucesso': False}
                tarefas.append(executor.submit(executar, dados, grupo[0], anterior, terminou, len(tarefas) + 1))
                anterior = terminou
        feitos = sum(tarefa.result() for tarefa in as_completed(tarefas))
    print(f'Lote finalizado: {feitos}/{quantidade} cadastro(s).')


def lancar_reuso(quantidade, pedido_origem):
    """Abre N janelas, cada uma criando uma conta nova com o e-mail do pedido."""
    chave = os.environ.get('TOLESS_TOKEN', '').strip()
    if not chave:
        raise RuntimeError('A chave da API precisa estar no ambiente para o reuso.')
    meu = Path(__file__).resolve()
    abertos = []
    for i in range(quantidade):
        ambiente = os.environ.copy()
        ambiente['TOLESS_TOKEN'] = chave
        ambiente['PYTHONUNBUFFERED'] = '1'
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
        proc = subprocess.Popen(
            [sys.executable, str(meu), '--reusar', str(pedido_origem)],
            cwd=str(meu.parent), env=ambiente, creationflags=flags)
        abertos.append(proc.pid)
        print(f'Janela de reuso {i + 1} de {quantidade} lançada (PID {proc.pid}).')
        time.sleep(3)
    print('\nJanelas de reuso lançadas:', len(abertos))


def main():
    parser = argparse.ArgumentParser(description='Assistente local para cadastro, com compra de e-mail mediante confirmação.')
    parser.add_argument('--retomar', metavar='ID', help='retoma um pedido salvo sem fazer nova compra')
    parser.add_argument('--novo-fingerprint', action='store_true', help='sorteia outro MacBook e usa um perfil isolado novo')
    parser.add_argument('--lote', type=int, metavar='N', help='executa N cadastros aproveitando os códigos de cada e-mail')
    parser.add_argument('--lote-um', action='store_true', help='uso interno do lote: uma compra e um cadastro sem perguntas')
    parser.add_argument('--produto', dest='produto_lote', help='ID do produto usado no lote')
    parser.add_argument('--reusar', metavar='ID', help='cria uma conta nova reaproveitando o e-mail do pedido ID')
    args = parser.parse_args()

    if args.lote:
        slug = args.produto_lote
        if not slug:
            parser.error('Informe --produto com o ID retornado pelo catálogo Toless.')
        lancar_lote(max(1, args.lote), slug)
        return

    if args.lote_um:
        slug = args.produto_lote
        if not slug:
            parser.error('Informe --produto com o ID retornado pelo catálogo Toless.')
        chave = os.environ.get('TOLESS_TOKEN', '').strip()
        if not chave:
            raise RuntimeError('Chave da API não informada no ambiente.')
        import playwright.sync_api
        print('Lote: comprando um e-mail do produto', slug)
        dados = comprar_dados(chave, slug)
        navegador(chave, dados)
        return

    chave = os.environ.get('TOLESS_TOKEN') or getpass.getpass('Chave da API Toless OTP (não aparece ao digitar): ').strip()
    if not chave: raise RuntimeError('Chave de API não informada.')
    try: import playwright.sync_api
    except ImportError: raise RuntimeError('Playwright não instalado. Siga as instruções do LEIA-ME antes de comprar.')
    if args.reusar:
        dados = criar_pedido_reuso(chave, args.reusar)
    elif args.retomar:
        caminho = PASTA / (re.sub(r'[^a-zA-Z0-9_-]', '_', args.retomar) + '.json')
        if not caminho.exists(): raise RuntimeError('Pedido não encontrado em dados_locais. Verifique o ID e execute na mesma pasta.')
        dados = json.loads(caminho.read_text(encoding='utf-8'))
    else:
        produtos = Toless(chave).request('/products')
        print('\nProdutos e preços retornados pela API:')
        print(json.dumps(produtos, ensure_ascii=False, indent=2))
        slug = input('\nDigite exatamente o ID do produto desejado: ').strip()
        if not slug: raise RuntimeError('Produto não informado; nenhuma compra foi feita.')
        print('A compra poderá ser cobrada. Confira o preço do produto acima.')
        if input('Digite COMPRAR para confirmar uma única compra: ').strip() != 'COMPRAR':
            print('Cancelado. Nenhuma compra foi feita.'); return
        try:
            dados = comprar_dados(chave, slug)
        except RuntimeError:
            print('ATENÇÃO: se houve erro de conexão, o resultado da compra pode ser desconhecido. Confira o histórico antes de tentar novamente.')
            raise
    if args.novo_fingerprint:
        trocar_fingerprint(dados)
    else:
        ident = obter_identidade(dados)
        print('\nFingerprint deste pedido:', ident['id'], '|', ident['preset'], '|', ident['chip'], '|', ident['hardware_concurrency'], 'núcleos')
    navegador(chave, dados)

if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: print('\nInterrompido. Confira o histórico antes de uma nova compra.')
    except Exception as e: print('\nErro:', e, file=sys.stderr); sys.exit(1)
