import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..storage import Storage


router = Router(name="admin")
log = logging.getLogger(__name__)

PAGE_SIZE = 5
CB_PAGE = "users:page:"
CB_SELECT = "users:select:"
CB_REMOVE = "users:remove:"
CB_CANCEL = "users:cancel:"
CB_CLOSE = "users:close"
CB_NOOP = "users:noop"

ADMIN_HELP = (
    "<b>Admin commands</b>\n"
    "/list_users - list and remove users\n"
    "/adduser &lt;user_id&gt; - add user\n\n"
    "<b>Owner only</b>\n"
    "/add_admin &lt;user_id&gt; - increase rank to admin\n"
    "/rm_admin &lt;user_id&gt; - decrease rank to user"
)


def parse_user_id(arguments: str | None) -> int | None:
    try:
        user_id = int((arguments or "").strip())
    except ValueError:
        return None
    return user_id if user_id > 0 else None


def _callback_number(data: str | None, prefix: str) -> int | None:
    try:
        return int((data or "").removeprefix(prefix))
    except ValueError:
        return None


def _callback_pair(data: str | None, prefix: str) -> tuple[int, int] | None:
    first, separator, second = (data or "").removeprefix(prefix).partition(":")
    if not separator:
        return None
    try:
        return int(first), int(second)
    except ValueError:
        return None


def _user_label(user_id: int, role: str) -> str:
    icons = {"owner": "👑", "admin": "🛠️", "user": "👤"}
    return f"{icons[role]} {user_id} ({role})"


def _list_view(storage: Storage, page: int) -> tuple[str, InlineKeyboardMarkup]:
    users = storage.list_users()
    total_pages = max(1, (len(users) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    rows: list[list[InlineKeyboardButton]] = []

    for user_id, role in users[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        callback = CB_NOOP if role == "owner" else f"{CB_SELECT}{user_id}:{page}"
        rows.append([InlineKeyboardButton(text=_user_label(user_id, role), callback_data=callback)])

    rows.append([
        InlineKeyboardButton(
            text="◀",
            callback_data=f"{CB_PAGE}{page - 1}" if page > 0 else CB_NOOP,
        ),
        InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data=CB_CLOSE),
        InlineKeyboardButton(
            text="▶",
            callback_data=f"{CB_PAGE}{page + 1}" if page + 1 < total_pages else CB_NOOP,
        ),
    ])
    text = (
        f"<b>Allowed users</b> ({len(users)} total)\n"
        "Select a user to remove them. Select the page number to close."
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("admin_help"))
async def admin_help(message: Message, storage: Storage) -> None:
    if message.from_user and storage.is_admin(message.from_user.id):
        await message.answer(ADMIN_HELP)


@router.message(Command("list_users"))
async def list_users(message: Message, storage: Storage) -> None:
    if not message.from_user or not storage.is_admin(message.from_user.id):
        return
    text, keyboard = _list_view(storage, 0)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == CB_NOOP)
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == CB_CLOSE)
async def close_list(callback: CallbackQuery, storage: Storage) -> None:
    if not callback.from_user or not storage.is_admin(callback.from_user.id):
        await callback.answer()
        return
    if callback.message:
        await callback.message.edit_text("User list closed.")
    await callback.answer()


@router.callback_query(F.data.startswith(CB_PAGE))
async def change_page(callback: CallbackQuery, storage: Storage) -> None:
    if not callback.from_user or not storage.is_admin(callback.from_user.id):
        await callback.answer()
        return
    page = _callback_number(callback.data, CB_PAGE)
    if page is None:
        await callback.answer("Invalid action.", show_alert=True)
        return
    text, keyboard = _list_view(storage, page)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith(CB_SELECT))
async def select_user(callback: CallbackQuery, storage: Storage) -> None:
    if not callback.from_user or not storage.is_admin(callback.from_user.id):
        await callback.answer()
        return
    parsed = _callback_pair(callback.data, CB_SELECT)
    if parsed is None:
        await callback.answer("Invalid action.", show_alert=True)
        return
    user_id, page = parsed
    role = storage.role(user_id)
    if role is None:
        await callback.answer("User no longer exists.", show_alert=True)
        return
    if role == "owner":
        await callback.answer("Owner cannot be removed.", show_alert=True)
        return
    if role == "admin" and not storage.is_owner(callback.from_user.id):
        await callback.answer("Only owner can remove an admin.", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirm", callback_data=f"{CB_REMOVE}{user_id}:{page}"),
        InlineKeyboardButton(text="❌ Cancel", callback_data=f"{CB_CANCEL}{page}"),
    ]])
    if callback.message:
        await callback.message.edit_text(
            f"Remove <code>{user_id}</code> ({role})?",
            reply_markup=keyboard,
        )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_REMOVE))
async def confirm_remove(callback: CallbackQuery, storage: Storage) -> None:
    if not callback.from_user or not storage.is_admin(callback.from_user.id):
        await callback.answer()
        return
    parsed = _callback_pair(callback.data, CB_REMOVE)
    if parsed is None:
        await callback.answer("Invalid action.", show_alert=True)
        return
    user_id, page = parsed
    role = storage.role(user_id)
    if role == "owner" or (role == "admin" and not storage.is_owner(callback.from_user.id)):
        await callback.answer("You cannot remove this user.", show_alert=True)
        return

    removed = storage.remove_user(user_id)
    if removed:
        log.info("Access changed action=remove_user actor=%s target=%s", callback.from_user.id, user_id)
    text, keyboard = _list_view(storage, page)
    if callback.message:
        result = f"Removed <code>{user_id}</code>.\n\n" if removed else "User was already removed.\n\n"
        await callback.message.edit_text(result + text, reply_markup=keyboard)
    await callback.answer("User removed." if removed else "User not found.")


@router.callback_query(F.data.startswith(CB_CANCEL))
async def cancel_remove(callback: CallbackQuery, storage: Storage) -> None:
    if not callback.from_user or not storage.is_admin(callback.from_user.id):
        await callback.answer()
        return
    page = _callback_number(callback.data, CB_CANCEL)
    if page is None:
        await callback.answer("Invalid action.", show_alert=True)
        return
    text, keyboard = _list_view(storage, page)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.message(Command("adduser"))
async def add_user(message: Message, command: CommandObject, storage: Storage) -> None:
    if not message.from_user or not storage.is_admin(message.from_user.id):
        return
    user_id = parse_user_id(command.args)
    if user_id is None:
        await message.answer("Usage: <code>/adduser &lt;user_id&gt;</code>")
    elif storage.add_user(user_id):
        log.info("Access changed action=add_user actor=%s target=%s", message.from_user.id, user_id)
        await message.answer(f"Added user <code>{user_id}</code>.")
    else:
        await message.answer(f"<code>{user_id}</code> already has access.")


@router.message(Command("add_admin"))
async def add_admin(message: Message, command: CommandObject, storage: Storage) -> None:
    if not message.from_user or not storage.is_owner(message.from_user.id):
        return
    user_id = parse_user_id(command.args)
    if user_id is None:
        await message.answer("Usage: <code>/add_admin &lt;user_id&gt;</code>")
    elif storage.promote(user_id):
        log.info("Access changed action=promote actor=%s target=%s", message.from_user.id, user_id)
        await message.answer(f"Promoted <code>{user_id}</code> to admin.")
    else:
        await message.answer(f"<code>{user_id}</code> must be an existing user.")


@router.message(Command("rm_admin"))
async def remove_admin(message: Message, command: CommandObject, storage: Storage) -> None:
    if not message.from_user or not storage.is_owner(message.from_user.id):
        return
    user_id = parse_user_id(command.args)
    if user_id is None:
        await message.answer("Usage: <code>/rm_admin &lt;user_id&gt;</code>")
    elif storage.demote(user_id):
        log.info("Access changed action=demote actor=%s target=%s", message.from_user.id, user_id)
        await message.answer(f"Demoted <code>{user_id}</code> to user.")
    else:
        await message.answer(f"<code>{user_id}</code> is not an admin.")
