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
    "awaiting_new_label", "awaiting_new_duration", "awaiting_manage_user", "awaiting_manage_action", "awaiting_manage_confirm", "profile_values", "assign_users", "assign_profiles",
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
    keyboard = [["🔑 Получить ключ"], ["👤 Пользователи", "➕ Новый ключ"], ["⚙️ Управление ключами"], ["🔑 Профили", "➕ Профиль"], ["🧩 Назначить профили", "🔗 Выдать ссылки"]]
    await update.effective_message.reply_text(
        "Панель управления VLESS. Ссылки — секреты доступа.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True),
    )


async def users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    db = registry()
    # Show safe metadata only; never list UUIDs in a general user list.
    rows = db.list_users()
    text = "\n".join(f"#{r['id']} {r['label']} — {r['status']} — до {r['expires_at'] or 'бессрочно'}" for r in rows[:50]) or "Пользователей пока нет."
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
    if context.user_data.get("awaiting_manage_confirm"):
        action, user_id = context.user_data.pop("awaiting_manage_confirm")
        if text != "Подтвердить":
            await update.effective_message.reply_text("Отменено."); return
        try:
            from .runtime import runtime_from_env
            svc = runtime_from_env(registry())
            if action == "delete": svc.delete(user_id)
            else: svc.transition(user_id, {"pause":"paused", "resume":"active", "revoke":"revoked"}[action])
            await update.effective_message.reply_text("Изменение применено в Xray.")
        except Exception as exc:
            await update.effective_message.reply_text(f"Не применено; состояние сохранено ({type(exc).__name__}).")
        return
    if context.user_data.get("awaiting_manage_user"):
        uid = text.lstrip("#").split()[0]
        if uid not in context.user_data.get("manage_users", {}):
            await update.effective_message.reply_text("Выберите пользователя кнопкой или /cancel."); return
        context.user_data.pop("awaiting_manage_user", None)
        context.user_data["manage_id"] = int(uid); context.user_data["awaiting_manage_action"] = True
        await update.effective_message.reply_text("Выберите действие: pause / resume / revoke / delete")
        return
    if context.user_data.get("awaiting_manage_action"):
        action = text.lower()
        if action not in {"pause", "resume", "revoke", "delete"}:
            await update.effective_message.reply_text("Действие: pause, resume, revoke или delete."); return
        uid = context.user_data.pop("manage_id"); context.user_data.pop("awaiting_manage_action", None)
        context.user_data["awaiting_manage_confirm"] = (action, uid)
        await update.effective_message.reply_text(f"Подтвердите {action} пользователя #{uid}: отправьте «Подтвердить» или /cancel")
        return
    if context.user_data.get("awaiting_new_duration"):
        if context.user_data["awaiting_new_duration"] != "new-key-duration":
            context.user_data.pop("new_label", None)
            context.user_data.pop("awaiting_new_duration", None)
            await update.effective_message.reply_text("Срок устарел; начните выдачу ключа заново.")
            return
        days = {"1 день":1,"7 дней":7,"30 дней":30,"Бессрочно":None}.get(text)
        if text not in {"1 день","7 дней","30 дней","Бессрочно"}:
            await update.effective_message.reply_text("Выберите срок кнопкой или /cancel."); return
        label = context.user_data.pop("new_label"); context.user_data.pop("awaiting_new_duration", None)
        try:
            from .runtime import runtime_from_env
            item = runtime_from_env(registry()).create(label, days)
            await update.effective_message.reply_text("Ключ применён в Xray. Ссылка:")
            await send_links(update, item["id"])
        except Exception as exc:
            await update.effective_message.reply_text(f"Ключ не выдан: применение не подтверждено ({type(exc).__name__}).")
        return
    if text == "🔑 Получить ключ":
        context.user_data.pop("profile_values", None)
        context.user_data.pop("awaiting_new_label", None)
        context.user_data.pop("new_label", None)
        context.user_data.pop("awaiting_new_duration", None)
        context.user_data["awaiting_new_label"] = True
        await update.effective_message.reply_text("Введите метку нового пользователя (1–80 символов), либо нажмите «Отмена».")
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
    elif text == "⚙️ Управление ключами":
        rows = registry().list_users()
        context.user_data["manage_users"] = {str(r["id"]): r["label"] for r in rows}
        context.user_data["awaiting_manage_user"] = True
        await update.effective_message.reply_text("Выберите пользователя:", reply_markup=ReplyKeyboardMarkup([[f"#{r['id']} {r['label']}"] for r in rows] + [["Отмена"]], resize_keyboard=True))
    elif text == "➕ Новый ключ":
        context.user_data.pop("profile_values", None)
        context.user_data.pop("new_label", None)
        context.user_data.pop("awaiting_new_duration", None)
        context.user_data["awaiting_new_label"] = True
        await update.effective_message.reply_text("Введите метку нового пользователя (1–80 символов), либо нажмите «Отмена».")
        return
    elif context.user_data.get("awaiting_new_label"):
        context.user_data.pop("awaiting_new_label", None)
        context.user_data["new_label"] = text
        context.user_data["awaiting_new_duration"] = "new-key-duration"
        await update.effective_message.reply_text("Выберите срок:", reply_markup=ReplyKeyboardMarkup([["1 день", "7 дней"], ["30 дней", "Бессрочно"], ["Отмена"]], resize_keyboard=True, one_time_keyboard=True))
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
    async def cleanup_job(context):
        try:
            from .runtime import runtime_from_env
            runtime_from_env(registry()).cleanup_expired()
        except Exception:
            # Retry on the next interval; never issue links for expired records.
            return
    app.job_queue.run_repeating(cleanup_job, interval=60, first=10)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.run_polling()


if __name__ == "__main__":
    main()
