"""Cadastro, teste e seleção local de proxies para os navegadores."""
import base64, csv, datetime as dt, json, queue, socket, sqlite3, ssl, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import tema

DB=Path(__file__).resolve().parent/'proxies.sqlite3'
TYPES=('HTTP','HTTPS','SOCKS5')
ENDPOINT_HOST='api.ipify.org'

def db():
 c=sqlite3.connect(DB,timeout=20); c.row_factory=sqlite3.Row
 c.execute("CREATE TABLE IF NOT EXISTS proxies(id INTEGER PRIMARY KEY,host TEXT,port INTEGER,username TEXT DEFAULT '',password TEXT DEFAULT '',kind TEXT,status TEXT DEFAULT 'nova',exit_ip TEXT DEFAULT '',latency INTEGER,checked TEXT DEFAULT '',uses INTEGER DEFAULT 0,UNIQUE(host,port,username,password,kind))")
 cols={r['name'] for r in c.execute('PRAGMA table_info(proxies)')}
 for name,definition in [('country',"TEXT DEFAULT ''"),('city',"TEXT DEFAULT ''"),('last_error',"TEXT DEFAULT ''")]:
  if name not in cols:c.execute(f'ALTER TABLE proxies ADD COLUMN {name} {definition}')
 c.execute("CREATE TABLE IF NOT EXISTS proxy_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,event TEXT NOT NULL,session_id TEXT DEFAULT '',proxy TEXT DEFAULT '',function_name TEXT DEFAULT '',exit_ip TEXT DEFAULT '',result TEXT DEFAULT '',message TEXT DEFAULT '',duration_ms INTEGER,details TEXT DEFAULT '')")
 c.commit(); return c

def log_event(event,proxy='',session_id='',function_name='',exit_ip='',result='',message='',duration_ms=None,details=None):
 now=dt.datetime.now().astimezone().isoformat(timespec='seconds')
 row=(now,event,session_id,proxy,function_name,exit_ip,result,message,duration_ms,json.dumps(details or {},ensure_ascii=False))
 with db() as c:c.execute('INSERT INTO proxy_logs(ts,event,session_id,proxy,function_name,exit_ip,result,message,duration_ms,details) VALUES(?,?,?,?,?,?,?,?,?,?)',row)
 print(f'[{now}] {event} proxy={proxy or "-"} resultado={result or "-"} {message}'.rstrip())

def parse(line,kind='HTTP'):
 s=line.strip()
 if not s or s.startswith('#'):return None
 user=pw=''; proto=None
 if '://' in s:proto,s=s.split('://',1); proto=proto.lower()
 if '@' in s:
  auth,s=s.rsplit('@',1)
  if ':' not in auth:raise ValueError('Credenciais devem estar no formato usuario:senha')
  user,pw=auth.split(':',1); user=urllib.parse.unquote(user);pw=urllib.parse.unquote(pw)
 parts=s.rsplit(':',3)
 if len(parts)==4 and not user:host,port,user,pw=parts
 elif len(parts)==2:host,port=parts
 else:raise ValueError('Formato inválido')
 try:port=int(port)
 except ValueError as e:raise ValueError('Porta inválida') from e
 kinds={'http':'HTTP','https':'HTTPS','socks5':'SOCKS5','socks5h':'SOCKS5'}
 kind=kinds.get(proto,kind.upper())
 if not host or not 1<=port<=65535 or kind not in TYPES:raise ValueError('Host, porta ou tipo inválido')
 return host,port,user,pw,kind

def import_text(text,kind='HTTP'):
 counts=[0,0,0]; errors=[]
 with db() as c:
  for n,line in enumerate(text.splitlines(),1):
   if not line.strip() or line.lstrip().startswith('#'):continue
   try:
    r=parse(line,kind)
    if r and c.execute('INSERT OR IGNORE INTO proxies(host,port,username,password,kind) VALUES(?,?,?,?,?)',r).rowcount:counts[0]+=1
    else:counts[1]+=1
   except Exception as e:counts[2]+=1;errors.append(f'{n}: {e}')
 if counts[0]:log_event('PROXY_IMPORTADA',result='sucesso',message=f'{counts[0]} proxy(s) cadastrada(s)',details={'quantidade':counts[0]})
 return counts,errors

def getall():
 with db() as c:return [dict(r) for r in c.execute('SELECT * FROM proxies ORDER BY id')]
def _proxy_label(r):return f"{r['host']}:{r['port']} ({r['kind']})"

def _recv_exact(sock,n):
 data=b''
 while len(data)<n:
  part=sock.recv(n-len(data))
  if not part:break
  data+=part
 return data

def _recv_until(sock,marker=b'\r\n\r\n',limit=65536):
 data=b''
 while marker not in data and len(data)<limit:
  chunk=sock.recv(4096)
  if not chunk:break
  data+=chunk
 return data

def _tunnel(r,target_host,target_port,timeout):
 kind=r['kind']; raw=socket.create_connection((r['host'],int(r['port'])),timeout=timeout);raw.settimeout(timeout)
 if kind=='HTTPS':raw=ssl.create_default_context().wrap_socket(raw,server_hostname=r['host'])
 if kind=='SOCKS5':
  methods=b'\x00\x02' if r['username'] else b'\x00'
  raw.sendall(b'\x05'+bytes([len(methods)])+methods); reply=_recv_exact(raw,2)
  if len(reply)!=2 or reply[0]!=5:raise ConnectionError('Resposta SOCKS5 inválida')
  if reply[1]==2:
   u=r['username'].encode();p=r['password'].encode()
   if len(u)>255 or len(p)>255:raise ValueError('Credenciais SOCKS5 excedem 255 bytes')
   raw.sendall(b'\x01'+bytes([len(u)])+u+bytes([len(p)])+p); auth=_recv_exact(raw,2)
   if len(auth)!=2 or auth[1]!=0:raise PermissionError('Autenticação SOCKS5 recusada')
  elif reply[1]!=0:raise PermissionError('Proxy SOCKS5 exige método de autenticação não suportado')
  host=target_host.encode('idna')
  if len(host)>255:raise ValueError('Nome do destino muito longo')
  raw.sendall(b'\x05\x01\x00\x03'+bytes([len(host)])+host+int(target_port).to_bytes(2,'big'))
  head=_recv_exact(raw,4)
  if len(head)<4 or head[1]!=0:raise ConnectionError('Conexão SOCKS5 recusada (código '+(str(head[1]) if len(head)>1 else 'sem resposta')+')')
  atyp=head[3]; size={1:4,4:16}.get(atyp)
  if atyp==3:
   n=_recv_exact(raw,1)
   if not n:raise ConnectionError('Resposta SOCKS5 incompleta')
   size=n[0]
  elif size is None:raise ConnectionError('Resposta SOCKS5 inválida')
  remaining=size+2
  while remaining:
   b=_recv_exact(raw,remaining)
   if not b:raise ConnectionError('Resposta SOCKS5 incompleta')
   remaining-=len(b)
 else:
  auth=''
  if r['username']:
   token=base64.b64encode((r['username']+':'+r['password']).encode()).decode();auth='Proxy-Authorization: Basic '+token+'\r\n'
  req=f'CONNECT {target_host}:{target_port} HTTP/1.1\r\nHost: {target_host}:{target_port}\r\n{auth}Proxy-Connection: Keep-Alive\r\n\r\n'.encode('ascii')
  raw.sendall(req); response=_recv_until(raw); first=response.split(b'\r\n',1)[0]
  try:status=int(first.split()[1])
  except Exception:raise ConnectionError('Resposta HTTP inválida do proxy')
  if status==407:raise PermissionError('Autenticação do proxy recusada (HTTP 407)')
  if status<200 or status>=300:raise ConnectionError(f'Túnel recusado pelo proxy (HTTP {status})')
 try:return ssl.create_default_context().wrap_socket(raw,server_hostname=target_host)
 except Exception:raw.close();raise

def _request_ip_through_proxy(r,timeout):
 s=_tunnel(r,ENDPOINT_HOST,443,timeout)
 try:
  s.sendall(b'GET /?format=json HTTP/1.1\r\nHost: api.ipify.org\r\nAccept: application/json\r\nConnection: close\r\n\r\n')
  response=_recv_until(s,b'\r\n\r\n')
  if b'\r\n\r\n' not in response:raise ConnectionError('Resposta incompleta do endpoint de IP')
  head,body=response.split(b'\r\n\r\n',1)
  while True:
   chunk=s.recv(4096)
   if not chunk:break
   body+=chunk
  if not head.split(b'\r\n',1)[0].split()[1].startswith(b'2'):raise ConnectionError('Endpoint de IP retornou erro HTTP')
  if b'transfer-encoding: chunked' in head.lower():
   # ipify retorna corpo pequeno; remove enquadramento chunked se presente.
   pieces=[]; rest=body
   while b'\r\n' in rest:
    n,rest=rest.split(b'\r\n',1)
    try:size=int(n.split(b';')[0],16)
    except ValueError:break
    if size==0:break
    pieces.append(rest[:size]);rest=rest[size+2:]
   body=b''.join(pieces)
  value=json.loads(body.decode('utf-8'))
  if not value.get('ip'):raise ValueError('Endpoint não retornou IP')
  return value['ip']
 finally:s.close()

def _geo(ip,timeout):
 try:
  # Envia somente o IP de saída ao serviço de geolocalização; falha geográfica não invalida o proxy.
  url='http://ip-api.com/json/'+urllib.parse.quote(ip,safe='')+'?fields=status,country,city'
  with urllib.request.urlopen(url,timeout=min(4,timeout)) as resp:d=json.loads(resp.read(8192))
  if d.get('status')=='success':return d.get('country',''),d.get('city','')
 except Exception:pass
 return '',''

def test(pid,timeout=10):
 with db() as c:r=c.execute('SELECT * FROM proxies WHERE id=?',(pid,)).fetchone()
 if not r:raise ValueError('Proxy não encontrada')
 r=dict(r); label=_proxy_label(r); start=time.monotonic(); log_event('TESTE_INICIADO',proxy=label,function_name='teste_manual')
 ip='';country=city='';status='falhou';err=''
 try:
  ip=_request_ip_through_proxy(r,timeout);status='ok';country,city=_geo(ip,timeout)
 except socket.timeout as e:err='timeout: '+str(e or 'tempo esgotado')
 except PermissionError as e:err='autenticação: '+str(e)
 except ConnectionRefusedError as e:err='conexão recusada: '+str(e)
 except Exception as e:err=type(e).__name__+': '+str(e)[:220]
 ms=int((time.monotonic()-start)*1000)
 with db() as c:c.execute('UPDATE proxies SET status=?,exit_ip=?,latency=?,checked=datetime("now"),country=?,city=?,last_error=? WHERE id=?',(status,ip,ms,country,city,err,pid))
 result={'id':pid,'status':status,'ip':ip,'latency':ms,'country':country,'city':city,'error':err,'proxy':label}
 log_event('TESTE_RESULTADO',proxy=label,function_name='teste_manual',exit_ip=ip,result=status,message=err or f'{ms} ms'+(f'; {country} / {city}' if country else ''),duration_ms=ms,details=result)
 return result

BROWSER_PROXY_CONFIG = Path(__file__).resolve().parent / 'proxy_config.json'

_RODIZIO_LOCK = threading.Lock()
_rodizio = 0


def _ler_proxies(ids):
    marcadores = ','.join('?' for _ in ids)
    with closing(db()) as c:
        rows = {row['id']: row for row in c.execute(f'SELECT * FROM proxies WHERE id IN ({marcadores})', ids)}
    resultados = []
    for pid in ids:
        row = rows.get(pid)
        if row is None or row['status'] != 'ok':
            raise ValueError(f'Proxy ID {pid} indisponível ou ainda sem status ok. Teste-a novamente ou ajuste a seleção.')
        scheme = {'HTTP': 'http', 'HTTPS': 'https', 'SOCKS5': 'socks5'}.get(row['kind'])
        if not scheme:
            raise ValueError(f'Tipo da proxy ID {pid} não suportado pelo navegador.')
        proxy = {'server': f"{scheme}://{row['host']}:{row['port']}"}
        if row['username']:
            proxy.update(username=row['username'], password=row['password'])
        resultados.append(proxy)
    return resultados


def set_browser_proxies(ids):
    global _rodizio
    ids = list(dict.fromkeys(int(pid) for pid in ids))
    if not ids:
        raise ValueError('Marque pelo menos uma proxy.')
    _ler_proxies(ids)
    with _RODIZIO_LOCK:
        tmp = BROWSER_PROXY_CONFIG.with_suffix('.tmp')
        tmp.write_text(json.dumps({'proxy_ids': ids}), encoding='utf-8')
        tmp.replace(BROWSER_PROXY_CONFIG)
        _rodizio = 0


def set_browser_proxy(pid):
    set_browser_proxies([pid])


def clear_browser_proxy():
    global _rodizio
    with _RODIZIO_LOCK:
        BROWSER_PROXY_CONFIG.unlink(missing_ok=True)
        _rodizio = 0


def _proxies_ativas():
    try:
        config = json.loads(BROWSER_PROXY_CONFIG.read_text(encoding='utf-8'))
        ids = config['proxy_ids'] if 'proxy_ids' in config else [config['proxy_id']]
        if not isinstance(ids, list) or not ids:
            raise ValueError
        return _ler_proxies(list(dict.fromkeys(int(pid) for pid in ids)))
    except FileNotFoundError:
        return []
    except (KeyError, TypeError) as exc:
        raise ValueError('Configuração das proxies do navegador inválida.') from exc


def get_browser_proxies(quantidade):
    """Retorna uma proxy distinta por janela ou sinaliza seleção insuficiente."""
    global _rodizio
    with _RODIZIO_LOCK:
        proxies = _proxies_ativas()
        if not proxies:
            return []
        unicas = list({proxy['server']: proxy for proxy in proxies}.values())
        if len(unicas) < quantidade:
            raise ValueError(f'Você ativou {len(unicas)} proxy(s) distinta(s) para {quantidade} janelas. Ative mais proxies ou reduza o lote.')
        escolhidas = [unicas[(_rodizio + i) % len(unicas)] for i in range(quantidade)]
        _rodizio = (_rodizio + quantidade) % len(unicas)
        return escolhidas


def get_browser_proxy():
    proxies = get_browser_proxies(1)
    return proxies[0] if proxies else None


def _manager(parent):
 import tkinter as tk
 from tkinter import ttk,messagebox,filedialog,simpledialog
 w=tk.Toplevel(parent);w.title('Gerenciador de proxies');w.geometry('1120x650');w.minsize(900,500)
 tema.aplicar(w)
 faixa=tk.Canvas(w,height=4,highlightthickness=0,bd=0,bg='#100e16');faixa.pack(fill='x');tema.animar_faixa(faixa,intervalo=85,largura_bloco=130)
 nb=ttk.Notebook(w);nb.pack(fill='both',expand=True,padx=8,pady=8)
 tab=ttk.Frame(nb);logs=ttk.Frame(nb);nb.add(tab,text='Proxies');nb.add(logs,text='Logs de proxy')
 kind=tk.StringVar(value='HTTP');top=ttk.Frame(tab,padding=8);top.pack(fill='x')
 ttk.Label(top,text='Tipo para importação:').pack(side='left');ttk.Combobox(top,textvariable=kind,values=TYPES,state='readonly',width=9).pack(side='left',padx=5)
 ttk.Label(top,text='Concorrência:').pack(side='left',padx=(16,3));conc=tk.StringVar(value='20');ttk.Spinbox(top,from_=1,to=100,textvariable=conc,width=5).pack(side='left')
 ttk.Label(top,text='Timeout (s):').pack(side='left',padx=(10,3));tout=tk.StringVar(value='10');ttk.Spinbox(top,from_=1,to=120,textvariable=tout,width=5).pack(side='left')
 selected_ids=set(); selected_count=tk.StringVar(value='0 selecionadas')
 cols=('id','host','port','kind','status','ip','lat','country','city','error','uses');t=ttk.Treeview(tab,columns=cols,show='tree headings',selectmode='none')
 t.heading('#0',text='☐');t.column('#0',width=34,minwidth=34,stretch=False,anchor='center')
 headers=('ID','Host','Porta','Tipo','Status','IP saída','ms','País','Cidade','Erro','Usos');widths=(42,145,55,65,70,110,55,105,100,240,48)
 for c,l,wd in zip(cols,headers,widths):t.heading(c,text=l);t.column(c,width=wd,anchor='w')
 sy=ttk.Scrollbar(tab,orient='vertical',command=t.yview);sx=ttk.Scrollbar(tab,orient='horizontal',command=t.xview);t.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
 t.pack(fill='both',expand=True,padx=8);sx.pack(fill='x',padx=8);sy.place(relx=.985,rely=.11,relheight=.62,anchor='ne')
 msg=tk.StringVar(value='Importação/teste manual; credenciais ficam no banco local.');ttk.Label(tab,textvariable=msg).pack(fill='x',padx=8,pady=4)
 ttk.Label(tab,textvariable=selected_count).pack(anchor='w',padx=10)
 q=queue.Queue();busy={'v':False};progress_state={'done':0,'ok':0,'bad':0,'total':0}
 def refresh():
  rows=getall(); valid={int(r['id']) for r in rows}; selected_ids.intersection_update(valid)
  t.delete(*t.get_children())
  for r in rows:t.insert('','end',iid=str(r['id']),text=('☑' if int(r['id']) in selected_ids else '☐'),values=(r['id'],r['host'],r['port'],r['kind'],r['status'],r['exit_ip'],r['latency'] or '',r['country'],r['city'],r['last_error'],r['uses']))
  selected_count.set(f'{len(selected_ids)} selecionadas')
 def toggle_checkbox(event):
  if t.identify_region(event.x,event.y)!='tree':return
  iid=t.identify_row(event.y)
  if not iid:return
  pid=int(iid)
  if pid in selected_ids:selected_ids.remove(pid)
  else:selected_ids.add(pid)
  t.item(iid,text=('☑' if pid in selected_ids else '☐'))
  selected_count.set(f'{len(selected_ids)} selecionadas')
  return 'break'
 t.bind('<Button-1>',toggle_checkbox)
 def import_content(s):
  counts,errs=import_text(s,kind.get());refresh();msg.set(f'Novos: {counts[0]} | duplicados: {counts[1]} | inválidos: {counts[2]} '+(' '.join(errs[:2])))
 def paste():
  d=tk.Toplevel(w);d.title('Colar proxies');x=tk.Text(d,width=70,height=18);x.pack(fill='both',expand=True)
  ttk.Button(d,text='Importar',command=lambda:(import_content(x.get('1.0','end')),d.destroy())).pack()
 def fromfile():
  f=filedialog.askopenfilename(parent=w,filetypes=[('Texto','*.txt'),('Todos','*.*')])
  if f:
   try:import_content(Path(f).read_text(encoding='utf-8-sig'))
   except Exception as e:messagebox.showerror('Leitura',str(e),parent=w)
 def chosen():return sorted(selected_ids)
 def activate_browser_proxy():
  ids=chosen()
  try:set_browser_proxies(ids)
  except (ValueError,OSError) as e:messagebox.showerror('Proxy do navegador',str(e),parent=w);return
  msg.set(f'{len(ids)} proxy(s) ativada(s). Inicie o lote no painel para usar uma por janela.')
 def deactivate_browser_proxy():
  try:clear_browser_proxy()
  except OSError as e:messagebox.showerror('Proxy do navegador',str(e),parent=w);return
  msg.set('Proxy desativada; os próximos navegadores usarão a conexão direta.')
 def select_all():
  visible={int(i) for i in t.get_children()}
  if visible and visible.issubset(selected_ids):selected_ids.difference_update(visible)
  else:selected_ids.update(visible)
  refresh()
 def testing(all_rows=False):
  if busy['v']:return
  ids=[r['id'] for r in getall()] if all_rows else chosen()
  if not ids:messagebox.showinfo('Teste','Selecione uma ou mais proxies.',parent=w);return
  try:n=max(1,min(100,int(conc.get())));timeout=max(1,int(tout.get()))
  except ValueError:messagebox.showerror('Configuração','Concorrência/timeout inválido.',parent=w);return
  busy['v']=True;progress_state.update(done=0,ok=0,bad=0,total=len(ids));msg.set(f'Testando 0/{len(ids)}…');log_event('TESTE_INICIADO',function_name='teste_lote',result='iniciado',message=f'{len(ids)} proxies')
  def worker(pid):
   try:return test(pid,timeout)
   except Exception as e:
    with db() as c:
     row=c.execute('SELECT host,port,kind FROM proxies WHERE id=?',(pid,)).fetchone()
     c.execute('UPDATE proxies SET status=?,exit_ip=?,latency=?,checked=datetime("now"),last_error=? WHERE id=?',('falhou','',0,str(e)[:220],pid))
    label=f"{row['host']}:{row['port']} ({row['kind']})" if row else str(pid)
    try:log_event('TESTE_RESULTADO',proxy=label,function_name='teste_manual',result='falhou',message=str(e)[:220])
    except Exception:pass
    return {'id':pid,'status':'falhou','ip':'','latency':0,'country':'','city':'','error':str(e),'proxy':label}
  def run():
   with ThreadPoolExecutor(max_workers=min(n,len(ids))) as pool:
    futures=[pool.submit(worker,pid) for pid in ids]
    results=[]
    for future in futures:
     result=future.result();results.append(result);q.put(('result',result))
   q.put(('finished',{'total':len(results),'ok':sum(r['status']=='ok' for r in results),'bad':sum(r['status']!='ok' for r in results)}))
  threading.Thread(target=run,daemon=True).start()
 def edit():
  ids=chosen()
  if len(ids)!=1:return
  v=simpledialog.askstring('Editar tipo','HTTP, HTTPS ou SOCKS5',parent=w)
  if v and v.upper() in TYPES:
   with db() as c:c.execute('UPDATE proxies SET kind=?,status="nova",last_error="" WHERE id=?',(v.upper(),ids[0]))
   refresh()
 def delete():
  ids=chosen()
  if not ids:messagebox.showinfo('Excluir','Marque as proxies que deseja excluir.',parent=w);return
  if not messagebox.askyesno('Excluir',f'Excluir {len(ids)} proxies?',parent=w):return
  with db() as c:
   in_use=[r['id'] for r in c.execute('SELECT id FROM proxies WHERE status="em_uso" AND id IN ('+','.join('?' for _ in ids)+')',ids)] if ids else []
   allowed=[i for i in ids if i not in in_use]
   if allowed:c.executemany('DELETE FROM proxies WHERE id=?',[(i,) for i in allowed])
  if allowed:log_event('PROXIES_EXCLUIDAS',result='sucesso',message=f'{len(allowed)} proxy(s) excluída(s)',details={'quantidade':len(allowed),'ids':allowed})
  selected_ids.difference_update(ids);refresh()
  if in_use:messagebox.showwarning('Proxies em uso','Não foram excluídas as proxies em uso: '+', '.join(map(str,in_use)),parent=w)
  else:msg.set(f'{len(allowed)} proxy(s) excluída(s).')
 b=ttk.Frame(tab,padding=8);b.pack(fill='x')
 for label,fn in [('Colar lista',paste),('Importar TXT',fromfile),('Selecionar todos',select_all),('Testar selecionadas',testing),('Testar todas',lambda:testing(True)),('Usar selecionadas no navegador',activate_browser_proxy),('Desativar proxy do navegador',deactivate_browser_proxy),('Editar tipo',edit),('Excluir selecionadas',delete),('Atualizar',refresh)]:ttk.Button(b,text=label,command=fn).pack(side='left',padx=3)
 # Logs UI: newest first, filters and session timeline.
 filt=ttk.Frame(logs,padding=8);filt.pack(fill='x');eventvar=tk.StringVar(value='Todos');proxyvar=tk.StringVar();sessionvar=tk.StringVar();datevar=tk.StringVar()
 ttk.Label(filt,text='Evento').pack(side='left');eventbox=ttk.Combobox(filt,textvariable=eventvar,values=('Todos','PROXY_IMPORTADA','PROXIES_EXCLUIDAS','TESTE_INICIADO','TESTE_RESULTADO','TESTE_FINALIZADO'),state='readonly',width=20);eventbox.pack(side='left',padx=4)
 for label,var,width in [('Proxy',proxyvar,14),('Sessão',sessionvar,14),('Data AAAA-MM-DD',datevar,12)]:ttk.Label(filt,text=label).pack(side='left',padx=(8,2));ttk.Entry(filt,textvariable=var,width=width).pack(side='left')
 lc=('ts','event','session_id','proxy','function_name','exit_ip','result','duration_ms','message');lt=ttk.Treeview(logs,columns=lc,show='headings',selectmode='browse')
 for c,l,wd in zip(lc,('Data/hora','Evento','Sessão','Proxy','Função','IP saída','Resultado','ms','Mensagem'),(145,140,100,170,110,105,70,55,280)):lt.heading(c,text=l);lt.column(c,width=wd,anchor='w')
 lt.tag_configure('failure',background='#ffd9d9',foreground='#8b0000')
 lsy=ttk.Scrollbar(logs,orient='vertical',command=lt.yview);lsx=ttk.Scrollbar(logs,orient='horizontal',command=lt.xview);lt.configure(yscrollcommand=lsy.set,xscrollcommand=lsx.set);lt.pack(fill='both',expand=True,padx=8);lsx.pack(fill='x',padx=8);lsy.pack(side='right',fill='y')
 def refresh_logs():
  lt.delete(*lt.get_children());query='SELECT * FROM proxy_logs WHERE 1=1';params=[]
  if eventvar.get()!='Todos':query+=' AND event=?';params.append(eventvar.get())
  if proxyvar.get().strip():query+=' AND proxy LIKE ?';params.append('%'+proxyvar.get().strip()+'%')
  if sessionvar.get().strip():query+=' AND session_id LIKE ?';params.append('%'+sessionvar.get().strip()+'%')
  if datevar.get().strip():query+=' AND ts LIKE ?';params.append(datevar.get().strip()+'%')
  query+=' ORDER BY id DESC LIMIT 2000'
  with db() as c:rows=c.execute(query,params).fetchall()
  for r in rows:
   failed=r['event'] in ('PROXY_NAO_OBTIDA','TESTE_RAPIDO_FALHOU','ERRO_AO_APLICAR_PROXY','IP_DIVERGENTE','NAVEGADOR_SEM_PROXY') or (r['event']=='TESTE_RESULTADO' and r['result']=='falhou')
   lt.insert('','end',iid=str(r['id']),values=tuple(r[k] for k in lc),tags=('failure',) if failed else ())
 def timeline(event=None):
  sel=lt.selection()
  if not sel:return
  with db() as c:r=c.execute('SELECT session_id FROM proxy_logs WHERE id=?',(sel[0],)).fetchone()
  if r and r['session_id']:
   sessionvar.set(r['session_id']);refresh_logs()
   messagebox.showinfo('Linha do tempo',f'Filtro aplicado à sessão {r["session_id"]}. Eventos de sessão só aparecerão quando houver integração de ciclo de navegador.',parent=w)
 def export():
  f=filedialog.asksaveasfilename(parent=w,defaultextension='.csv',filetypes=[('CSV','*.csv'),('Texto','*.txt')])
  if not f:return
  with db() as c:rows=c.execute('SELECT ts,event,session_id,proxy,function_name,exit_ip,result,message,duration_ms,details FROM proxy_logs ORDER BY id DESC').fetchall()
  try:
   with open(f,'w',newline='',encoding='utf-8-sig') as out:
    wr=csv.writer(out);wr.writerow(('data_hora','evento','sessao','proxy','funcao','ip_saida','resultado','mensagem','duracao_ms','detalhes'))
    wr.writerows([tuple(r) for r in rows])
   messagebox.showinfo('Exportação',f'{len(rows)} eventos exportados.',parent=w)
  except OSError as e:messagebox.showerror('Exportação',str(e),parent=w)
 def clear_old():
  days=simpledialog.askinteger('Limpar logs antigos','Remover eventos com mais de quantos dias?',parent=w,minvalue=1,maxvalue=36500)
  if days is None:return
  cutoff=(dt.datetime.now().astimezone()-dt.timedelta(days=days)).isoformat(timespec='seconds')
  with db() as c:cur=c.execute('DELETE FROM proxy_logs WHERE ts<?',(cutoff,));n=cur.rowcount
  refresh_logs();messagebox.showinfo('Logs',f'{n} evento(s) removido(s).',parent=w)
 ttk.Button(filt,text='Filtrar',command=refresh_logs).pack(side='left',padx=5);ttk.Button(filt,text='Exportar CSV',command=export).pack(side='left',padx=5);ttk.Button(filt,text='Limpar antigos',command=clear_old).pack(side='left',padx=5);lt.bind('<Double-1>',timeline)
 def poll():
  nonlocal_dummy=None
  try:
   while True:
    typ,val=q.get_nowait()
    if typ=='result':
     progress_state['done']+=1
     if val['status']=='ok':progress_state['ok']+=1
     else:progress_state['bad']+=1
     refresh()
     detail=val['ip'] or val['error']
     msg.set(f'Testando {progress_state["done"]}/{progress_state["total"]}: {val["proxy"]} — {val["status"]}; {detail}')
     refresh_logs()
    elif typ=='finished':
     stats=val;ok=stats['ok'];bad=stats['bad']
     log_event('TESTE_FINALIZADO',function_name='teste_lote',result='concluido',message=f'{ok} ok / {bad} falharam',details={'selecionadas':stats['total']})
     busy['v']=False;refresh();refresh_logs();msg.set(f'Teste concluído: {ok} ok / {bad} falharam.')
  except queue.Empty:pass
  if w.winfo_exists():w.after(100,poll)
 for v in (eventvar,):v.trace_add('write',lambda *_:refresh_logs())
 refresh();refresh_logs();w.after(100,poll);return w

def open_manager(parent):return _manager(parent)
