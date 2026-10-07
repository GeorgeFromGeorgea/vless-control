"""Minimal owner-only Telegram admin bot; no host installer actions are exposed."""
from __future__ import annotations

import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .registry import Registry, vless_uri


def admins_from_env() -> set[int]:
    raw = os.getenv("TELEGRAM_ADMIN_IDS", "").strip()
    return {int(item.strip()) for item in raw.split(",") if item.strip().isdigit()} if raw else set()


def authorized(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id in admins_from_env())


def registry() -> Registry:
    return Registry(os.getenv("DATABASE_PATH", "data/vless-control.sqlite3"))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        await update.effective_message.reply_text("Доступ запрещён.")
        return
    keyboard = [["👤 Пользователи", "➕ Новый ключ"], ["🔑 Мои профили"]]
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


async def assign(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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


async def links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    try:
        if len(context.args) != 1:
            raise ValueError
        records = registry().list_connections(int(context.args[0]))
        if not records:
            await update.effective_message.reply_text("Активных профилей не найдено.")
            return
        await update.effective_message.reply_text("Ссылки — секреты доступа. Передавайте пользователю лично, не публикуйте в группах.")
        for item in records:
            await update.effective_message.reply_text(vless_uri(item))
    except Exception:
        await update.effective_message.reply_text("Формат: /links USER_ID")


async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    try:
        user_id = int(context.args[0])
        ok = registry().deactivate_user(user_id)
        await update.effective_message.reply_text("Деактивирован." if ok else "Активный пользователь не найден.")
    except Exception:
        await update.effective_message.reply_text("Формат: /revoke USER_ID")


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    text = update.effective_message.text
    if text == "👤 Пользователи":
        await users(update, context)
    elif text == "🔑 Мои профили":
        await profiles(update, context)
    elif text == "➕ Новый ключ":
        await update.effective_message.reply_text("Используйте /new_user ИМЯ, затем назначьте профили.")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not admins_from_env():
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and at least one numeric TELEGRAM_ADMIN_IDS; refusing open access")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new_user", new_user))
    app.add_handler(CommandHandler("users", users))
    app.add_handler(CommandHandler("profiles", profiles))
    app.add_handler(CommandHandler("assign", assign))
    app.add_handler(CommandHandler("links", links))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.run_polling()


if __name__ == "__main__":
    main()
