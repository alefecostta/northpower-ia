# Robô de atendimento por email com IA — North Power

Ele verifica a caixa **contato@northpowerbr.com** a cada 1 minuto. Para cada email de cliente, lê o que a pessoa pediu, gera uma resposta personalizada com o **Gemini** (Google AI Studio) e responde na mesma conversa, sempre direcionando para o Instagram **@northpowerbr**.

## Rodar no seu computador (com painel visual)

1. Dê dois cliques em **INICIAR.bat**.
   - Se o Python não estiver instalado, ele tenta instalar sozinho. Depois é só abrir o INICIAR.bat de novo.
2. O painel abre no navegador em **http://127.0.0.1:8765**.
3. Vá em **Configurações**, preencha a **senha do email** e a **chave do Gemini**, clique em **Testar conexão**. Tem que aparecer ✓ nos 3 itens.
4. O robô começa no **Modo teste**: ele lê os emails e mostra a resposta da IA no painel, **mas não envia nada**. Você pode editar e enviar uma por uma pelo botão.
5. Quando gostar das respostas, clique em **Automático (envia)** no topo.

Deixe a janela preta aberta: se fechar, o robô para.

**Menu lateral do painel:** Visão geral · Caixa de entrada (emails recebidos e o que a IA respondeu) · Precisam de você · Informações da loja (o que a IA sabe) · Testar a IA · Caixa de e-mail · Inteligência artificial · Comportamento · Logs.

---

## Subir na VPS (Hostinger) com atualização automática

Como funciona: o código fica num repositório privado no GitHub. A VPS confere o GitHub a cada minuto e, se tiver versão nova, baixa e reinicia o robô sozinha. As senhas (`.env`), o histórico e as informações da loja ficam só na VPS e nunca são apagados por uma atualização.

1. Na VPS (Terminal do navegador da Hostinger), gere a chave de acesso ao GitHub:
   ```
   apt-get update -qq && apt-get install -y -qq git
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C vps-northpower
   cat ~/.ssh/id_ed25519.pub
   ```
2. No GitHub: repositório → **Settings → Deploy keys → Add deploy key**. Cole a linha que apareceu, título `VPS`, **sem** marcar "Allow write access".
3. Na VPS:
   ```
   ssh-keyscan github.com >> ~/.ssh/known_hosts
   git clone git@github.com:SEU_USUARIO/northpower-ia.git /root/northpower-ia
   cd /root/northpower-ia && bash instalar_vps.sh
   ```
   O instalador pede a senha do email, a chave do Gemini e uma senha para o painel.
4. Pronto. Comandos úteis:
   - Ver o robô trabalhando: `journalctl -u northpower -f`
   - Ver as atualizações recebidas: `cat /root/northpower-ia/atualizacoes.log`
   - Abrir o painel pelo seu PC (PowerShell): `ssh -L 8765:127.0.0.1:8765 root@IP_DA_VPS` e acesse http://127.0.0.1:8765

---

## O que ele faz sozinho

- Responde clientes, inclusive quem responde o email de aprovação de compra.
- **Não responde robôs:** ignora emails "no-reply", notificações de plataforma, newsletters e respostas automáticas, então não cria loop.
- **Não responde duas vezes o mesmo email** (marca cada um como tratado dentro da própria caixa).
- **Estrela ⭐ nos casos que precisam de você:** quando a IA percebe reclamação, reembolso ou dúvida sobre um pedido específico, ela responde e marca o email com estrela no webmail.
- Guarda uma cópia de cada resposta na pasta **Enviados**.
- Tem limites de segurança: no máximo 5 respostas por cliente por dia e 300 por dia no total.
- Se a IA falhar, envia uma resposta padrão direcionando pro Instagram.

---

## Passo 1: Desligar a resposta automática da Hostinger

Senão o cliente recebe duas respostas. No painel do email da Hostinger, vá em **Resposta automática** e clique no ícone de lixeira (ou desative).

## Passo 2: Pegar a chave do Gemini

1. Acesse **https://aistudio.google.com/apikey**
2. Clique em **Create API key** e copie a chave.

## Passo 3: Colocar o código no GitHub

1. Crie uma conta em **github.com** (se não tiver).
2. Clique em **New repository**, dê o nome `northpower-ia`, marque **Private** e crie.
3. Clique em **uploading an existing file** e arraste todos os arquivos desta pasta, **exceto** `.env` se você tiver criado um (ele tem senha).
4. Clique em **Commit changes**.

## Passo 4: Colocar no ar no Railway (fica ligado 24h)

1. Acesse **railway.com** e entre com sua conta do GitHub.
2. **New Project** → **Deploy from GitHub repo** → escolha `northpower-ia`.
3. Clique no serviço criado → aba **Variables** → **Raw Editor** e cole (trocando os valores):

```
EMAIL_USUARIO=contato@northpowerbr.com
EMAIL_SENHA=senha_do_email
GEMINI_API_KEY=sua_chave_do_ai_studio
INSTAGRAM_USUARIO=northpowerbr
NOME_REMETENTE=North Power
ENVIO_AUTOMATICO=sim
```

> **Importante:** no servidor use `ENVIO_AUTOMATICO=sim`. Com `nao` ele só gera as respostas e não envia.

4. Salve. O Railway reinicia sozinho.
5. Na aba **Deployments** → **View logs**, deve aparecer:
   `Robô North Power iniciado. Caixa: contato@northpowerbr.com`

O Railway é pago por uso (tem um período de teste). Um robô desse tamanho costuma ficar no plano mais barato. Confira o valor atual no site.

## Passo 5: Testar

Mande um email de outra conta para **contato@northpowerbr.com** com uma pergunta, por exemplo "Oi, quando chega meu pedido?". Em até 1 ou 2 minutos a resposta chega, e nos logs do Railway aparece `Respondido: ...`.

---

## Como mudar o que a IA sabe

No painel, abra **Informações da loja** no menu lateral e clique em **Adicionar informação**. Tudo o que estiver ativo ali a IA usa para responder: prazos de entrega, política de troca, formas de pagamento, dúvidas frequentes e regras de como ela deve falar. **O que não estiver ali ela não inventa.**

As informações ficam salvas no arquivo `base_conhecimento.json`. Ao subir para a VPS, leve esse arquivo junto.

## Configurações opcionais (variáveis)

| Variável | Padrão | O que faz |
|---|---|---|
| `GEMINI_MODELO` | `gemini-2.5-flash` | Modelo da IA. Se der erro 404 nos logs, troque por um modelo listado no AI Studio. |
| `INTERVALO_SEGUNDOS` | `60` | De quanto em quanto tempo verifica a caixa. |
| `HORAS_RETROATIVAS` | `2` | Só responde emails que chegaram nas últimas X horas (evita responder email antigo quando liga o robô). |
| `MAX_POR_REMETENTE_DIA` | `5` | Máximo de respostas para a mesma pessoa por dia. |
| `MAX_RESPOSTAS_DIA` | `300` | Máximo de respostas no dia (proteção contra spam). |

## Problemas comuns

- **"authentication failed" nos logs:** senha do email errada em `EMAIL_SENHA`.
- **"Gemini respondeu 400/403":** chave do AI Studio errada ou sem permissão.
- **"Gemini respondeu 404":** o modelo mudou de nome. Troque `GEMINI_MODELO`.
- **Não responde nada:** confira se o email não caiu no spam da caixa da Hostinger e se o remetente não é um "no-reply".

## Comandos (avançado)

Com Python instalado, na pasta do projeto:

```
pip install -r requirements.txt
copy .env.example .env      (depois edite o .env com a senha e a chave)
python app.py --checar                         (testa login e IA, não envia nada)
python app.py --testar "Vocês trocam tamanho?"  (mostra o que a IA responderia)
python app.py                                   (robô + painel)
python app.py --sem-painel                      (só o robô, para servidor)
```
