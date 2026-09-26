"""
North Power - Atendente de email com IA (Gemini) + painel visual

Lê a caixa contato@northpowerbr.com (Hostinger) via IMAP, gera uma resposta
com o Gemini (Google AI Studio) e envia via SMTP. Tem um painel no navegador
para acompanhar tudo.

Uso:
  python app.py                 -> robô + painel (abre o navegador)
  python app.py --sem-painel    -> só o robô (para VPS)
  python app.py --checar        -> testa login no email e na IA, não envia nada
  python app.py --testar "Oi, cadê meu pedido?"  -> mostra o que a IA responderia
"""

import base64
import email
import html
import imaplib
import json
import os
import re
import smtplib
import sys
import threading
import time
import uuid
import webbrowser
from collections import deque
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
ARQ_ENV = os.path.join(BASE, ".env")
ARQ_HIST = os.path.join(BASE, "historico.json")
ARQ_BASE = os.path.join(BASE, "base_conhecimento.json")
ARQ_PAINEL = os.path.join(BASE, "painel.html")

FLAG_RESPONDIDO = "$AIRespondido"
FLAG_IGNORADO = "$AIIgnorado"

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

PADROES = {
    "EMAIL_USUARIO": "contato@northpowerbr.com",
    "EMAIL_SENHA": "",
    "IMAP_HOST": "imap.hostinger.com",
    "IMAP_PORTA": "993",
    "SMTP_HOST": "smtp.hostinger.com",
    "SMTP_PORTA": "465",
    "GEMINI_API_KEY": "",
    "GEMINI_MODELO": "gemini-2.5-flash",
    "NOME_REMETENTE": "North Power",
    "INSTAGRAM_USUARIO": "northpowerbr",
    "ENVIO_AUTOMATICO": "nao",
    "INTERVALO_SEGUNDOS": "60",
    "HORAS_RETROATIVAS": "2",
    "MAX_POR_REMETENTE_DIA": "5",
    "MAX_RESPOSTAS_DIA": "300",
    "PAINEL_HOST": "127.0.0.1",
    "PAINEL_PORTA": "8765",
    "PAINEL_SENHA": "",
}
SECRETOS = {"EMAIL_SENHA", "GEMINI_API_KEY", "PAINEL_SENHA"}


def ler_env_arquivo():
    valores = {}
    if os.path.exists(ARQ_ENV):
        with open(ARQ_ENV, encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                k, v = linha.split("=", 1)
                valores[k.strip()] = v.strip().strip('"').strip("'")
    return valores


def carregar_config():
    cfg = dict(PADROES)
    for k in PADROES:
        if k in os.environ:
            cfg[k] = os.environ[k]
    for k, v in ler_env_arquivo().items():
        if k in PADROES:
            cfg[k] = v
    return cfg


def salvar_env(cfg):
    with open(ARQ_ENV, "w", encoding="utf-8") as f:
        f.write("# Configuração do robô North Power (gerado pelo painel)\n")
        for k in PADROES:
            f.write(f"{k}={cfg.get(k, '')}\n")


CFG = carregar_config()


def c(k):
    return str(CFG.get(k, PADROES.get(k, ""))).strip()


def ci(k):
    try:
        return int(float(c(k)))
    except ValueError:
        return int(float(PADROES[k]))


def cf(k):
    try:
        return float(c(k))
    except ValueError:
        return float(PADROES[k])


def cb(k):
    return c(k).lower() in ("1", "sim", "s", "true", "yes", "on", "ligado")


def insta():
    return c("INSTAGRAM_USUARIO").lstrip("@") or "northpowerbr"


def config_faltando():
    return [k for k in ("EMAIL_USUARIO", "EMAIL_SENHA", "GEMINI_API_KEY") if not c(k)]


def versao_atual():
    try:
        import subprocess
        r = subprocess.run(["git", "log", "-1", "--format=%h · %cd", "--date=format:%d/%m %H:%M"],
                           cwd=BASE, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "local"
    except Exception:
        return "local"


VERSAO = versao_atual()

LOGS = deque(maxlen=300)


def log(*args):
    agora = datetime.now().strftime("%d/%m %H:%M:%S")
    linha = " ".join(str(a) for a in args)
    LOGS.append(f"[{agora}] {linha}")
    print(f"[{agora}]", linha, flush=True)


def resposta_reserva():
    return (
        "Olá! Obrigado por entrar em contato com a North Power.\n\n"
        "Nosso atendimento é feito principalmente pelo Instagram oficial, onde "
        "nossa equipe consegue responder com mais rapidez:\n"
        f"@{insta()}\n\n"
        "Se a sua mensagem for sobre um pedido, envie o número dele por lá para agilizar.\n\n"
        "Equipe North Power"
    )


# ---------------------------------------------------------------------------
# Leitura e limpeza do email recebido
# ---------------------------------------------------------------------------


def decodificar(valor):
    if not valor:
        return ""
    try:
        return str(make_header(decode_header(valor)))
    except Exception:
        return str(valor)


def html_para_texto(conteudo):
    conteudo = re.sub(r"(?is)<(script|style).*?</\1>", " ", conteudo)
    conteudo = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", conteudo)
    conteudo = re.sub(r"<[^>]+>", " ", conteudo)
    conteudo = html.unescape(conteudo)
    conteudo = re.sub(r"[ \t]+", " ", conteudo)
    return re.sub(r"\n\s*\n+", "\n\n", conteudo).strip()


def extrair_texto(msg):
    texto, texto_html = None, None
    partes = msg.walk() if msg.is_multipart() else [msg]
    for parte in partes:
        if parte.get_content_maintype() == "multipart":
            continue
        if "attachment" in str(parte.get("Content-Disposition", "")).lower():
            continue
        tipo = parte.get_content_type()
        try:
            carga = parte.get_payload(decode=True)
            if carga is None:
                continue
            conteudo = carga.decode(parte.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if tipo == "text/plain" and texto is None:
            texto = conteudo
        elif tipo == "text/html" and texto_html is None:
            texto_html = conteudo
    if texto is None and texto_html is not None:
        texto = html_para_texto(texto_html)
    return remover_citacao(texto or "")


def remover_citacao(texto):
    padroes_corte = [
        r"^\s*Em .{0,200}escreveu:\s*$",
        r"^\s*On .{0,200}wrote:\s*$",
        r"^\s*-{2,}\s*Mensagem original\s*-{2,}",
        r"^\s*-{2,}\s*Original Message\s*-{2,}",
        r"^\s*De:\s.+$",
        r"^\s*From:\s.+$",
        r"^\s*Enviado do meu",
        r"^\s*Sent from my",
    ]
    linhas = []
    for linha in texto.splitlines():
        if any(re.match(p, linha, re.IGNORECASE) for p in padroes_corte):
            break
        if linha.lstrip().startswith(">"):
            continue
        linhas.append(linha)
    limpo = re.sub(r"\n{3,}", "\n\n", "\n".join(linhas).strip())
    return limpo[:4000]


def eh_automatico(msg, remetente):
    r = remetente.lower()
    if not r or "@" not in r:
        return True
    if r == c("EMAIL_USUARIO").lower():
        return True
    if re.search(r"no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounce|notifica|notification|alerts?@", r):
        return True
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if re.search(r"bulk|list|junk|auto_reply", msg.get("Precedence") or "", re.IGNORECASE):
        return True
    for cab in ("List-Unsubscribe", "List-Id", "X-Autoreply", "X-Autorespond", "X-Auto-Response-Suppress"):
        if msg.get(cab):
            return True
    return False


# ---------------------------------------------------------------------------
# IA (Gemini)
# ---------------------------------------------------------------------------


CATEGORIAS = ["Sobre a loja", "Atendimento", "Pedidos e entregas", "Trocas e devoluções",
              "Pagamentos", "Produtos", "Regras para a IA", "Outros"]

BASE_INICIAL = [
    ("Sobre a loja", "Sobre a North Power",
     "A North Power é uma loja online (e-commerce) que vende para todo o Brasil."),
    ("Atendimento", "Canal oficial de atendimento",
     "O atendimento principal é pelo Instagram oficial @northpowerbr. Pelo Direct a equipe responde "
     "dúvidas sobre pedidos, entregas, trocas e produtos com mais rapidez.\n"
     "Este email (contato@northpowerbr.com) é usado para assuntos comerciais e não é o canal principal de atendimento."),
    ("Pedidos e entregas", "Dúvidas sobre um pedido",
     "Após a compra, o cliente recebe um email de aprovação do pedido. Para qualquer dúvida sobre um pedido "
     "específico (status, rastreio), o cliente deve mandar o NÚMERO DO PEDIDO no Direct do Instagram."),
    ("Regras para a IA", "O que nunca fazer",
     "Não informar status, código de rastreio ou prazo de um pedido específico.\n"
     "Não prometer reembolso, troca ou desconto.\n"
     "Sempre direcionar o cliente para o Instagram @northpowerbr."),
]


class BaseConhecimento:
    """Informações da loja que a IA usa, organizadas em itens."""

    def __init__(self):
        self.lock = threading.Lock()
        self.itens = []
        if os.path.exists(ARQ_BASE):
            try:
                with open(ARQ_BASE, encoding="utf-8") as f:
                    self.itens = json.load(f)
                return
            except Exception:
                pass
        agora = time.time()
        self.itens = [
            {"id": uuid.uuid4().hex[:10], "categoria": cat, "titulo": tit, "conteudo": cont,
             "ativo": True, "atualizado": agora}
            for cat, tit, cont in BASE_INICIAL
        ]
        self._salvar()

    def _salvar(self):
        tmp = ARQ_BASE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.itens, f, ensure_ascii=False, indent=1)
        os.replace(tmp, ARQ_BASE)

    def lista(self):
        with self.lock:
            return [dict(i) for i in self.itens]

    def salvar_item(self, d):
        titulo = str(d.get("titulo", "")).strip()
        conteudo = str(d.get("conteudo", "")).strip()
        if not titulo or not conteudo:
            raise ValueError("preencha o título e o conteúdo")
        categoria = d.get("categoria") if d.get("categoria") in CATEGORIAS else "Outros"
        with self.lock:
            item = next((i for i in self.itens if i["id"] == d.get("id")), None)
            if item is None:
                item = {"id": uuid.uuid4().hex[:10]}
                self.itens.append(item)
            item.update(titulo=titulo[:120], conteudo=conteudo[:6000], categoria=categoria,
                        ativo=bool(d.get("ativo", True)), atualizado=time.time())
            self._salvar()
            return dict(item)

    def excluir(self, id_):
        with self.lock:
            self.itens = [i for i in self.itens if i["id"] != id_]
            self._salvar()

    def texto_para_ia(self):
        blocos = []
        with self.lock:
            ativos = [i for i in self.itens if i.get("ativo", True)]
        for cat in CATEGORIAS:
            doCat = [i for i in ativos if i["categoria"] == cat]
            if doCat:
                blocos.append(f"## {cat}\n" + "\n".join(f"- {i['titulo']}: {i['conteudo']}" for i in doCat))
        return "\n\n".join(blocos) or "(nenhuma informação cadastrada)"


BASE_IA = BaseConhecimento()


def ler_conhecimento():
    return BASE_IA.texto_para_ia()


def montar_instrucoes():
    return f"""Você é o atendente de email da loja North Power (e-commerce brasileiro).
Responda sempre em português do Brasil, com tom educado, simpático e direto.

INFORMAÇÕES DA LOJA (use só o que está aqui, nunca invente prazos, preços, códigos ou políticas):
---
{ler_conhecimento()}
---

REGRAS (além das que estiverem na seção "Regras para a IA" acima):
1. Leia a mensagem do cliente e responda de forma personalizada ao que ele pediu.
2. SEMPRE termine direcionando o cliente para o Instagram oficial @{insta()},
   explicando que é lá que a equipe atende mais rápido.
3. Se a pergunta não puder ser resolvida com as informações acima (ex.: status de um
   pedido específico, reclamação, reembolso, problema com produto), não prometa nada:
   diga que a equipe vai ajudar pelo Instagram e marque precisa_humano = true.
4. Se o cliente mencionar um pedido, peça para ele enviar o número do pedido no Instagram.
5. Nunca peça senha, dados de cartão ou documentos.
6. Resposta curta: no máximo 6 parágrafos curtos. Sem markdown, sem asteriscos.
7. Assine como "Equipe North Power".
8. Use acao = "ignorar" SOMENTE se for claramente spam, propaganda de terceiros
   oferecendo serviços, ou mensagem automática. Na dúvida, responda.

Responda APENAS com um JSON neste formato:
{{"acao": "responder" ou "ignorar", "precisa_humano": true ou false, "resposta": "texto do email"}}"""


def perguntar_ia(remetente, assunto, texto):
    if not c("GEMINI_API_KEY"):
        raise RuntimeError("chave do Gemini não configurada")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{c('GEMINI_MODELO')}:generateContent"
    corpo = {
        "system_instruction": {"parts": [{"text": montar_instrucoes()}]},
        "contents": [{
            "role": "user",
            "parts": [{"text": f"Remetente: {remetente}\nAssunto: {assunto}\n\nMensagem do cliente:\n{texto or '(sem texto)'}"}],
        }],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
    }
    resp = requests.post(url, json=corpo, headers={"x-goog-api-key": c("GEMINI_API_KEY")}, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini respondeu {resp.status_code}: {resp.text[:300]}")
    bruto = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    bruto = re.sub(r"^```(?:json)?|```$", "", bruto.strip()).strip()
    r = json.loads(bruto)
    resposta = str(r.get("resposta", "")).strip()
    if resposta and f"@{insta()}".lower() not in resposta.lower():
        resposta += f"\n\nFale com a gente no Instagram: @{insta()}"
    return {
        "acao": str(r.get("acao", "responder")).lower(),
        "precisa_humano": bool(r.get("precisa_humano", False)),
        "resposta": resposta,
    }


# ---------------------------------------------------------------------------
# Envio
# ---------------------------------------------------------------------------


def texto_para_html(texto):
    link = f"https://instagram.com/{insta()}"
    seguro = html.escape(texto)
    seguro = re.sub(
        rf"@{re.escape(insta())}\b",
        f'<a href="{link}" style="color:#1a73e8;font-weight:bold">@{insta()}</a>',
        seguro, flags=re.IGNORECASE,
    )
    paragrafos = [p.replace("\n", "<br>") for p in seguro.split("\n\n")]
    corpo = "".join(f"<p style='margin:0 0 14px'>{p}</p>" for p in paragrafos)
    return f"<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.5;color:#222'>{corpo}</div>"


def montar_resposta(orig, texto_resposta):
    """orig: dict com para, assunto, message_id, references."""
    assunto = orig.get("assunto") or "Sua mensagem"
    if not re.match(r"^\s*(re|res|ref)\s*:", assunto, re.IGNORECASE):
        assunto = f"Re: {assunto}"
    msg = EmailMessage()
    msg["From"] = formataddr((c("NOME_REMETENTE"), c("EMAIL_USUARIO")))
    msg["To"] = orig["para"]
    msg["Subject"] = assunto
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=c("EMAIL_USUARIO").split("@")[-1])
    msg["Auto-Submitted"] = "auto-replied"
    msg["X-Auto-Response-Suppress"] = "All"
    if orig.get("message_id"):
        msg["In-Reply-To"] = orig["message_id"]
        refs = (orig.get("references") or "").split()
        msg["References"] = " ".join(refs + [orig["message_id"]])
    msg.set_content(texto_resposta)
    msg.add_alternative(texto_para_html(texto_resposta), subtype="html")
    return msg


def enviar_smtp(msg):
    with smtplib.SMTP_SSL(c("SMTP_HOST"), ci("SMTP_PORTA"), timeout=60) as smtp:
        smtp.login(c("EMAIL_USUARIO"), c("EMAIL_SENHA"))
        smtp.send_message(msg)


def conectar_imap():
    conn = imaplib.IMAP4_SSL(c("IMAP_HOST"), ci("IMAP_PORTA"))
    conn.login(c("EMAIL_USUARIO"), c("EMAIL_SENHA"))
    conn.select("INBOX")
    return conn


def pasta_enviados(conn):
    try:
        _, pastas = conn.list()
        for linha in pastas or []:
            linha = linha.decode(errors="replace")
            m = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<nome>.+)$', linha)
            if m and "\\Sent" in m.group("flags"):
                return m.group("nome").strip().strip('"')
    except Exception:
        pass
    for nome in ("INBOX.Sent", "Sent"):
        try:
            if conn.select(f'"{nome}"', readonly=True)[0] == "OK":
                return nome
        except Exception:
            continue
    return None


def salvar_em_enviados(conn, msg):
    try:
        pasta = pasta_enviados(conn)
        if pasta:
            conn.append(f'"{pasta}"', "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    except Exception as e:
        log("Aviso: não consegui salvar cópia em Enviados:", e)
    finally:
        conn.select("INBOX")


def marcar(conn, uid, flag, extra=""):
    try:
        conn.uid("STORE", uid, "+FLAGS", f"({flag}{' ' + extra if extra else ''})")
    except Exception as e:
        log(f"Aviso: não consegui marcar o email {uid}:", e)


# ---------------------------------------------------------------------------
# Histórico (o que aparece no painel)
# ---------------------------------------------------------------------------


class Historico:
    def __init__(self):
        self.lock = threading.Lock()
        self.itens = []
        try:
            with open(ARQ_HIST, encoding="utf-8") as f:
                self.itens = json.load(f)
        except Exception:
            self.itens = []

    def _salvar(self):
        self.itens = self.itens[-500:]
        tmp = ARQ_HIST + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.itens, f, ensure_ascii=False, indent=1)
        os.replace(tmp, ARQ_HIST)

    def adicionar(self, item):
        item.setdefault("id", uuid.uuid4().hex[:12])
        item.setdefault("ts", time.time())
        with self.lock:
            self.itens.append(item)
            self._salvar()
        return item

    def atualizar(self, id_, **campos):
        with self.lock:
            for it in self.itens:
                if it["id"] == id_:
                    it.update(campos)
                    self._salvar()
                    return it
        return None

    def por_id(self, id_):
        with self.lock:
            return next((dict(i) for i in self.itens if i["id"] == id_), None)

    def por_mid(self, mid):
        with self.lock:
            for it in reversed(self.itens):
                if it.get("message_id") == mid:
                    return dict(it)
        return None

    def lista(self, limite=200):
        with self.lock:
            return [dict(i) for i in reversed(self.itens[-limite:])]

    def enviados_desde(self, ts, remetente=None):
        with self.lock:
            return sum(
                1 for i in self.itens
                if i["status"] in ("respondido", "enviado_manual") and i.get("ts_envio", i["ts"]) > ts
                and (remetente is None or i.get("remetente") == remetente)
            )


HIST = Historico()


# ---------------------------------------------------------------------------
# Robô
# ---------------------------------------------------------------------------


class Robo:
    def __init__(self):
        self.rodando = True
        self.evento = threading.Event()
        self.lock_ciclo = threading.Lock()
        self.ultima = None
        self.erro = None
        self.falhas = 0

    def ja_tratado(self, mid, automatico):
        # Emails que já apareceram no painel (inclusive no modo teste) não são
        # reenviados sozinhos ao ligar o automático: use o botão "Enviar".
        return HIST.por_mid(mid) is not None

    def pode_enviar(self, remetente):
        limite = time.time() - 86400
        if HIST.enviados_desde(limite) >= ci("MAX_RESPOSTAS_DIA"):
            return False, "limite diário geral atingido"
        if HIST.enviados_desde(limite, remetente) >= ci("MAX_POR_REMETENTE_DIA"):
            return False, "limite diário deste cliente atingido"
        return True, ""

    def processar(self):
        automatico = cb("ENVIO_AUTOMATICO")
        conn = conectar_imap()
        try:
            horas = cf("HORAS_RETROATIVAS")
            desde = (datetime.now() - timedelta(hours=horas)).strftime("%d-%b-%Y")
            status, dados = conn.uid("SEARCH", None, "SINCE", desde,
                                     "UNKEYWORD", FLAG_RESPONDIDO, "UNKEYWORD", FLAG_IGNORADO)
            if status != "OK" or not dados or not dados[0]:
                return
            corte = datetime.now(timezone.utc) - timedelta(hours=horas)

            for uid_b in dados[0].split():
                uid = uid_b.decode()
                status, partes = conn.uid("FETCH", uid, "(INTERNALDATE BODY.PEEK[])")
                if status != "OK" or not partes or not isinstance(partes[0], tuple):
                    continue
                meta, bruto = partes[0]
                dt = imaplib.Internaldate2tuple(meta)
                if dt and datetime.fromtimestamp(time.mktime(dt), timezone.utc) < corte:
                    continue

                msg = email.message_from_bytes(bruto)
                mid = msg.get("Message-ID") or f"uid-{uid}"
                if self.ja_tratado(mid, automatico):
                    continue

                nome, remetente = parseaddr(msg.get("Reply-To") or msg.get("From"))
                remetente = remetente.lower()
                assunto = decodificar(msg.get("Subject"))
                item = {
                    "uid": uid, "message_id": mid, "references": msg.get("References") or "",
                    "remetente": remetente, "nome": decodificar(nome), "assunto": assunto,
                    "modo": "automatico" if automatico else "teste",
                    "precisa_humano": False, "resposta": "", "mensagem": "",
                }

                if eh_automatico(msg, remetente):
                    item.update(status="ignorado", motivo="Email automático / notificação / newsletter")
                    HIST.adicionar(item)
                    if automatico:
                        marcar(conn, uid, FLAG_IGNORADO)
                    log(f"Ignorado (automático): {remetente} | {assunto}")
                    continue

                item["mensagem"] = extrair_texto(msg)

                if automatico:
                    ok, motivo = self.pode_enviar(remetente)
                    if not ok:
                        item.update(status="limite", motivo=motivo, precisa_humano=True)
                        HIST.adicionar(item)
                        marcar(conn, uid, FLAG_IGNORADO, "\\Flagged")
                        log(f"Não respondido ({motivo}): {remetente}")
                        continue

                try:
                    r = perguntar_ia(remetente, assunto, item["mensagem"])
                except Exception as e:
                    log(f"IA falhou ({e}); usando resposta padrão.")
                    r = {"acao": "responder", "precisa_humano": True, "resposta": "",
                         "erro_ia": str(e)[:200]}

                if r["acao"] == "ignorar":
                    item.update(status="ignorado", motivo="A IA classificou como spam/propaganda")
                    HIST.adicionar(item)
                    if automatico:
                        marcar(conn, uid, FLAG_IGNORADO)
                    log(f"Ignorado pela IA: {remetente} | {assunto}")
                    continue

                item["resposta"] = r["resposta"] or resposta_reserva()
                item["precisa_humano"] = r["precisa_humano"]
                if r.get("erro_ia"):
                    item["motivo"] = "IA falhou, usada resposta padrão: " + r["erro_ia"]

                if not automatico:
                    item["status"] = "simulado"
                    HIST.adicionar(item)
                    log(f"[TESTE] Resposta gerada (não enviada): {remetente} | {assunto}")
                    continue

                email_resp = montar_resposta(
                    {"para": remetente, "assunto": assunto, "message_id": msg.get("Message-ID"),
                     "references": item["references"]}, item["resposta"])
                enviar_smtp(email_resp)
                item.update(status="respondido", ts_envio=time.time())
                HIST.adicionar(item)
                marcar(conn, uid, FLAG_RESPONDIDO, "\\Flagged" if item["precisa_humano"] else "")
                salvar_em_enviados(conn, email_resp)
                log(f"Respondido: {remetente} | {assunto}" + ("  [precisa de você ⭐]" if item["precisa_humano"] else ""))
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def enviar_manual(self, id_, texto):
        it = HIST.por_id(id_)
        if not it:
            raise ValueError("conversa não encontrada")
        if it["status"] not in ("simulado", "limite"):
            raise ValueError("essa conversa já foi respondida ou ignorada")
        texto = (texto or it.get("resposta") or "").strip()
        if not texto:
            raise ValueError("resposta vazia")
        email_resp = montar_resposta(
            {"para": it["remetente"], "assunto": it["assunto"],
             "message_id": it["message_id"] if not it["message_id"].startswith("uid-") else None,
             "references": it.get("references", "")}, texto)
        enviar_smtp(email_resp)
        HIST.atualizar(id_, status="enviado_manual", resposta=texto, ts_envio=time.time())
        try:
            conn = conectar_imap()
            marcar(conn, it["uid"], FLAG_RESPONDIDO)
            salvar_em_enviados(conn, email_resp)
            conn.logout()
        except Exception as e:
            log("Aviso ao marcar/salvar envio manual:", e)
        log(f"Enviado manualmente: {it['remetente']} | {it['assunto']}")

    def loop(self):
        while True:
            espera = ci("INTERVALO_SEGUNDOS")
            if self.rodando and not config_faltando():
                with self.lock_ciclo:
                    try:
                        self.processar()
                        self.erro = None
                        self.falhas = 0
                    except Exception as e:
                        self.falhas += 1
                        self.erro = str(e)
                        log("Erro ao verificar a caixa:", e)
                        espera = min(espera * (2 ** min(self.falhas, 4)), 900)
                    self.ultima = time.time()
            self.evento.wait(max(espera, 10))
            self.evento.clear()


ROBO = Robo()


def checar():
    resultados = []

    def passo(nome, fn):
        try:
            detalhe = fn() or "OK"
            resultados.append({"item": nome, "ok": True, "detalhe": detalhe})
        except Exception as e:
            resultados.append({"item": nome, "ok": False, "detalhe": str(e)[:300]})

    def t_imap():
        conn = conectar_imap()
        _, d = conn.status("INBOX", "(MESSAGES UNSEEN)")
        conn.logout()
        return "Conectado. " + (d[0].decode() if d and d[0] else "")

    def t_smtp():
        with smtplib.SMTP_SSL(c("SMTP_HOST"), ci("SMTP_PORTA"), timeout=60) as smtp:
            smtp.login(c("EMAIL_USUARIO"), c("EMAIL_SENHA"))
        return "Login de envio OK"

    def t_ia():
        r = perguntar_ia("teste@exemplo.com", "Teste", "Olá, vocês entregam em Recife?")
        return "IA respondeu: " + r["resposta"][:160] + "..."

    passo("Receber emails (IMAP)", t_imap)
    passo("Enviar emails (SMTP)", t_smtp)
    passo("Inteligência artificial (Gemini)", t_ia)
    return resultados


# ---------------------------------------------------------------------------
# Painel (servidor web local)
# ---------------------------------------------------------------------------


def serie_semana(itens):
    hoje = datetime.now().date()
    dias = [hoje - timedelta(days=n) for n in range(6, -1, -1)]
    cont = {d: 0 for d in dias}
    for i in itens:
        if i["status"] in ("respondido", "enviado_manual"):
            d = datetime.fromtimestamp(i.get("ts_envio", i["ts"])).date()
            if d in cont:
                cont[d] += 1
    nomes = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
    return [{"dia": nomes[d.weekday()], "data": d.strftime("%d/%m"), "n": cont[d]} for d in dias]


def estado():
    hoje = datetime.now().date()
    itens = HIST.lista(300)
    de_hoje = [i for i in itens if datetime.fromtimestamp(i["ts"]).date() == hoje]
    return {
        "rodando": ROBO.rodando,
        "automatico": cb("ENVIO_AUTOMATICO"),
        "ultima": ROBO.ultima,
        "erro": ROBO.erro,
        "faltando": config_faltando(),
        "intervalo": ci("INTERVALO_SEGUNDOS"),
        "email": c("EMAIL_USUARIO"),
        "versao": VERSAO,
        "instagram": insta(),
        "stats": {
            "respondidos": sum(1 for i in de_hoje if i["status"] in ("respondido", "enviado_manual")),
            "simulados": sum(1 for i in de_hoje if i["status"] == "simulado"),
            "humano": sum(1 for i in de_hoje if i.get("precisa_humano")),
            "ignorados": sum(1 for i in de_hoje if i["status"] == "ignorado"),
        },
        "semana": serie_semana(itens),
        "total_respondidos": sum(1 for i in itens if i["status"] in ("respondido", "enviado_manual")),
        "historico": itens,
        "logs": list(LOGS)[-150:],
    }


class Painel(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _autorizado(self):
        senha = c("PAINEL_SENHA")
        if not senha:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                _, s = base64.b64decode(auth[6:]).decode().split(":", 1)
                return s == senha
            except Exception:
                return False
        return False

    def _json(self, dados, codigo=200):
        corpo = json.dumps(dados, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _corpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _negar(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="North Power"')
        self.end_headers()

    def do_GET(self):
        if not self._autorizado():
            return self._negar()
        if self.path in ("/", "/index.html"):
            with open(ARQ_PAINEL, "rb") as f:
                corpo = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        elif self.path == "/api/estado":
            self._json(estado())
        elif self.path == "/api/config":
            dados = {k: ("" if k in SECRETOS else c(k)) for k in PADROES}
            dados["_tem"] = {k: bool(c(k)) for k in SECRETOS}
            self._json(dados)
        elif self.path == "/api/base":
            self._json({"itens": BASE_IA.lista(), "categorias": CATEGORIAS})
        else:
            self._json({"erro": "não encontrado"}, 404)

    def do_POST(self):
        if not self._autorizado():
            return self._negar()
        try:
            d = self._corpo()
            if self.path == "/api/controle":
                acao = d.get("acao")
                if acao == "pausar":
                    ROBO.rodando = False
                    log("Robô pausado pelo painel.")
                elif acao == "iniciar":
                    ROBO.rodando = True
                    ROBO.evento.set()
                    log("Robô ligado pelo painel.")
                elif acao == "verificar":
                    ROBO.evento.set()
                elif acao == "modo":
                    CFG["ENVIO_AUTOMATICO"] = "sim" if d.get("automatico") else "nao"
                    salvar_env(CFG)
                    log("Modo alterado para:", "AUTOMÁTICO (envia)" if d.get("automatico") else "TESTE (não envia)")
                    ROBO.evento.set()
                return self._json({"ok": True})
            if self.path == "/api/config":
                for k in PADROES:
                    if k in d:
                        v = str(d[k]).strip()
                        if k in SECRETOS and not v:
                            continue
                        CFG[k] = v
                salvar_env(CFG)
                log("Configurações salvas.")
                ROBO.evento.set()
                return self._json({"ok": True})
            if self.path == "/api/checar":
                return self._json({"resultados": checar()})
            if self.path == "/api/testar-ia":
                r = perguntar_ia("cliente@exemplo.com", d.get("assunto") or "Dúvida", d.get("mensagem", ""))
                return self._json(r)
            if self.path == "/api/base":
                item = BASE_IA.salvar_item(d)
                log(f"Base de conhecimento: '{item['titulo']}' salvo.")
                return self._json({"ok": True, "item": item})
            if self.path == "/api/base/excluir":
                BASE_IA.excluir(d.get("id"))
                log("Base de conhecimento: item excluído.")
                return self._json({"ok": True})
            if self.path == "/api/enviar":
                ROBO.enviar_manual(d.get("id"), d.get("resposta"))
                return self._json({"ok": True})
            if self.path == "/api/descartar":
                it = HIST.por_id(d.get("id"))
                if it and it["status"] in ("simulado", "limite"):
                    HIST.atualizar(it["id"], status="ignorado", motivo="Descartado por você no painel")
                return self._json({"ok": True})
            self._json({"erro": "não encontrado"}, 404)
        except Exception as e:
            self._json({"erro": str(e)}, 400)


# ---------------------------------------------------------------------------
# Início
# ---------------------------------------------------------------------------


def main():
    if "--testar" in sys.argv:
        texto = " ".join(sys.argv[sys.argv.index("--testar") + 1:]) or "Oi, qual o prazo de entrega?"
        print(json.dumps(perguntar_ia("cliente@exemplo.com", "Dúvida", texto), ensure_ascii=False, indent=2))
        return
    if "--checar" in sys.argv:
        for r in checar():
            print(("OK   " if r["ok"] else "ERRO ") + r["item"] + ": " + r["detalhe"])
        return

    modo = "AUTOMÁTICO (envia respostas)" if cb("ENVIO_AUTOMATICO") else "TESTE (só mostra, não envia)"
    log(f"Robô North Power iniciado (versão {VERSAO}). Caixa: {c('EMAIL_USUARIO')} | modo: {modo}")
    if config_faltando():
        log("Falta configurar:", ", ".join(config_faltando()), "(abra o painel > Configurações)")

    threading.Thread(target=ROBO.loop, daemon=True).start()

    if "--sem-painel" in sys.argv:
        while True:
            time.sleep(3600)

    host, porta = c("PAINEL_HOST"), ci("PAINEL_PORTA")
    servidor = ThreadingHTTPServer((host, porta), Painel)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{porta}"
    log(f"Painel aberto em {url}  (deixe esta janela aberta)")
    if "--nao-abrir" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        log("Encerrado.")


if __name__ == "__main__":
    main()
