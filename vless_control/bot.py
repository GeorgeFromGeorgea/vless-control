"""Minimal owner-only Telegram admin bot; no host installer actions are exposed."""
from __future__ import annotations

import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .profile_wizard import PROMPTS, accept_value, next_field
from .registry import Registry, vless_uri


def admins_from_env() -> set[int]:
    raw = os.getenv("TELEGRAM_ADMIN_IDS", "").strip()
    return {int(item.strip()) for item in raw.split(",") if item.strip().isdigit()} if raw else set()


def authorized(update: Update) -> bool:
    return bool(
        update.effective_user
        and update.effective_chat
        and update.effective_chat.type == "private"
        and update.effective_user.id in admins_from_env()
    )


def registry() -> Registry:
    return Registry(os.getenv("DATABASE_PATH", "data/vless-control.sqlite3"))


CANCEL_TEXTS = {"Отмена", "❌ Отмена", "Отменить"}
STATE_KEYS = (
    "awaiting_assign_user", "awaiting_assign_profiles", "awaiting_link_user",
    "awaiting_new_label", "profile_values", "assign_users", "assign_profiles",
    "selected_assign_user", "link_users",
)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    for key in STATE_KEYS:
        context.user_data.pop(key, None)
    await update.effective_message.reply_text("Действие отменено.")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cancel(update, context)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        await update.effective_message.reply_text("Доступ запрещён.")
        return
    keyboard = [["🔑 Получить ключ"], ["👤 Пользователи", "➕ Новый ключ"], ["🔑 Профили", "➕ Профиль"], ["🧩 Назначить профили", "🔗 Выдать ссылки"]]
    await update.effective_message.reply_text(
        "Панель управления VLESS. Ссылки — секреты доступа.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True),
    )


async def users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    db = registry()
    # Show safe metadata only; never list UUIDs in a general user list.
    with db._connect() as conn:
        rows = conn.execute("SELECT id,label,active FROM users ORDER BY id DESC LIMIT 50").fetchall()
    text = "\n".join(f"#{r['id']} {r['label']} — {'активен' if r['active'] else 'отключён'}" for r in rows) or "Пользователей пока нет."
    await update.effective_message.reply_text(text)


async def new_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    label = " ".join(context.args).strip()
    if not label:
        await update.effective_message.reply_text("Формат: /new_user имя")
        return
    try:
        item = registry().add_user(label)
    except Exception as exc:
        await update.effective_message.reply_text(f"Не удалось создать пользователя: {type(exc).__name__}")
        return
    await update.effective_message.reply_text(f"Создан пользователь #{item['id']}. Назначьте профили через /assign {item['id']} ID[,ID].")


async def profiles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    db = registry()
    with db._connect() as conn:
        rows = conn.execute("SELECT id,name,host,port,security,transport FROM profiles WHERE active=1 ORDER BY id").fetchall()
    await update.effective_message.reply_text("\n".join(f"#{r['id']} {r['name']} {r['host']}:{r['port']} ({r['security']}/{r['transport']})" for r in rows) or "Профилей нет.")


async def choose_assign(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    with registry()._connect() as conn:
        users = conn.execute("SELECT id,label FROM users WHERE active=1 ORDER BY id DESC LIMIT 20").fetchall()
        profiles = conn.execute("SELECT id,name,port FROM profiles WHERE active=1 ORDER BY id").fetchall()
    if not users or not profiles:
        await update.effective_message.reply_text("Сначала добавьте пользователя (/new_user) и профили в реестр.")
        return
    context.user_data["assign_users"] = {str(u["id"]): u["label"] for u in users}
    context.user_data["assign_profiles"] = [dict(p) for p in profiles]
    buttons = [[f"#{u['id']} {u['label']}" ] for u in users]
    context.user_data["awaiting_assign_user"] = True
    await update.effective_message.reply_text("Выберите пользователя:", reply_markup=ReplyKeyboardMarkup(buttons + [["Отмена"]], resize_keyboard=True, one_time_keyboard=True))


async def choose_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    with registry()._connect() as conn:
        rows = conn.execute("SELECT id,label FROM users WHERE active=1 ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await update.effective_message.reply_text("Пока нет активных пользователей.")
        return
    context.user_data["link_users"] = {str(r["id"]): r["label"] for r in rows}
    context.user_data["awaiting_link_user"] = True
    buttons = [[f"#{r['id']} {r['label']}"] for r in rows]
    await update.effective_message.reply_text("Кому показать персональные профили? Ссылки являются секретами.", reply_markup=ReplyKeyboardMarkup(buttons + [["Отмена"]], resize_keyboard=True, one_time_keyboard=True))


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    text = (update.effective_message.text or "").strip()
    # Handle cancel before every wizard guard, field parse, and validation branch.
    if text in CANCEL_TEXTS:
        await cancel(update, context)
        return
    if text == "🔑 Получить ключ":
        host = os.getenv("PUBLIC_SERVER_HOST", "").strip()
        port_raw = os.getenv("XRAY_MANAGED_VLESS_PORT", "")
        if not host or not port_raw.isdigit() or int(port_raw) != 443:
            await update.effective_message.reply_text("Требуются PUBLIC_SERVER_HOST и XRAY_MANAGED_VLESS_PORT=443.")
            return
        import uuid
        db = registry()
        item = None
        try:
            from .deploy import XrayConfigDeployer, reconcile_clients
            import json, subprocess
            config_path = os.getenv("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json")
            config = json.loads(open(config_path, encoding="utf-8").read())
            inbounds = [i for i in config.get("inbounds", []) if i.get("protocol") == "vless" and i.get("port") == 443 and i.get("streamSettings", {}).get("security", "none") == "none" and i.get("streamSettings", {}).get("network", "tcp") == "tcp"]
            if len(inbounds) != 1:
                raise ValueError("ambiguous inbound")
            with db._connect() as conn:
                row = conn.execute("SELECT id FROM profiles WHERE active=1 AND port=443 AND security='none' AND transport='tcp' AND host=? AND sni='' AND public_key='' AND short_id='' AND path='' ORDER BY id LIMIT 1", (host,)).fetchone()
            profile_id = int(row["id"]) if row else db.add_profile("admin-key-443", host, 443, "none", "tcp")
            profile = db.list_connections(-1)
            with db._connect() as conn:
                conn.execute("INSERT INTO user_profiles(user_id,profile_id) VALUES (NULL,NULL)") if False else None
            item = db.add_user(f"admin-{update.effective_user.id}-{uuid.uuid4().hex[:8]}")
            db.assign_profiles(item["id"], [profile_id])
            assignments = db.xray_assignments()
            target = inbounds[0]
            tag = target.get("tag") or "vless-control-443"
            candidate = reconcile_clients(config, {tag: assignments.get(tag, assignments.get("admin-key-443", []))}, target_port=443)
            # Registry profile names are mapped to the generated target tag.
            client = {"id": item["uuid"], "email": f"vless-control-{item['uuid']}"}
            candidate = reconcile_clients(config, {tag: [client]}, target_port=443)
            deployer = XrayConfigDeployer(config_path, os.getenv("XRAY_BINARY", "/usr/local/bin/xray"))
            service = os.getenv("XRAY_SERVICE", "xray")
            def restart():
                subprocess.run(["systemctl", "restart", service], check=True, timeout=60)
                subprocess.run(["systemctl", "is-active", "--quiet", service], check=True, timeout=15)
                import socket
                with socket.create_connection(("127.0.0.1", 443), timeout=3):
                    pass
            deployer.deploy(candidate, restart=restart)
            await send_links(update, item["id"])
        except Exception as exc:
            if item is not None:
                try: db.deactivate_user(item["id"])
                except Exception: pass
            await update.effective_message.reply_text(f"Ключ не выдан: применение не подтверждено ({type(exc).__name__}).")
        return
    if context.user_data.get("awaiting_assign_user"):
        user_id = text.split(maxsplit=1)[0].lstrip("#")
        if user_id not in context.user_data.get("assign_users", {}):
            await update.effective_message.reply_text("Выберите пользователя кнопкой или нажмите «Отмена».")
            return
        context.user_data["selected_assign_user"] = int(user_id)
        context.user_data.pop("awaiting_assign_user", None)
        context.user_data["awaiting_assign_profiles"] = True
        options = context.user_data["assign_profiles"]
        buttons = [[f"#{p['id']} {p['name']} — :{p['port']}"] for p in options]
        await update.effective_message.reply_text("Выберите один профиль за раз, либо отправьте список ID через запятую для нескольких портов.", reply_markup=ReplyKeyboardMarkup(buttons + [["Отмена"]], resize_keyboard=True, one_time_keyboard=True))
        return
    if context.user_data.get("awaiting_assign_profiles"):
        try:
            ids = [int(part.strip().lstrip("#").split()[0]) for part in text.split(",")]
            if not ids or len(set(ids)) != len(ids) or any(int(p["id"]) not in ids for p in context.user_data["assign_profiles"]):
                raise ValueError
            registry().assign_profiles(context.user_data["selected_assign_user"], ids)
            context.user_data.pop("awaiting_assign_profiles", None)
            context.user_data.pop("assign_profiles", None)
            context.user_data.pop("assign_users", None)
            context.user_data.pop("selected_assign_user", None)
            await update.effective_message.reply_text("Профили назначены. Для выдачи нажмите «Выдать ссылки» или отправьте /links ID.")
        except Exception:
            await update.effective_message.reply_text("Не получилось распознать выбор. Отправьте ID профилей через запятую или «Отмена».")
        return
    if context.user_data.get("awaiting_link_user"):
        user_id = text.split(maxsplit=1)[0].lstrip("#")
        if user_id not in context.user_data.get("link_users", {}):
            await update.effective_message.reply_text("Выберите пользователя кнопкой или нажмите «Отмена».")
            return
        context.user_data.pop("awaiting_link_user", None)
        context.user_data.pop("link_users", None)
        await send_links(update, int(user_id))
        return
    if context.user_data.get("awaiting_new_label"):
        context.user_data.pop("awaiting_new_label", None)
        try:
            created = registry().add_user(text)
            await update.effective_message.reply_text(f"Создан пользователь #{created['id']}. Теперь нажмите «Назначить профили».")
        except Exception as exc:
            await update.effective_message.reply_text(f"Не удалось создать запись ({type(exc).__name__}). Проверьте метку.")
        return
    if context.user_data.get("profile_values") is not None:
        values = context.user_data["profile_values"]
        field = next_field(values)
        if text in {"👤 Пользователи", "➕ Новый ключ", "🧩 Назначить профили", "➕ Профиль", "🔑 Профили", "🔗 Выдать ссылки", "🔑 Получить ключ"}:
            await update.effective_message.reply_text(f"Выберите или введите значение для поля. {PROMPTS[field]} Нажмите «Отмена» для выхода.")
            return
        try:
            values = accept_value(values, field, text)
        except ValueError as exc:
            await update.effective_message.reply_text(f"Ошибка: {exc}\n{PROMPTS[field]}")
            return
        context.user_data["profile_values"] = values
        field = next_field(values)
        if field is None:
            try:
                profile_id = registry().add_profile(**values)
                context.user_data.pop("profile_values", None)
                await update.effective_message.reply_text(f"Профиль #{profile_id} создан. Теперь его можно назначить пользователю.")
            except Exception as exc:
                await update.effective_message.reply_text(f"Профиль не создан ({type(exc).__name__}). Проверьте данные и повторите.")
                context.user_data.pop("profile_values", None)
        else:
            await update.effective_message.reply_text(PROMPTS[field])
        return
    if text == "👤 Пользователи":
        await users(update, context)
    elif text == "➕ Новый ключ":
        context.user_data["awaiting_new_label"] = True
        await update.effective_message.reply_text("Введите метку нового пользователя (1–80 символов), либо нажмите «Отмена».")
    elif context.user_data.get("awaiting_new_label"):
        context.user_data.pop("awaiting_new_label", None)
        try:
            created = registry().add_user(text)
            await update.effective_message.reply_text(f"Создан пользователь #{created['id']}. Теперь нажмите «Назначить профили».")
        except Exception as exc:
            await update.effective_message.reply_text(f"Не удалось создать запись ({type(exc).__name__}). Проверьте метку.")
    elif text == "➕ Профиль":
        context.user_data["profile_values"] = {}
        await update.effective_message.reply_text(PROMPTS["name"])
    elif text == "🔑 Профили":
        await profiles(update, context)
    elif text == "🔗 Выдать ссылки":
        await choose_links(update, context)
    elif text == "🧩 Назначить профили":
        await choose_assign(update, context)


async def send_links(update: Update, user_id: int) -> None:
    records = registry().list_connections(user_id)
    if not records:
        await update.effective_message.reply_text("У этого пользователя нет активных назначенных профилей.")
        return
    # Deliver in the existing owner/admin control chat; never log URI text.
    await update.effective_message.reply_text("Внимание: следующие ссылки дают доступ. Перешлите их пользователю только в личном чате.")
    for item in records:
        await update.effective_message.reply_text(vless_uri(item))


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await button_router(update, context)


async def assign_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    try:
        if len(context.args) != 2:
            raise ValueError
        user_id = int(context.args[0])
        profile_ids = [int(x) for x in context.args[1].split(",")]
        registry().assign_profiles(user_id, profile_ids)
        await update.effective_message.reply_text("Профили назначены. Отправьте /links ID для получения ссылок.")
    except Exception:
        await update.effective_message.reply_text("Формат: /assign USER_ID PROFILE_ID[,PROFILE_ID]")


async def links_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    try:
        if len(context.args) != 1:
            raise ValueError
        await send_links(update, int(context.args[0]))
    except Exception:
        await update.effective_message.reply_text("Формат: /links USER_ID")


async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    try:
        if len(context.args) == 2 and context.args[0] == "confirm":
            user_id = int(context.args[1])
            ok = registry().deactivate_user(user_id)
            await update.effective_message.reply_text("Запись деактивирована. Runtime-sync нужен для фактического отзыва в Xray." if ok else "Активный пользователь не найден.")
        elif len(context.args) == 1:
            user_id = int(context.args[0])
            await update.effective_message.reply_text(f"Подтвердите деактивацию пользователя #{user_id}: /revoke confirm {user_id}. Это отключит запись в реестре; для удаления из работающего Xray понадобится синхронизация.")
        else:
            raise ValueError
    except Exception:
        await update.effective_message.reply_text("Формат: /revoke USER_ID, затем /revoke confirm USER_ID")


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not admins_from_env():
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and at least one numeric TELEGRAM_ADMIN_IDS; refusing open access")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", main_menu))
    app.add_handler(CommandHandler("new_user", new_user))
    app.add_handler(CommandHandler("users", users))
    app.add_handler(CommandHandler("profiles", profiles))
    app.add_handler(CommandHandler("assign", assign_command))
    app.add_handler(CommandHandler("links", links_command))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.run_polling()


if __name__ == "__main__":
    main()
