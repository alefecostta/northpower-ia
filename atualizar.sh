#!/usr/bin/env bash
# Chamado a cada minuto: se houver versão nova no GitHub, baixa e reinicia o robô.
cd "$(dirname "$0")" || exit 1
git fetch -q origin main 2>/dev/null || exit 0
ATUAL=$(git rev-parse HEAD)
NOVA=$(git rev-parse origin/main)
if [ "$ATUAL" != "$NOVA" ]; then
  git reset -q --hard origin/main
  ./venv/bin/pip install -q --disable-pip-version-check -r requirements.txt
  chmod +x atualizar.sh instalar_vps.sh
  systemctl restart northpower
  echo "$(date '+%d/%m/%Y %H:%M') atualizado para $(git rev-parse --short HEAD): $(git log -1 --pretty=%s)" >> atualizacoes.log
fi
