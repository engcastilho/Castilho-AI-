"""Comandos de administração para usar no terminal (no seu PC ou no servidor).

Exemplos (dentro da pasta do projeto):
    .venv/bin/python -m app.cli list-users
    .venv/bin/python -m app.cli reset-password michael
    .venv/bin/python -m app.cli make-admin maria
No Windows, troque `.venv/bin/python` por `.venv\\Scripts\\python`.
"""

import argparse
import getpass
import sys
from datetime import datetime

from .auth import MIN_PASSWORD_LENGTH, hash_password
from .config import settings
from .storage import Storage


def find_user(storage: Storage, username: str) -> dict:
    user = storage.get_user_with_password(username)
    if user is None:
        sys.exit(f"Usuário '{username}' não encontrado.")
    return user


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Administração do Castilho AI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-users", help="lista os usuários cadastrados")
    reset = sub.add_parser("reset-password", help="define uma nova senha para um usuário")
    reset.add_argument("username")
    admin = sub.add_parser("make-admin", help="transforma um usuário em administrador")
    admin.add_argument("username")
    args = parser.parse_args(argv)

    storage = Storage(settings.db_path)

    if args.command == "list-users":
        for u in storage.list_users():
            status = "admin" if u["is_admin"] else ("ativo" if u["is_approved"] else "aguardando/bloqueado")
            created = datetime.fromtimestamp(u["created_at"]).strftime("%d/%m/%Y")
            print(f"{u['username']:<30} {status:<22} {created}")

    elif args.command == "reset-password":
        user = find_user(storage, args.username)
        password = getpass.getpass("Nova senha (não aparece enquanto você digita): ")
        if len(password) < MIN_PASSWORD_LENGTH:
            sys.exit(f"A senha deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")
        if getpass.getpass("Repita a nova senha: ") != password:
            sys.exit("As senhas não conferem.")
        storage.set_password(user["id"], hash_password(password))
        print(f"Senha de '{user['username']}' alterada.")

    elif args.command == "make-admin":
        user = find_user(storage, args.username)
        storage.make_admin(user["id"])
        print(f"'{user['username']}' agora é administrador.")


if __name__ == "__main__":
    main()
