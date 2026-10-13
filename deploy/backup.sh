#!/usr/bin/env bash
# Cópia de segurança diária do banco de dados (usuários e conversas).
# Guarda os últimos 14 dias em /home/castilho/backups.
set -euo pipefail

DB=/home/castilho/Castilho-AI-/data/chat.db
DEST=/home/castilho/backups
mkdir -p "$DEST"
sqlite3 "$DB" ".backup '$DEST/chat-$(date +%F).db'"
find "$DEST" -name 'chat-*.db' -mtime +14 -delete
