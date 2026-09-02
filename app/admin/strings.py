"""Persian user-facing text for the whole bot.

Every string an admin or operator can see lives here, so the wording can be
changed in one place without touching handler logic. Technical records --
structured logs and the audit trail's stored messages -- stay in English;
only their presentation is translated (see ``AUDIT_EVENT_LABELS``).

Chat ids, user ids and template placeholders are wrapped in <code> by the
callers, which keeps Telegram from reordering them inside right-to-left text.
"""

from __future__ import annotations

from app.utils.enums import AuditEvent, OrderStatus, SignalKey

# ---------------------------------------------------------------------------
# Shared buttons
# ---------------------------------------------------------------------------
BTN_BACK = "⬅️ بازگشت"
BTN_ADD = "➕ افزودن"
BTN_DELETE = "🗑 حذف"
BTN_ENABLE = "🟢 فعال کردن"
BTN_DISABLE = "🔴 غیرفعال کردن"
BTN_EDIT_TITLE = "✏️ تغییر عنوان"
BTN_TEST_ACCESS = "🧪 آزمایش دسترسی"
BTN_CONFIRM_DELETE = "✅ بله، حذف کن"
BTN_CANCEL = "❌ انصراف"

STATUS_ENABLED = "🟢 فعال"
STATUS_DISABLED = "🔴 غیرفعال"
YES = "بله"
NO = "خیر"
DASH = "—"

# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------
MENU_ITEMS: list[tuple[str, str]] = [
    ("📊 داشبورد", "dashboard"),
    ("📥 کانال‌های مبدأ", "sources"),
    ("👥 گروه‌های کاری", "workgroups"),
    ("🔀 مسیردهی", "routing"),
    ("👤 اپراتورها", "operators"),
    ("✅ قوانین موفق", "rules_success"),
    ("❌ قوانین ناموفق", "rules_failed"),
    ("👍 واکنش تأیید", "reactions"),
    ("📦 مقصد نتایج", "destinations"),
    ("🔁 واکنش کانال مبدأ", "source_reactions"),
    ("🧾 محتوای نتیجه", "result_content"),
    ("📈 گزارش‌ها", "reports"),
    ("🔎 جستجوی سفارش", "find_order"),
    ("⚙️ تنظیمات", "settings"),
    ("🩺 وضعیت سیستم", "system_status"),
    ("📝 رویدادها", "audit"),
]

MAIN_TEXT = (
    "🤖 <b>ربات مدیریت سفارش</b>\n\n"
    "تمام تنظیمات از همین‌جا انجام می‌شود — هیچ‌چیز در کد ثابت نشده.\n"
    "یک بخش را انتخاب کنید:"
)

#: Deliberately English: this is the only message a stranger ever sees, and
#: it must be readable by whoever stumbles onto the bot.
ACCESS_DENIED = (
    "⛔️ You are not authorised to use this bot's admin panel.\n"
    "Ask a Super Admin to add your Telegram user ID."
)

CMD_ID = (
    "👤 شناسه‌ی عددی شما: <code>{user_id}</code>\n"
    "💬 شناسه‌ی این گفتگو: <code>{chat_id}</code>"
)

GENERIC_ERROR = "مشکلی پیش آمد. لاگ‌ها را بررسی کنید."

#: Registered with Telegram so the command menu is Persian as well.
BOT_COMMANDS: list[tuple[str, str]] = [
    ("start", "باز کردن پنل مدیریت"),
    ("order", "جستجوی سفارش با شماره"),
    ("id", "نمایش شناسه‌ی شما و این گفتگو"),
]
NOT_FOUND = "پیدا نشد"

# ---------------------------------------------------------------------------
# Chats: source channels, work groups, result destinations
# ---------------------------------------------------------------------------
SOURCES_TITLE = "📥 <b>کانال‌های مبدأ</b>"
WORKGROUPS_TITLE = "👥 <b>گروه‌های کاری</b>"
DESTINATIONS_TITLE = "📦 <b>مقصد نتایج</b>"

CHAT_LIST_EMPTY = "\n\nهنوز چیزی تعریف نشده. از دکمه‌ی ➕ افزودن استفاده کنید."

DESTINATIONS_INTRO = (
    "📦 <b>مقصد نتایج</b>\n\n"
    "سفارش‌های نهایی‌شده به این مقصدها فرستاده می‌شوند. گروه، سوپرگروه و "
    "کانال هر سه پشتیبانی می‌شوند و هر وضعیت می‌تواند چند مقصد داشته باشد."
)
BTN_SUCCESS_DESTINATIONS = "✅ مقصد سفارش‌های موفق"
BTN_FAILURE_DESTINATIONS = "❌ مقصد سفارش‌های ناموفق"
DESTINATIONS_FOR = "📦 <b>مقصد سفارش‌های {status}</b>"

BTN_REQUIRED = "الزامی: {value}"
BTN_PRIMARY_IS = "⭐ اصلی"
BTN_MAKE_PRIMARY = "انتخاب به‌عنوان اصلی"
LABEL_REQUIRED = "الزامی"
LABEL_OPTIONAL = "اختیاری"
LABEL_PRIMARY = "⭐ اصلی"

ADD_CHAT_PROMPT = (
    "شناسه‌ی عددی گفتگو را بفرستید.\n\n"
    "راهنما: ربات را به آن گفتگو اضافه کنید و داخلش <code>/id</code> بزنید.\n"
    "شناسه‌ی کانال‌ها و سوپرگروه‌ها با <code>-100</code> شروع می‌شود."
)
ADD_CHAT_INVALID = (
    "❌ این یک شناسه‌ی عددی نیست. نام کاربری پذیرفته نمی‌شود چون قابل تغییر "
    "است؛ شناسه‌ی عددی را بفرستید."
)
CHAT_SAVED = "✅ <b>{title}</b> ذخیره شد"
CHAT_PROBE_FAILED = (
    "\n⚠️ نتوانستم اطلاعات گفتگو را بخوانم: {error}\n"
    "با این حال ذخیره شد — ربات را به گفتگو اضافه کنید و «🧪 آزمایش دسترسی» را بزنید."
)
CHAT_ALLOWED_REACTIONS = "\nواکنش‌های مجاز در این گفتگو: {reactions}"
CHAT_REACTIONS_ALL = "\nواکنش‌ها: همه‌ی ایموجی‌ها مجازند"
CHAT_REACTIONS_LIST = "\nواکنش‌های مجاز: {reactions}"
CHAT_NONE = "هیچ‌کدام"

CHAT_DETAIL = (
    "<b>{title}</b>\n\n"
    "شناسه‌ی گفتگو: <code>{chat_id}</code>\n"
    "نام کاربری: {username}\n"
    "وضعیت: {status}\n"
    "تاریخ ایجاد: {created}\n"
)
CHAT_DETAIL_TOPIC = "تاپیک: {topic}\n"
CHAT_DETAIL_DESTINATION_EXTRA = "الزامی: {required}\nاصلی: {primary}\n"
CHAT_DETAIL_DESTINATION_SOURCE = "مبدأ: {source}\n"
TOPIC_NONE = "کل گفتگو"
BTN_SET_TOPIC = "🧵 تاپیک"
SET_TOPIC_PROMPT = (
    "شناسه‌ی عددی تاپیک را بفرستید.\n\n"
    "برای اینکه پیام‌ها به کل گفتگو برود (نه یک تاپیک خاص)، عدد <code>0</code> را بفرستید.\n\n"
    "شناسه‌ی تاپیک را از لینک پیام داخل آن تاپیک می‌گیرید: در "
    "<code>t.me/c/123456/<b>25</b>/98</code> عدد وسط، یعنی <b>25</b>، شناسه‌ی تاپیک است."
)
TOPIC_SAVED = "✅ تاپیک روی {topic} تنظیم شد"
TOPIC_INVALID = "⛔️ یک عدد صحیح بفرستید (یا 0 برای کل گفتگو)."
BTN_SET_DEST_SOURCE = "📥 مبدأ اختصاصی"
DEST_SOURCE_INTRO = (
    "این مقصد نتایج کدام مبدأ را دریافت کند؟\n\n"
    "«همه‌ی مبدأها» یعنی مقصد مشترک است. اگر برای یک مبدأ حتی یک مقصد اختصاصی "
    "تعریف شود، نتایج آن مبدأ <b>فقط</b> به مقصدهای اختصاصی خودش می‌رود و "
    "مقصدهای مشترک را نادیده می‌گیرد."
)
DEST_SOURCE_ALL = "🌐 همه‌ی مبدأها"
DEST_SOURCE_SAVED = "✅ مبدأ این مقصد روی «{source}» تنظیم شد"
EDIT_TITLE_PROMPT = "عنوان جدید این گفتگو را بفرستید."
CONFIRM_DELETE_CHAT = (
    "⚠️ این مورد حذف شود؟\n\n"
    "سفارش‌های قبلی و تاریخچه‌شان دست‌نخورده می‌مانند؛ فقط همین ردیف تنظیمات حذف می‌شود."
)
ACCESS_TEST_RESULT = "{icon} نتیجه‌ی آزمایش دسترسی: {detail}"

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
ROUTING_TITLE = "🔀 <b>مسیردهی</b>"
ROUTING_EMPTY = (
    "🔀 <b>مسیردهی</b>\n\n"
    "هیچ مسیری تعریف نشده. تا وقتی یک کانال مبدأ به دست‌کم یک گروه کاری وصل "
    "نشود، هیچ سفارشی به گروه‌ها نمی‌رسد.\n\n"
    "یک مبدأ می‌تواند به چند گروه کاری وصل شود."
)
BTN_ADD_ROUTE = "➕ افزودن مسیر"
PICK_SOURCE = "کانال <b>مبدأ</b> را انتخاب کنید:"
PICK_WORK_GROUP = "<b>گروه کاری</b>ای که این مبدأ به آن وصل می‌شود را انتخاب کنید:"
NEED_SOURCE_FIRST = "اول یک کانال مبدأ اضافه کنید."
NEED_WORK_GROUP_FIRST = "اول یک گروه کاری اضافه کنید."
ROUTE_DETAIL = (
    "🔀 <b>مسیر</b>\n\n"
    "مبدأ: {source}\n<code>{source_id}</code>\n\n"
    "گروه کاری: {target}\n<code>{target_id}</code>\n\n"
    "وضعیت: {status}"
)

# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
OPERATORS_TITLE = "👤 <b>اپراتورها</b>"
OPERATORS_INTRO = "فقط این کاربران می‌توانند وضعیت سفارش را تغییر دهند."
OPERATORS_EMPTY = (
    "👤 <b>اپراتورها</b>\n\n"
    "هیچ اپراتوری تعریف نشده. تا وقتی اپراتوری اضافه نشود، پاسخ‌ها و واکنش‌ها "
    "در گروه‌های کاری هیچ سفارشی را تغییر نمی‌دهند."
)
BTN_ADD_OPERATOR = "➕ افزودن اپراتور"
ADD_OPERATOR_PROMPT = (
    "شناسه‌ی عددی تلگرام اپراتور را بفرستید.\n\n"
    "خودش می‌تواند با فرستادن <code>/id</code> به این ربات آن را ببیند."
)
ADD_OPERATOR_INVALID = "❌ یک شناسه‌ی عددی تلگرام بفرستید."
OPERATOR_ADDED = "✅ اپراتور <code>{user_id}</code> اضافه شد."
OPERATOR_SCOPE_ALL = "همه‌ی گروه‌ها"
OPERATOR_SCOPE_SELECTED = "فقط گروه‌های انتخابی"
BTN_OPERATOR_SCOPE = "محدوده: {scope}"
OPERATOR_DETAIL = (
    "👤 <b>{name}</b>\n\n"
    "شناسه‌ی کاربر: <code>{user_id}</code>\n"
    "وضعیت: {status}\n"
    "محدوده: {scope}\n"
    "گروه‌های اختصاص‌یافته: {assigned}"
)

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
SIGNAL_LABELS_FA: dict[SignalKey, str] = {
    SignalKey.REPLY_PHOTO: "پاسخ با عکس",
    SignalKey.REPLY_VIDEO: "پاسخ با ویدیو",
    SignalKey.REPLY_DOCUMENT: "پاسخ با فایل",
    SignalKey.REPLY_AUDIO: "پاسخ با صوت",
    SignalKey.REPLY_VOICE: "پاسخ با ویس",
    SignalKey.REPLY_ANIMATION: "پاسخ با گیف",
    SignalKey.REPLY_TEXT: "پاسخ متنی",
    SignalKey.REACTION: "واکنش (ری‌اکشن)",
}

STATUS_NAMES_FA: dict[OrderStatus, str] = {
    OrderStatus.PENDING: "در انتظار",
    OrderStatus.SUCCESS: "موفق",
    OrderStatus.FAILED: "ناموفق",
    OrderStatus.CONFLICT: "تداخل",
}

MODE_ANY = "هرکدام"
MODE_ALL = "همه"
MODE_NAMES = {"ANY": MODE_ANY, "ALL": MODE_ALL}

BTN_DETECTION = "تشخیص: {status}"
BTN_MODE = "حالت: {mode}"
BTN_TEXT_PATTERNS = "📝 الگوهای متنی"
BTN_RULE_REACTIONS = "😀 واکنش‌ها"

RULES_SCREEN = (
    "{icon} <b>قوانین سفارش {status}</b>\n\n"
    "تشخیص: {detection}\n"
    "حالت: <b>{mode}</b>\n"
    "<i>{mode_help}</i>\n\n"
    "<b>سیگنال‌ها</b>\n{signals}\n\n"
    "الگوهای متنی: {patterns}\n"
    "واکنش‌های پذیرفته‌شده: {reactions}"
)
MODE_HELP_ANY = "هرکدام — رخ دادن هر سیگنال فعالی سفارش را نهایی می‌کند."
MODE_HELP_ALL = "همه — تا همه‌ی سیگنال‌های فعال رخ ندهند، سفارش نهایی نمی‌شود."

WARN_NO_SIGNAL = "\n⚠️ <b>هیچ سیگنالی فعال نیست — این قانون هرگز برقرار نمی‌شود.</b>"
WARN_TEXT_NO_PATTERN = "\n⚠️ «پاسخ متنی» فعال است ولی هیچ الگوی متنی تعریف نشده."
WARN_REACTION_NO_EMOJI = "\n⚠️ «واکنش» فعال است ولی هیچ ایموجی پذیرفته‌شده‌ای تعریف نشده."

CANNOT_SET_ALL_WITHOUT_SIGNAL = (
    "تا وقتی هیچ سیگنالی فعال نیست نمی‌توان حالت را روی «همه» گذاشت — "
    "قانون هرگز برقرار نمی‌شد. اول یک سیگنال را فعال کنید."
)
CANNOT_DISABLE_LAST_SIGNAL = "حالت روی «همه» است: دست‌کم یک سیگنال باید فعال بماند."
UNKNOWN_SIGNAL = "سیگنال ناشناخته"

TEXT_PATTERNS_TITLE = "📝 <b>الگوهای متنی سفارش {status}</b>"
TEXT_PATTERNS_EMPTY = "هنوز الگویی تعریف نشده. قانون «پاسخ متنی» دست‌کم به یکی نیاز دارد."
BTN_ADD_PATTERN = "➕ افزودن الگو"
ADD_PATTERN_PROMPT = (
    "متنی که باید تطبیق داده شود را بفرستید.\n\n"
    "نمونه: <code>انجام شد</code>، <code>اوکی شد</code>، <code>done</code>\n"
    "در مرحله‌ی بعد نوع تطبیق را انتخاب می‌کنید."
)
PATTERN_EMPTY = "❌ الگو خالی است."
PATTERN_CHOSEN = "الگو: <code>{pattern}</code>\n\nنوع تطبیق را انتخاب کنید:"
PATTERN_RESTART = "دوباره از «➕ افزودن الگو» شروع کنید."
MATCH_MODE_NAMES = {
    "EXACT": "دقیقاً برابر",
    "CONTAINS": "شامل باشد",
    "REGEX": "الگوی منظم",
}
CASE_SENSITIVE = "حساس به بزرگی و کوچکی"
CASE_INSENSITIVE = "بدون حساسیت به بزرگی و کوچکی"

RULE_REACTIONS_TITLE = "😀 <b>واکنش‌های تشخیص سفارش {status}</b>"
RULE_REACTIONS_INTRO = (
    "اگر یک <b>اپراتور مجاز</b> با یکی از این ایموجی‌ها روی پیام سفارش واکنش "
    "بگذارد، سیگنال ساخته می‌شود. واکنش بقیه نادیده گرفته می‌شود."
)
RULE_REACTIONS_EMPTY = "هنوز چیزی تعریف نشده."
BTN_ADD_REACTION = "➕ افزودن واکنش"
ADD_REACTION_PROMPT = (
    "یک ایموجی بفرستید تا به‌عنوان واکنش تشخیص پذیرفته شود (مثلاً ✅ یا 👍)."
)
REACTION_INVALID = "❌ یک ایموجی بفرستید."
REACTION_ADDED = "✅ {emoji} اضافه شد"

# ---------------------------------------------------------------------------
# Acknowledgement (result reactions)
# ---------------------------------------------------------------------------
REACTIONS_INTRO = (
    "👍 <b>واکنش تأیید</b>\n\n"
    "واکنشی که ربات بعد از ارسال موفق سفارش به مقصد نتایج، روی پیام می‌گذارد.\n\n"
    "تنظیمات سفارش موفق و ناموفق کاملاً از هم مستقل‌اند."
)
BTN_SUCCESS_ACK = "✅ تأیید سفارش موفق"
BTN_FAILURE_ACK = "❌ تأیید سفارش ناموفق"

ACK_SCREEN = (
    "{icon} <b>واکنش تأیید سفارش {status}</b>\n\n"
    "وضعیت:\n{enabled}\n\n"
    "واکنش:\n{reaction}\n\n"
    "هدف:\n{target}\n\n"
    "سیاست ارسال:\n{policy}\n\n"
    "تلاش مجدد: {retry} (حداکثر {max_retry} بار)\n\n"
    "<i>واکنش فقط بعد از اینکه سفارش واقعاً به مقصد نتایج ارسال شد گذاشته می‌شود.</i>"
)
ACK_NOT_SET = "— تعیین نشده —"
RETRY_ON = "روشن"
RETRY_OFF = "خاموش"

BTN_CHANGE_REACTION = "😀 تغییر واکنش"
BTN_CHANGE_TARGET = "🎯 تغییر هدف"
BTN_DISPATCH_POLICY = "📦 سیاست ارسال"
BTN_TEST_REACTION = "🧪 آزمایش واکنش"

ACK_NEEDS_REACTION_FIRST = (
    "اول یک واکنش تعیین کنید — تأییدِ فعال بدون ایموجی هیچ کاری نمی‌کند."
)
ACK_WARN_NO_REACTION = "فعال است ولی واکنشی تعیین نشده — چیزی گذاشته نخواهد شد."
ACK_WARN_NO_DESTINATION = (
    "هیچ مقصد فعالی برای سفارش {status} تعریف نشده. چون چیزی برای ارسال نیست، "
    "شرط باز است و واکنش گذاشته می‌شود، هرچند چیزی ارسال نشده."
)
ACK_WARN_SAME_EMOJI = (
    "تأیید موفق و ناموفق هر دو از {emoji} استفاده می‌کنند — اپراتورها نمی‌توانند "
    "این دو نتیجه را از هم تشخیص دهند."
)

SET_ACK_REACTION_PROMPT = (
    "ایموجی واکنش تأیید را بفرستید.\n\n"
    "تلگرام به ربات اجازه می‌دهد روی هر پیام فقط <b>یک</b> واکنش بگذارد، پس یک "
    "ایموجی بفرستید (مثلاً ✅، 👍، ❌ یا 👎).\n\n"
    "باید ایموجی‌ای باشد که آن گفتگو اجازه می‌دهد — بعدش با «🧪 آزمایش واکنش» "
    "مطمئن شوید."
)
ACK_REACTION_SAVED = "✅ واکنش تأیید سفارش {status} روی {emoji} تنظیم شد"
ACK_REACTION_NOT_ALLOWED = (
    "⚠️ {chat} ایموجی {emoji} را اجازه نمی‌دهد. مجاز: {allowed}"
)

TARGET_MODE_NAMES = {
    "SMART": "هوشمند",
    "TRIGGER_MESSAGE": "پیام محرک",
    "ORDER_MESSAGE": "پیام سفارش",
}
TARGET_PROMPT = (
    "🎯 <b>هدف واکنش تأیید</b>\n\n"
    "<b>هوشمند</b> (پیش‌فرض) — اگر سفارش با پاسخ اپراتور نهایی شده، واکنش روی "
    "همان پاسخ می‌نشیند؛ و اگر با واکنش نهایی شده (که پیام جدیدی وجود ندارد)، "
    "روی پیام اصلی سفارش.\n\n"
    "<b>پیام محرک</b> — همیشه روی پیامی که وضعیت را ایجاد کرده، و اگر نبود روی "
    "پیام سفارش.\n\n"
    "<b>پیام سفارش</b> — همیشه روی پیام اصلی سفارش."
)

DISPATCH_POLICY_NAMES = {
    "ALL_REQUIRED_DESTINATIONS": "همه‌ی مقصدهای الزامی",
    "ANY_DESTINATION": "هر مقصدی",
    "PRIMARY_DESTINATION": "فقط مقصد اصلی",
}
POLICY_PROMPT = (
    "📦 <b>سیاست ارسال</b>\n\n"
    "وقتی چند مقصد تعریف شده، این تعیین می‌کند واکنش تأیید چه زمانی گذاشته شود.\n\n"
    "<b>همه‌ی مقصدهای الزامی</b> (پیش‌فرض) — همه‌ی مقصدهایی که «الزامی» شده‌اند "
    "باید ارسال شده باشند.\n"
    "<b>هر مقصدی</b> — یک ارسال موفق کافی است.\n"
    "<b>فقط مقصد اصلی</b> — فقط مقصد ⭐ اصلی حساب می‌شود."
)

TEST_REACTION_PROMPT = (
    "🧪 <b>آزمایش واکنش</b>\n\n"
    "شناسه‌ی گفتگو و شناسه‌ی پیام را به این شکل بفرستید:\n"
    "<code>&lt;chat_id&gt; &lt;message_id&gt;</code>\n\n"
    "نمونه: <code>-1001234567890 42</code>"
)
TEST_REACTION_FORMAT = "❌ دقیقاً به این شکل بفرستید: <code>chat_id message_id</code>"
TEST_REACTION_NUMERIC = "❌ هر دو مقدار باید عددی باشند."
TEST_REACTION_NO_EMOJI = "❌ هنوز واکنشی تعیین نشده."
TEST_REACTION_RESULT = "{icon} نتیجه‌ی آزمایش: {detail}"

# ---------------------------------------------------------------------------
# Source-message reactions
# ---------------------------------------------------------------------------
MENU_SOURCE_REACTIONS = "🔁 واکنش کانال مبدأ"
BACKLOG_TITLE = "⏱ پیام‌های زمان خاموشی"
BACKLOG_INTRO = (
    "وقتی ربات خاموش است، تلگرام پست‌های کانال مبدأ را در صف نگه می‌دارد و "
    "هنگام روشن شدن همه را یک‌جا تحویل می‌دهد. این تنظیم می‌گوید با آن‌ها چه کند.\n\n"
    "حالت فعلی: <b>{mode}</b>\n"
    "سقف سن پیام: <b>{minutes} دقیقه</b>"
)
BACKLOG_MODE_LABELS = {
    "ALL": "همه را پردازش کن",
    "IGNORE_DOWNTIME": "هرچه قبل از روشن شدن ربات آمده نادیده گرفته شود",
    "MAX_AGE": "فقط پیام‌های تازه‌تر از سقف سن",
}
BACKLOG_MODE_SAVED = "✅ حالت روی «{mode}» تنظیم شد"
BACKLOG_AGE_PROMPT = "سقف سن پیام را بر حسب دقیقه بفرستید (بین ۱ تا ۱۴۴۰)."
BACKLOG_AGE_SAVED = "✅ سقف سن روی {minutes} دقیقه تنظیم شد"


def backlog_mode_label(mode: str) -> str:
    return BACKLOG_MODE_LABELS.get(str(mode), str(mode))


SOURCE_STAGE_NAMES = {
    "RECEIVED": "دریافت شد",
    "IN_PROGRESS": "در حال انجام",
    "SUCCESS": "موفق",
    "FAILED": "ناموفق",
}
SOURCE_STAGE_HELP = {
    "RECEIVED": "به‌محض اینکه سفارش با موفقیت به گروه کاری رسید.",
    "IN_PROGRESS": "وقتی اپراتور با یکی از ایموجی‌های «در حال انجام» روی سفارش واکنش بگذارد.",
    "SUCCESS": "وقتی سفارش موفق شد.",
    "FAILED": "وقتی سفارش ناموفق شد.",
}

SOURCE_REACTIONS_INTRO = (
    "🔁 <b>واکنش کانال مبدأ</b>\n\n"
    "کسی که سفارش را در کانال مبدأ گذاشته، گروه کاری را نمی‌بیند. این واکنش‌ها "
    "روی همان پیام اولیه گذاشته می‌شوند تا وضعیت را ببیند:\n\n"
    "دریافت شد ← در حال انجام ← موفق / ناموفق\n\n"
    "<i>تلگرام به ربات فقط یک واکنش روی هر پیام می‌دهد، پس هر مرحله جای مرحله‌ی "
    "قبل را می‌گیرد.</i>"
)
SOURCE_STAGE_SCREEN = (
    "🔁 <b>مرحله: {stage}</b>\n\n"
    "<i>{help}</i>\n\n"
    "وضعیت:\n{enabled}\n\n"
    "واکنش:\n{reaction}"
)
BTN_PROGRESS_REACTIONS = "👋 ایموجی‌های «در حال انجام»"
PROGRESS_REACTIONS_TITLE = "👋 <b>ایموجی‌های «در حال انجام»</b>"
PROGRESS_REACTIONS_INTRO = (
    "اگر یک <b>اپراتور مجاز</b> با یکی از این ایموجی‌ها روی پیام سفارش در گروه "
    "کاری واکنش بگذارد، سفارش «در حال انجام» علامت می‌خورد و واکنش کانال مبدأ "
    "به مرحله‌ی مربوطه تغییر می‌کند."
)
PROGRESS_REACTIONS_EMPTY = "هنوز ایموجی‌ای تعریف نشده."
SET_SOURCE_REACTION_PROMPT = (
    "ایموجی این مرحله را بفرستید (مثلاً 👀، ⏳، 💯 یا 👎).\n\n"
    "ربات باید در کانال مبدأ ادمین باشد تا بتواند واکنش بگذارد."
)
SOURCE_REACTION_SAVED = "✅ واکنش مرحله‌ی «{stage}» روی {emoji} تنظیم شد"
SOURCE_NEEDS_REACTION_FIRST = "اول یک ایموجی تعیین کنید."

# ---------------------------------------------------------------------------
# Result content, appended text and the store
# ---------------------------------------------------------------------------
MENU_RESULT_CONTENT = "🧾 محتوای نتیجه"
RESULT_CONTENT_INTRO = (
    "🧾 <b>محتوای نتیجه</b>\n\n"
    "تنظیم اینکه در «مقصد نتایج» چه چیزی فرستاده شود، چه متنی به آخرش اضافه "
    "شود، و آیا وضعیت سفارش در فروشگاه ووکامرس هم به‌روز شود یا نه."
)
RESULT_CONTENT_MODE_NAMES = {
    "ORDER_AND_ATTACHMENTS": "سفارش + پیوست‌های اپراتور",
    "ATTACHMENTS_ONLY": "فقط پیوست‌های اپراتور",
}
BTN_RESULT_MODE = "ارسال: {value}"
RESULT_MODE_PROMPT = (
    "🧾 <b>چه چیزی به مقصد نتایج برود؟</b>\n\n"
    "<b>سفارش + پیوست‌های اپراتور</b> — اول خود سفارش و بعد عکس‌هایی که اپراتور "
    "فرستاده.\n\n"
    "<b>فقط پیوست‌های اپراتور</b> — فقط عکس‌های اپراتور. اگر اپراتور چیزی نفرستاده "
    "باشد، خود سفارش فرستاده می‌شود تا مقصد خالی نماند."
)
BTN_APPEND_TEXT = "📝 متن پایانی"
APPEND_TEXT_SCREEN = (
    "📝 <b>متن پایانی سفارش {status}</b>\n\n"
    "این متن به انتهای سفارش در مقصد نتایج اضافه می‌شود.\n\n"
    "وضعیت:\n{enabled}\n\n"
    "متن:\n{text}"
)
APPEND_TEXT_PROMPT = (
    "متنی که باید به انتهای سفارش اضافه شود را بفرستید.\n\n"
    "نمونه: <code>✅سفارش با موفقیت انجام شد</code>"
)
APPEND_TEXT_SAVED = "✅ متن پایانی ذخیره شد."
APPEND_NEEDS_TEXT_FIRST = "اول یک متن تعیین کنید."

BTN_WOO = "🛒 ووکامرس"
WOO_SCREEN = (
    "🛒 <b>ووکامرس — سفارش {status}</b>\n\n"
    "وضعیت:\n{enabled}\n\n"
    "وضعیت جدید سفارش در فروشگاه:\n{woo_status}\n\n"
    "یادداشت:\n{note_enabled}\n{note}"
)
WOO_STORE_SCREEN = (
    "🛒 <b>اتصال فروشگاه</b>\n\n"
    "آدرس:\n{base_url}\n\n"
    "کلید مصرف‌کننده:\n{key}\n\n"
    "رمز مصرف‌کننده:\n{secret}\n\n"
    "<i>کلیدها را از پیشخوان ووکامرس بسازید: WooCommerce ← Settings ← "
    "Advanced ← REST API، با دسترسی خواندن/نوشتن.</i>"
)
BTN_WOO_STORE = "🔗 اتصال فروشگاه"
BTN_WOO_STATUS = "🏷 وضعیت جدید"
BTN_WOO_NOTE = "🗒 یادداشت"
BTN_WOO_TEST = "🧪 آزمایش اتصال"
BTN_WOO_URL = "آدرس فروشگاه"
BTN_WOO_KEY = "کلید مصرف‌کننده"
BTN_WOO_SECRET = "رمز مصرف‌کننده"
WOO_URL_PROMPT = (
    "آدرس فروشگاه را بفرستید، مثلاً:\n<code>https://example.com</code>\n\n"
    "بدون <code>/wp-json</code> — خودش اضافه می‌شود."
)
WOO_KEY_PROMPT = "کلید مصرف‌کننده (Consumer key) را بفرستید. با <code>ck_</code> شروع می‌شود."
WOO_SECRET_PROMPT = "رمز مصرف‌کننده (Consumer secret) را بفرستید. با <code>cs_</code> شروع می‌شود."
WOO_STATUS_PROMPT = (
    "وضعیتی که سفارش در فروشگاه باید بگیرد را بفرستید.\n\n"
    "نمونه‌های رایج: <code>completed</code>، <code>processing</code>، "
    "<code>cancelled</code>، <code>refunded</code>، <code>failed</code>"
)
WOO_NOTE_PROMPT = (
    "متن یادداشتی که در فروشگاه ثبت شود را بفرستید.\n\n"
    "جایگزین‌ها: <code>{order}</code> شماره‌ی روزانه، <code>{number}</code> "
    "شماره‌ی فروشگاه، <code>{status}</code> وضعیت."
)
WOO_SAVED = "✅ ذخیره شد."
WOO_NOT_CONFIGURED = "— تنظیم نشده —"
WOO_SECRET_MASK = "••••••••"
WOO_TEST_RESULT = "{icon} نتیجه‌ی آزمایش اتصال: {detail}"
WOO_NEEDS_ORDER_NUMBER = (
    "برای فعال کردن ووکامرس، اول باید «شماره سفارش فروشگاه» در ⚙️ تنظیمات فعال "
    "باشد — بدون آن ربات نمی‌داند کدام سفارش فروشگاه را به‌روز کند."
)
WOO_NEEDS_STORE = "اول آدرس و کلیدهای فروشگاه را در «🔗 اتصال فروشگاه» تنظیم کنید."

# ---------------------------------------------------------------------------
# Store order number (parsed from the source message)
# ---------------------------------------------------------------------------
BTN_ORDER_NUMBER = "🔢 شماره سفارش فروشگاه"
ORDER_NUMBER_SCREEN = (
    "🔢 <b>شماره سفارش فروشگاه</b>\n\n"
    "ربات <b>خط آخر</b> هر سفارش را می‌خواند و شماره‌ی سفارش فروشگاه را از آن "
    "برمی‌دارد.\n\n"
    "وضعیت:\n{enabled}\n\n"
    "تعداد ارقام:\n{length}\n\n"
    "حذف پیام نامعتبر:\n{delete}\n\n"
    "پیام خطا:\n{message}\n\n"
    "<i>ارقام فارسی و عربی هم شناخته می‌شوند. اگر خط آخر شماره‌ی درست نداشته "
    "باشد، سفارش ساخته نمی‌شود و به گروه کاری نمی‌رود.</i>"
)
BTN_ORDER_NUMBER_LENGTH = "تعداد ارقام: {value}"
BTN_ORDER_NUMBER_DELETE = "حذف پیام نامعتبر: {value}"
BTN_ORDER_NUMBER_MESSAGE = "✏️ پیام خطا"
ORDER_NUMBER_LENGTH_PROMPT = (
    "تعداد ارقام شماره سفارش را بفرستید (بین {min} و {max}).\n\n"
    "الان {current} رقم است."
)
ORDER_NUMBER_LENGTH_INVALID = "❌ یک عدد بین {min} و {max} بفرستید."
ORDER_NUMBER_MESSAGE_PROMPT = (
    "پیامی که وقتی شماره سفارش غلط یا غایب است ریپلای شود را بفرستید.\n\n"
    "<code>{name}</code> با نام فرستنده جایگزین می‌شود.\n\n"
    "نمونه: <code>{name} عزیز، شماره سفارش قرار نگرفته یا اشتباه است.</code>"
)
ORDER_NUMBER_SAVED = "✅ ذخیره شد."

# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
REPORTS_INTRO = (
    "📈 <b>گزارش‌ها</b>\n\n"
    "نرخ‌ها فقط روی سفارش‌های نهایی‌شده حساب می‌شوند:\n"
    "<code>نهایی‌شده = موفق + ناموفق</code>\n"
    "<code>نرخ موفقیت = موفق ÷ نهایی‌شده × ۱۰۰</code>\n\n"
    "سفارش‌های در انتظار و دارای تداخل جداگانه گزارش می‌شوند."
)
BTN_TODAY = "امروز"
BTN_YESTERDAY = "دیروز"
BTN_LAST_7 = "۷ روز اخیر"
BTN_LAST_30 = "۳۰ روز اخیر"
BTN_CUSTOM_RANGE = "📅 بازه‌ی دلخواه"
BTN_BY_OPERATOR = "👤 بر اساس اپراتور"
BTN_BY_SOURCE = "📥 بر اساس مبدأ"
BTN_BY_WORK_GROUP = "👥 بر اساس گروه کاری"

PERIOD_TODAY = "امروز"
PERIOD_YESTERDAY = "دیروز"
PERIOD_LAST_DAYS = "{days} روز اخیر"

ORDER_REPORT = (
    "📊 <b>گزارش {period}</b>\n"
    "<i>{first} تا {last}</i>\n\n"
    "کل: <b>{total}</b>\n\n"
    "✅ موفق: <b>{success}</b>\n"
    "❌ ناموفق: <b>{failed}</b>\n"
    "⏳ در انتظار: <b>{pending}</b>\n"
    "⚠️ تداخل: <b>{conflict}</b>\n\n"
    "نهایی‌شده (موفق + ناموفق): <b>{completed}</b>\n"
    "نرخ موفقیت: <b>{success_rate}٪</b>\n"
    "نرخ ناموفقی: <b>{failure_rate}٪</b>\n\n"
    "میانگین زمان تکمیل: {average}"
)
OPERATOR_REPORT_TITLE = "👤 <b>گزارش اپراتورها — {period}</b>"
OPERATOR_REPORT_EMPTY = "در این بازه هیچ سفارش نهایی‌شده‌ای وجود ندارد."
OPERATOR_REPORT_ROW = (
    "<b>{name}</b>\n"
    "  سفارش‌های رسیدگی‌شده: {total}\n"
    "  موفق: {success}\n"
    "  ناموفق: {failed}\n"
    "  میانگین زمان تکمیل: {average}"
)
BY_SOURCE_TITLE = "📥 <b>بر اساس مبدأ — {period}</b>"
BY_WORK_GROUP_TITLE = "👥 <b>بر اساس گروه کاری — {period}</b>"
BY_GROUP_ROW = (
    "<b>{name}</b>\n"
    "  کل {total} · ✅ {success} · ❌ {failed} · ⏳ {pending} · ⚠️ {conflict}\n"
    "  نرخ موفقیت {rate}٪"
)
NO_SOURCE_CONFIGURED = "هیچ کانال مبدأیی تعریف نشده."
NO_WORK_GROUP_CONFIGURED = "هیچ گروه کاری‌ای تعریف نشده."
CUSTOM_RANGE_PROMPT = (
    "📅 یک بازه‌ی تاریخ به این شکل بفرستید:\n"
    "<code>YYYY-MM-DD YYYY-MM-DD</code>\n"
    "یا یک تاریخ تکی: <code>YYYY-MM-DD</code>\n\n"
    "تاریخ‌ها بر اساس منطقه‌ی زمانی سیستم تفسیر می‌شوند."
)
CUSTOM_RANGE_INVALID = (
    "❌ از قالب <code>YYYY-MM-DD</code> یا دو تاریخ با همین قالب استفاده کنید."
)

# ---------------------------------------------------------------------------
# Dashboard / system status
# ---------------------------------------------------------------------------
DASHBOARD = (
    "📊 <b>داشبورد</b>\n\n"
    "📦 سفارش‌های امروز: <b>{total}</b>\n"
    "✅ موفق: <b>{success}</b>\n"
    "❌ ناموفق: <b>{failed}</b>\n"
    "⏳ در انتظار: <b>{pending}</b>\n"
    "⚠️ تداخل: <b>{conflict}</b>\n\n"
    "نرخ موفقیت: <b>{success_rate}٪</b>\n"
    "نرخ ناموفقی: <b>{failure_rate}٪</b>\n\n"
    "کانال‌های مبدأ فعال: {sources}\n"
    "گروه‌های کاری فعال: {work_groups}\n"
    "اپراتورهای فعال: {operators}\n"
    "وضعیت ربات: {bot}\n"
    "وضعیت پایگاه داده: {database}"
)
SYSTEM_STATUS = (
    "🩺 <b>وضعیت سیستم</b>\n\n"
    "ربات: {bot}\n"
    "پایگاه داده: {database}\n\n"
    "کانال‌های مبدأ: {sources}\n"
    "گروه‌های کاری: {work_groups}\n"
    "اپراتورها: {operators}\n\n"
    "سفارش‌های در انتظار: {pending}\n"
    "سفارش‌های دارای تداخل: {conflict}\n"
    "ارسال‌های ناموفق: {failed_dispatches}\n"
    "تأییدهای ناموفق: {failed_acks}\n\n"
    "مدت فعالیت: {uptime}"
)
ONLINE = "✅ آنلاین"
OFFLINE = "❌ آفلاین"
CONNECTED = "✅ متصل"
DB_ERROR = "❌ خطا"

# ---------------------------------------------------------------------------
# Order search and manual override
# ---------------------------------------------------------------------------
FIND_ORDER_PROMPT = (
    "🔎 <b>جستجوی سفارش</b>\n\n"
    "شماره‌ی سفارش را بفرستید (مثلاً <code>153</code> یا <code>order153</code>) "
    "تا در سفارش‌های امروز جستجو شود.\n\n"
    "برای روز دیگر: <code>YYYY-MM-DD 153</code>\n\n"
    "هرجای ربات هم می‌توانید <code>/order 153</code> بزنید."
)
ORDER_QUERY_INVALID = "❌ نتوانستم شماره‌ی سفارشی از این متن بخوانم."
ORDER_NOT_FOUND = "سفارشی با شماره‌ی <b>{number}</b> در تاریخ <b>{day}</b> پیدا نشد."
ORDER_MULTIPLE = "{count} سفارش با شماره‌ی <b>{number}</b> در {day} پیدا شد:"
ORDER_MULTIPLE_ROW = (
    "• {display} — {status}\n  (دامنه {scope}، ایجاد {created})\n  /orderid_{id}"
)
ORDER_MISSING = "سفارش پیدا نشد."

ORDER_DETAIL = (
    "🔎 <b>سفارش {display}</b>\n\n"
    "شناسه‌ی یکتا: <code>{uuid}</code>\n"
    "تاریخ کاری: {business_date}\n"
    "شماره‌ی روزانه: {daily_number} (دامنه {scope})\n"
    "مبدأ: {source} / پیام {source_message}\n"
    "آلبوم: {album}\n\n"
    "وضعیت: <b>{status}</b>\n"
    "ایجاد: {created}\n"
    "تکمیل: {completed}\n"
    "تکمیل‌کننده: {completed_by}\n"
    "محرک: {trigger} (گفتگو {trigger_chat}، پیام {trigger_message})\n"
    "دلیل: {reason}\n\n"
    "ارسال نتیجه: <b>{dispatch_state}</b>\n{dispatches}\n\n"
    "واکنش تأیید: <b>{ack_status}</b>\n"
    "  واکنش: {ack_reaction}\n"
    "  هدف: گفتگو {ack_chat} / پیام {ack_message}\n"
    "  زمان اعمال: {ack_applied}\n"
    "  تعداد تلاش: {ack_attempts}\n"
    "  خطا: {ack_error}\n\n"
    "<b>تحویل به گروه‌های کاری</b>\n{deliveries}\n\n"
    "<b>سیگنال‌ها</b>\n{signals}"
)
DELIVERY_ROW = "  • گفتگو {chat}: {status} [{messages}]"
DELIVERY_NO_MESSAGE = "بدون پیام"
DISPATCH_ROW = "  • {chat}: {status}"

BTN_MARK_SUCCESS = "✅ ثبت به‌عنوان موفق"
BTN_MARK_FAILED = "❌ ثبت به‌عنوان ناموفق"
BTN_MARK_PENDING = "⏳ بازگرداندن به در انتظار"
BTN_RETRY_DISPATCH = "🔁 تلاش مجدد ارسال"
BTN_ORDER_AUDIT = "📝 تاریخچه‌ی رویدادها"
BTN_BACK_TO_ORDER = "⬅️ بازگشت به سفارش"

OVERRIDE_PROMPT = (
    "این سفارش روی <b>{status}</b> تنظیم شود.\n\n"
    "انتخاب کنید چه کار دیگری انجام شود:\n\n"
    "• <b>ارسال + واکنش تأیید</b> — به مقصد نتایج فرستاده شود و پس از موفقیت، "
    "واکنش تأیید گذاشته شود.\n"
    "• <b>فقط ارسال</b> — نتیجه فرستاده شود ولی واکنشی گذاشته نشود.\n"
    "• <b>فقط تغییر وضعیت</b> — فقط وضعیت عوض شود و هیچ کار دیگری انجام نشود.\n\n"
    "مقصدهایی که قبلاً ارسال شده‌اند هرگز دوباره ارسال نمی‌شوند."
)
BTN_OVERRIDE_FULL = "ارسال + واکنش تأیید"
BTN_OVERRIDE_DISPATCH = "فقط ارسال"
BTN_OVERRIDE_STATUS = "فقط تغییر وضعیت"

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
SETTINGS_SCREEN = (
    "⚙️ <b>تنظیمات</b>\n\n"
    "<b>دامنه‌ی شمارنده:</b> {scope}\n"
    "<i>سراسری — یک دنباله‌ی روزانه‌ی مشترک برای همه‌ی مبدأها.\n"
    "به‌ازای هر مبدأ — هر کانال مبدأ شماره‌گذاری مستقل خودش را دارد.</i>\n\n"
    "<b>پیشوند سفارش:</b> {prefix}\n"
    "<b>قالب شماره:</b> <code>{format}</code>\n"
    "پیش‌نمایش: <b>{preview}</b>\n\n"
    "<i>شمارنده در اولین سفارش هر روز کاری به ۱ برمی‌گردد — بدون هیچ زمان‌بندی، "
    "پس حتی اگر ربات دقیقاً نیمه‌شب خاموش باشد هم درست کار می‌کند.</i>"
)
BTN_COUNTER_SCOPE = "دامنه‌ی شمارنده: {value}"
BTN_ORDER_PREFIX = "پیشوند سفارش: {value}"
BTN_NUMBER_FORMAT = "قالب شماره: {value}"
BTN_NOTIFICATIONS = "اعلان‌های مدیر: {value}"
BTN_ADMINS = "👮 مدیران"
NOTIFICATIONS_ON = "روشن"
NOTIFICATIONS_OFF = "خاموش"

COUNTER_SCOPE_NAMES = {"GLOBAL": "سراسری", "PER_SOURCE": "به‌ازای هر مبدأ"}
COUNTER_SCOPE_PROMPT = (
    "دامنه‌ی شمارنده را انتخاب کنید.\n\n"
    "تغییر آن شماره‌ی سفارش‌های قبلی را عوض نمی‌کند؛ از سفارش بعدی اعمال می‌شود."
)
PREFIX_PROMPT = (
    "پیشوند جدید سفارش را بفرستید.\n\n"
    "پیش‌فرض <code>order</code> است که <code>order125</code> تولید می‌کند."
)
FORMAT_PROMPT = (
    "قالب شماره را بفرستید.\n\n"
    "جایگزین‌های موجود: <code>{prefix}</code> و <code>{number}</code>\n\n"
    "نمونه‌ها:\n"
    "<code>{prefix}{number}</code> ← order125\n"
    "<code>ORD-{number}</code> ← ORD-125\n"
    "<code>{prefix}-{number}</code> ← order-125"
)
SETTING_EMPTY = "❌ مقدار خالی است."
FORMAT_INVALID = (
    "❌ قالب نامعتبر است. فقط از <code>{prefix}</code> و <code>{number}</code> "
    "استفاده کنید."
)
FORMAT_NEEDS_NUMBER = "❌ قالب باید شامل <code>{number}</code> باشد."
SETTING_SAVED = "✅ ذخیره شد. سفارش‌های جدید به این شکل خواهند بود: <b>{preview}</b>"

# ---------------------------------------------------------------------------
# Admins
# ---------------------------------------------------------------------------
ADMINS_HEADER = (
    "👮 <b>مدیران</b>\n\n"
    "👑 مدیر ارشد — دسترسی کامل، شامل همین صفحه.\n"
    "👮 مدیر — همه‌چیز به‌جز مدیریت مدیران.\n"
    "🔒 — تعریف‌شده در <code>SUPERADMIN_IDS</code>؛ از اینجا قابل تغییر نیست.\n"
)
ROLE_SUPER_ADMIN = "مدیر ارشد"
ROLE_ADMIN = "مدیر"
BTN_ADD_ADMIN = "➕ افزودن مدیر"
BTN_PROMOTE = "⬆️ ارتقا به مدیر ارشد"
BTN_DEMOTE = "⬇️ تنزل به مدیر"
BTN_REMOVE_ADMIN = "🗑 حذف"
ADMIN_DETAIL = (
    "{badge} <b>{name}</b>\n\n"
    "شناسه‌ی کاربر: <code>{user_id}</code>\n"
    "نقش: {role}\n"
    "وضعیت: {status}\n"
    "منبع: {source}"
)
ADMIN_SOURCE_ENV = "متغیر محیطی (قفل)"
ADMIN_SOURCE_PANEL = "پنل مدیریت"
ADD_ADMIN_PROMPT = (
    "شناسه‌ی عددی تلگرام کسی که می‌خواهید دسترسی مدیریت بگیرد را بفرستید.\n\n"
    "خودش می‌تواند با فرستادن <code>/id</code> به این ربات آن را ببیند.\n"
    "کاربر جدید به‌عنوان 👮 مدیر ساخته می‌شود؛ در صورت نیاز بعداً ارتقایش دهید."
)
ADMIN_ADDED = "✅ <code>{user_id}</code> حالا می‌تواند پنل مدیریت را باز کند."
ADMIN_LOCKED_ROLE = (
    "این مدیر ارشد از SUPERADMIN_IDS می‌آید و از اینجا قابل تغییر نیست."
)
ADMIN_LOCKED_DISABLE = (
    "این مدیر ارشد از SUPERADMIN_IDS می‌آید و از اینجا قابل غیرفعال کردن نیست."
)
ADMIN_LOCKED_DELETE = "مدیران ارشدِ تعریف‌شده در SUPERADMIN_IDS قابل حذف نیستند."
SUPER_ADMIN_ONLY = "فقط مدیر ارشد می‌تواند حساب‌های مدیریت را تغییر دهد."

# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
AUDIT_TITLE = "📝 <b>رویدادها</b> (از شماره‌ی {start})"
AUDIT_ORDER_TITLE = "📝 <b>تاریخچه‌ی رویدادهای سفارش #{order_id}</b>"
AUDIT_EMPTY = "رویدادی ثبت نشده."
AUDIT_ORDER_REF = " · سفارش #{order_id}"
BTN_NEWER = "⬅️ جدیدتر"
BTN_OLDER = "قدیمی‌تر ➡️"

AUDIT_EVENT_LABELS: dict[str, str] = {
    AuditEvent.ORDER_CREATED: "سفارش ایجاد شد",
    AuditEvent.ORDER_ROUTED: "سفارش به گروه کاری رفت",
    AuditEvent.ORDER_ROUTE_FAILED: "ارسال به گروه کاری ناموفق",
    AuditEvent.OPERATOR_SIGNAL_RECEIVED: "سیگنال اپراتور دریافت شد",
    AuditEvent.SUCCESS_RULE_MATCHED: "قانون موفقیت برقرار شد",
    AuditEvent.FAILURE_RULE_MATCHED: "قانون ناموفقی برقرار شد",
    AuditEvent.STATUS_CHANGED: "وضعیت تغییر کرد",
    AuditEvent.CONFLICT_DETECTED: "تداخل تشخیص داده شد",
    AuditEvent.RESULT_DISPATCH_ATTEMPTED: "تلاش برای ارسال نتیجه",
    AuditEvent.RESULT_DISPATCH_SUCCEEDED: "نتیجه ارسال شد",
    AuditEvent.RESULT_DISPATCH_FAILED: "ارسال نتیجه ناموفق",
    AuditEvent.ACKNOWLEDGEMENT_ATTEMPTED: "تلاش برای واکنش تأیید",
    AuditEvent.ACKNOWLEDGEMENT_APPLIED: "واکنش تأیید گذاشته شد",
    AuditEvent.ACKNOWLEDGEMENT_FAILED: "واکنش تأیید ناموفق",
    AuditEvent.ACKNOWLEDGEMENT_SKIPPED: "واکنش تأیید انجام نشد",
    AuditEvent.MANUAL_OVERRIDE: "تغییر دستی توسط مدیر",
    AuditEvent.RULE_CHANGED: "قانون تغییر کرد",
    AuditEvent.REACTION_CONFIGURATION_CHANGED: "تنظیمات واکنش تغییر کرد",
    AuditEvent.CONFIGURATION_CHANGED: "پیکربندی تغییر کرد",
    AuditEvent.RECOVERY_PERFORMED: "بازیابی پس از راه‌اندازی",
}

# ---------------------------------------------------------------------------
# Notifications sent to admins
# ---------------------------------------------------------------------------
NOTIFY_DISPATCH_FAILED = (
    "⚠️ سفارش {number}\n\n"
    "ارسال نتیجه ناموفق بود.\n\n"
    "مقصد:\n{chat_id}\n\n"
    "دلیل:\n{reason}"
)
NOTIFY_ACK_FAILED = (
    "⚠️ سفارش {number}\n\n"
    "نتیجه با موفقیت ارسال شد، ولی گذاشتن واکنش تأیید ناموفق بود.\n\n"
    "دلیل:\n{reason}"
)
NOTIFY_CONFLICT = (
    "⚠️ سفارش {number}\n\n"
    "قوانین موفق و ناموفق هم‌زمان برقرار شدند.\n"
    "سفارش در وضعیت «تداخل» متوقف است: چیزی ارسال نشد و واکنشی گذاشته نشد.\n\n"
    "از مسیر 🔎 جستجوی سفارش آن را حل کنید."
)
NOTIFY_STORE_FAILED = (
    "⚠️ سفارش {number}\n\n"
    "به‌روزرسانی وضعیت در فروشگاه ناموفق بود.\n\n"
    "شماره سفارش فروشگاه:\n{order_number}\n\n"
    "دلیل:\n{reason}"
)
NOTIFY_ROUTE_FAILED = (
    "⚠️ سفارش {number}\n\nارسال به گروه کاری ناموفق بود.\n\nدلیل:\n{reason}"
)

STARTUP_TITLE = "✅ <b>ربات با موفقیت راه‌اندازی شد</b>"
STARTUP_BOT = "ربات: @{username} (<code>{bot_id}</code>)"
STARTUP_BOT_NO_USERNAME = "شناسه‌ی ربات: <code>{bot_id}</code>"
STARTUP_DATABASE = "پایگاه داده: متصل"
STARTUP_TIME = "زمان محلی: {time} ({timezone})"
STARTUP_CONFIG_TITLE = "<b>پیکربندی</b>"
STARTUP_SOURCES = "{mark} کانال‌های مبدأ: {count}"
STARTUP_WORK_GROUPS = "{mark} گروه‌های کاری: {count}"
STARTUP_ROUTES = "{mark} مسیرها: {count}"
STARTUP_OPERATORS = "{mark} اپراتورها: {count}"
STARTUP_SUCCESS_TARGETS = "{mark} مقصد سفارش‌های موفق: {count}"
STARTUP_FAILURE_TARGETS = "{mark} مقصد سفارش‌های ناموفق: {count}"
STARTUP_PENDING = "⏳ سفارش‌های در انتظار از قبل: {count}"
STARTUP_NOT_READY = "⚠️ <b>هنوز آماده نیست.</b> تا این موارد اضافه نشوند سفارشی جریان پیدا نمی‌کند: {missing}."
STARTUP_NOT_READY_HINT = "برای تنظیم، <code>/start</code> را بزنید و پنل مدیریت را باز کنید."
STARTUP_READY = "همه‌چیزِ لازم تنظیم شده است. برای مدیریت <code>/start</code> را بزنید."
STARTUP_MISSING_SOURCE = "یک کانال مبدأ"
STARTUP_MISSING_WORK_GROUP = "یک گروه کاری"
STARTUP_MISSING_ROUTE = "یک مسیر بین آن دو"
STARTUP_MISSING_OPERATOR = "یک اپراتور"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def status_name(status: OrderStatus | str) -> str:
    """Persian name for an order status."""
    try:
        return STATUS_NAMES_FA[OrderStatus(status)]
    except (ValueError, KeyError):
        return str(status)


def toggle_text(enabled: bool) -> str:
    return STATUS_ENABLED if enabled else STATUS_DISABLED


def toggle_button(enabled: bool) -> str:
    """Label for the button that flips the current state."""
    return BTN_DISABLE if enabled else BTN_ENABLE


def yes_no(value: bool) -> str:
    return YES if value else NO


def audit_event_label(event: str) -> str:
    return AUDIT_EVENT_LABELS.get(event, event)


#: Persian digits make numbers read naturally inside right-to-left sentences.
_LATIN_TO_PERSIAN = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_digits(value: object) -> str:
    return str(value).translate(_LATIN_TO_PERSIAN)
