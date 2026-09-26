#!/usr/bin/env bash
# Instala o robô North Power na VPS (Ubuntu). Rode uma vez: bash instalar_vps.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo ""
echo "=== 1/4 Instalando o necessário (1 a 2 minutos) ==="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv git curl >/dev/null
python3 -m venv venv
./venv/bin/pip install -q --disable-pip-version-check -r requirements.txt
chmod +x atualizar.sh

echo ""
echo "=== 2/4 Configuração ==="
if [ -f .env ]; then
  echo "Arquivo .env já existe, mantendo as configurações atuais."
else
  read -rp "Email da loja [contato@northpowerbr.com]: " EMAIL
  EMAIL=${EMAIL:-contato@northpowerbr.com}
  read -rsp "Senha do email (não aparece enquanto digita): " SENHA; echo
  read -rsp "Chave do Gemini / AI Studio (não aparece): " CHAVE; echo
  read -rsp "Crie uma senha para entrar no painel (não aparece): " PSENHA; echo
  read -rp "Ligar o envio automático agora? (s/N): " AUTO
  [[ "$AUTO" =~ ^[sS] ]] && AUTO=sim || AUTO=nao
  cat > .env <<EOF
EMAIL_USUARIO=$EMAIL
EMAIL_SENHA=$SENHA
GEMINI_API_KEY=$CHAVE
ENVIO_AUTOMATICO=$AUTO
PAINEL_HOST=127.0.0.1
PAINEL_PORTA=8765
PAINEL_SENHA=$PSENHA
EOF
  chmod 600 .env
fi

echo ""
echo "=== 3/4 Deixando o robô ligado 24h e com atualização automática ==="
cat > /etc/systemd/system/northpower.service <<EOF
[Unit]
Description=North Power - Atendente IA
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python app.py --nao-abrir
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/northpower-atualizar.service <<EOF
[Unit]
Description=North Power - busca atualizações no GitHub

[Service]
Type=oneshot
Environment=HOME=/root
ExecStart=$DIR/atualizar.sh
EOF

cat > /etc/systemd/system/northpower-atualizar.timer <<EOF
[Unit]
Description=North Power - verifica atualizações a cada minuto

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now northpower >/dev/null 2>&1
systemctl enable --now northpower-atualizar.timer >/dev/null 2>&1
systemctl restart northpower

echo ""
echo "=== 4/4 Painel pela internet (opcional) ==="
echo "Se você criou um subdomínio (ex.: painel.northpowerbr.com) apontando para o IP desta VPS,"
read -rp "digite ele aqui. Ou aperte Enter para pular: " DOMINIO
if [ -n "$DOMINIO" ]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy >/dev/null
  cat > /etc/caddy/Caddyfile <<EOF
$DOMINIO {
    reverse_proxy 127.0.0.1:8765
}
EOF
  systemctl enable caddy >/dev/null 2>&1
  systemctl restart caddy
  echo "Painel: https://$DOMINIO  (usuário: qualquer nome · senha: a que você criou)"
fi

sleep 3
echo ""
if systemctl is-active --quiet northpower; then
  echo "✅ Pronto! O robô está rodando e se atualiza sozinho quando houver mudança no GitHub."
else
  echo "⚠️  O robô não iniciou. Veja o erro com: journalctl -u northpower -n 50"
fi
echo "Ver o que ele está fazendo:   journalctl -u northpower -f"
