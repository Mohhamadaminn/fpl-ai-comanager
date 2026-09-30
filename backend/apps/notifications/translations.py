TRANSLATIONS = {
    # --- Generic / shared ---
    "no_team_linked": {
        "en": "No team linked yet. Use /setteamid first.",
        "fa": "هنوز تیمی متصل نکرده‌ای. اول از /setteamid استفاده کن.",
    },
    "no_current_gameweek": {
        "en": "Couldn't find the current gameweek.",
        "fa": "گیم‌ویک جاری پیدا نشد.",
    },
    "no_finished_gameweek": {
        "en": "No finished gameweek to report on yet.",
        "fa": "هنوز گیم‌ویک تمام‌شده‌ای برای گزارش وجود ندارد.",
    },
    "rate_limited": {
        "en": "Slow down a bit — try again in a few seconds.",
        "fa": "یکم آروم‌تر — چند ثانیه دیگه دوباره امتحان کن.",
    },
    "rate_limited_global": {
        "en": "Lots of requests right now — please try again in a minute.",
        "fa": "الان درخواست زیاده — یه دقیقه دیگه دوباره امتحان کن.",
    },
    "welcome": {
        "en": "Hey! I'm your FPL AI Co-Manager. Use /setteamid to link your team, or /prediction to get this gameweek's suggestion.",
        "fa": "سلام! من دستیار هوش مصنوعی فانتزی پریمیرلیگ توام. از /setteamید برای اتصال تیمت استفاده کن یا /prediction برای پیشنهاد این گیم‌ویک.",
    },

    # --- /prediction ---
    "prediction_pending": {
        "en": "Got it — I'll send your prediction shortly.",
        "fa": "باشه — پیش‌بینی رو به زودی برات می‌فرستم.",
    },
    "prediction_failed": {
        "en": "Sorry, I couldn't generate your prediction right now. Please try again in a few minutes.",
        "fa": "متاسفم، الان نتونستم پیش‌بینی رو تولید کنم. چند دقیقه دیگه دوباره امتحان کن.",
    },

    # --- /performance ---
    "crunching_numbers": {
        "en": "Crunching the numbers, one moment...",
        "fa": "دارم آمارها رو بررسی می‌کنم، یه لحظه...",
    },
    "performance_failed": {
        "en": "Sorry, I couldn't generate your performance report right now. Please try again in a few minutes.",
        "fa": "متاسفم، الان نتونستم گزارش عملکرد رو تولید کنم. چند دقیقه دیگه دوباره امتحان کن.",
    },
    "overperformed": {"en": "Overperformed", "fa": "بهتر از حد انتظار"},
    "underperformed": {"en": "Underperformed", "fa": "بدتر از حد انتظار"},
    "verdict_over": {"en": "Overperform", "fa": "فراتر از انتظار"},
    "verdict_under": {"en": "Underperform", "fa": "کمتر از انتظار"},
    "on_bench": {"en": "On the bench", "fa": "روی نیمکت"},
    "expected": {"en": "expected", "fa": "انتظار می‌رفت"},
    "gw_title": {"en": "Who Beat Their Numbers?", "fa": "چه کسی از انتظارات فراتر رفت؟"},
    "nobody_stood_out": {
        "en": "Nobody in your XI stood out this week — everyone performed close to expectations.",
        "fa": "این هفته کسی در ترکیب اصلی متمایز نبود — همه نزدیک به حد انتظار بازی کردن.",
    },
    "played_as_expected": {"en": "played about as expected", "fa": "طبق انتظار بازی کردن"},
    "limited_minutes": {"en": "didn't play enough minutes", "fa": "دقایق کافی بازی نکردن"},

    # --- /help ---
    "help_text": {
        "en": (
            "🤖 *FPL AI Co-Manager — Commands*\n\n"
            "/start — Get started and enable reminders\n"
            "/fixtures — Show your squad's teams' next fixtures\n"
            "/setteamid — Link your FPL team (paste your team link)\n"
            "/myteamid — Show your linked team ID\n"
            "/myteam — See your squad's live/final points for this gameweek\n"
            "/prediction — Get this gameweek's AI suggestion\n"
            "/differentials — Get 3 low-ownership picks worth watching\n"
            "/performance — See your underperform and overperform players\n"
            "/status — Show team ID, gameweek, deadline, free transfers, reminder status\n"
            "/language — Change language\n"
            "/help — Show this list"
        ),
        "fa": (
            "🤖 *دستیار هوش مصنوعی فانتزی — دستورات*\n\n"
            "/start — شروع و فعال‌سازی یادآوری‌ها\n"
            "/fixtures — بازی‌های بعدی تیم‌های بازیکنانت\n"
            "/setteamid — اتصال تیم فانتزی (لینک تیمت رو بفرست)\n"
            "/myteamid — نمایش شناسه تیم متصل‌شده\n"
            "/myteam — امتیاز زنده/نهایی تیمت در این گیم‌ویک\n"
            "/prediction — پیشنهاد هوش مصنوعی برای این گیم‌ویک\n"
            "/differentials — سه بازیکن کم‌انتخاب برای دیده‌بانی\n"
            "/performance — بازیکنانی که بهتر یا بدتر از انتظار بازی کردن\n"
            "/status — شناسه تیم، گیم‌ویک، ددلاین، ترانسفرهای آزاد و وضعیت یادآوری\n"
            "/language — تغییر زبان\n"
            "/help — نمایش همین لیست"
        ),
    },

    # --- /myteam ---
    "your_team_title": {"en": "YOUR TEAM", "fa": "تیم تو"},
    "total": {"en": "Total", "fa": "مجموع"},
    "pos_gkp": {"en": "GOALKEEPER", "fa": "دروازه‌بان"},
    "pos_def": {"en": "DEFENDERS", "fa": "مدافعان"},
    "pos_mid": {"en": "MIDFIELDERS", "fa": "هافبک‌ها"},
    "pos_fwd": {"en": "FORWARDS", "fa": "مهاجمان"},
    "bench": {"en": "BENCH", "fa": "نیمکت"},
    "live": {"en": "LIVE", "fa": "زنده"},

    # --- /status ---
    "status_title": {"en": "Status", "fa": "وضعیت"},
    "team_id_label": {"en": "Team ID", "fa": "شناسه تیم"},
    "gameweek_label": {"en": "Gameweek", "fa": "گیم‌ویک"},
    "deadline_label": {"en": "Deadline", "fa": "ددلاین"},
    "free_transfers_label": {"en": "Free transfers", "fa": "ترانسفرهای آزاد"},
    "reminders_label": {"en": "Reminders", "fa": "یادآوری‌ها"},
    "reminders_enabled": {"en": "✅ Enabled", "fa": "✅ فعال"},
    "reminders_disabled": {"en": "❌ Not set (send /start to enable)", "fa": "❌ فعال نیست (برای فعال‌سازی /start بزن)"},
    "unknown": {"en": "Unknown", "fa": "نامشخص"},
    "no_team_linked_status": {
        "en": "No team linked yet. Use /setteamid to get started.",
        "fa": "هنوز تیمی متصل نکرده‌ای. برای شروع از /setteamid استفاده کن.",
    },

    # --- /fixtures ---
    "fixtures_title": {"en": "Your Squad's Upcoming Fixtures", "fa": "بازی‌های بعدی تیمت"},
    "no_upcoming_fixture": {"en": "no upcoming fixture found", "fa": "بازی آینده‌ای پیدا نشد"},

    # --- /setteamid, /myteamid ---
    "team_saved": {"en": "Your team has been saved!", "fa": "تیمت ذخیره شد!"},
    "team_id_not_found": {
        "en": "Couldn't find a team ID in that. Please paste your team link again.",
        "fa": "شناسه تیم توی پیامت پیدا نشد. لطفا لینک تیمت رو دوباره بفرست.",
    },
    "team_id_taken": {
        "en": "That team is already linked to another chat.",
        "fa": "این تیم قبلا به یک چت دیگه متصل شده.",
    },
    "setteamid_prompt": {
        "en": "Go to your Fantasy team, click on the Points tab, and paste the URL link here.",
        "fa": "به تیم فانتزیت برو، روی تب Points بزن و لینک صفحه رو اینجا بفرست.",
    },
    "cancelled": {"en": "Cancelled.", "fa": "لغو شد."},
    "your_team_id": {"en": "Your team ID", "fa": "شناسه تیم تو"},
    "no_team_id_set": {
        "en": "No team ID set yet. Use /setteamid.",
        "fa": "هنوز شناسه تیمی ثبت نشده. از /setteamid استفاده کن.",
    },


    # --- /differentials ---
    "differentials_searching": {
        "en": "Looking for differentials, one moment...",
        "fa": "دارم دنبال بازیکنای کم‌انتخاب می‌گردم، یه لحظه...",
    },
    "differentials_title": {"en": "Differentials", "fa": "بازیکنان کم‌انتخاب"},
    "no_differentials": {
        "en": "No strong low-ownership picks found this week.",
        "fa": "این هفته گزینه قوی کم‌انتخاب پیدا نشد.",
    },
    "owned": {"en": "owned", "fa": "انتخاب‌شده"},

    # --- deadline reminder ---
    "deadline_warning": {"en": "deadline in less than 24h!", "fa": "کمتر از ۲۴ ساعت تا ددلاین!"},
    "captain_label": {"en": "Captain", "fa": "کاپیتان"},

    # --- weekly squad health ---
    "squad_check_title": {"en": "Weekly Squad Check", "fa": "بررسی هفتگی تیم"},
    "no_concerns": {"en": "No concerns — your squad looks healthy.", "fa": "نگرانی خاصی نیست — تیمت وضعیت خوبی داره."},


    # --- indicators (differentials) ---
    "fixture_unknown": {"en": "Fixture: unknown", "fa": "بازی: نامشخص"},
    "fixture_very_good": {"en": "Fixture: very good", "fa": "بازی: خیلی خوب"},
    "fixture_average": {"en": "Fixture: average", "fa": "بازی: متوسط"},
    "fixture_tough": {"en": "Fixture: tough", "fa": "بازی: سخت"},
    "xgi_no_data": {"en": "xGI: not enough data yet", "fa": "xGI: داده کافی نیست"},
    "xgi_up": {"en": "xGI: upward", "fa": "xGI: صعودی"},
    "xgi_down": {"en": "xGI: downward", "fa": "xGI: نزولی"},
    "xgi_stable": {"en": "xGI: stable", "fa": "xGI: پایدار"},
    "low_confidence_note": {"en": "(early season, low confidence)", "fa": "(اوایل فصل، اطمینان پایین)"},
    "minutes_reliable": {"en": "Minutes: reliable", "fa": "دقایق بازی: قابل‌اعتماد"},
    "minutes_rotation_risk": {"en": "Minutes: rotation risk", "fa": "دقایق بازی: احتمال تعویض"},
    "minutes_bench_risk": {"en": "Minutes: bench risk", "fa": "دقایق بازی: احتمال نیمکت‌نشینی"},
    "form_good": {"en": "Form: good", "fa": "فرم: خوب"},
    "form_average": {"en": "Form: average", "fa": "فرم: متوسط"},
    "form_poor": {"en": "Form: poor", "fa": "فرم: ضعیف"},
    "ownership_low": {"en": "Ownership: low", "fa": "درصد انتخاب: پایین"},
    "ownership_borderline": {"en": "Ownership: borderline", "fa": "درصد انتخاب: مرزی"},

    # --- squad health flags ---
    "form_declining": {"en": "form declining (xGI last 5 GWs: {xgi})", "fa": "افت فرم (xGI پنج بازی اخیر: {xgi})"},
    "status_injured": {"en": "injured", "fa": "مصدوم"},
    "status_doubtful": {"en": "doubtful", "fa": "مشکوک به بازی"},
    "status_suspended": {"en": "suspended", "fa": "محروم"},
    "status_unavailable": {"en": "unavailable", "fa": "غایب"},
    "tough_fixtures_ahead": {"en": "tough fixtures ahead (avg FDR {fdr})", "fa": "بازی‌های سخت پیش رو (میانگین FDR {fdr})"},
    
}


def t(key: str, lang: str) -> str:
    entry = TRANSLATIONS.get(key)
    if not entry:
        return key
    return entry.get(lang, entry["en"])