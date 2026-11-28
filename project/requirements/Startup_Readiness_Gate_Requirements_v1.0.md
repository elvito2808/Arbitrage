# Startup Readiness Gate Requirements — SRG v1.0

**وضعیت:** پیش‌نویس آماده پیاده‌سازی و بازبینی مالک؛ سیاست retry تأیید شده است.
**مخزن:** elvito2808/Arbitrage — شاخه main
**دامنه:** SIMULATION ONLY، سه تعریف Worker و حداکثر یک Worker فعال، Hedera 295، SaucerSwap V1 و KuCoin HBAR-USDT.

## ۱. هدف و مبنا
این سند مکمل Bot_Lifecycle_Management_Requirements_v1.0 و Execution_and_Balance_Management_Requirements_v1.1 است. در runtime فعلی تحلیل بدون Gas معتبر مجاز است؛ این سند عمداً آن رفتار را برای شروع نشست تغییر می‌دهد. هیچ مجوزی برای LIVE، deployment، ارسال سفارش، Approval یا تراکنش واقعی صادر نمی‌شود. منطق مالی، محدودیت‌های ریسک، قواعد recovery و دفتر مالی نباید تضعیف شوند.

## ۲. الزامات اجرایی
| ID | الزام قابل آزمون |
|---|---|
| SRG-001 | Start، resume و recover مجاز ابتدا مالکیت انحصاری runtime و وضعیت نشست، latch اضطراری و deadline را بررسی کنند. نشست متوقف یا اضطراری خودکار فعال نشود. |
| SRG-002 | قبل از شروع حلقه تحلیل، وضعیت آمادگی STARTING ثبت شود؛ وضعیت intent نشست از readiness عملیاتی جدا باشد. RUNNING عملیاتی فقط بعد از PASS همه شروط ضروری اعلام شود. |
| SRG-003 | تنظیمات معتبر، حالت simulation، حجم 1000 HBAR، ظرفیت 3 و سقف فعال 1، پارامترهای ریسک، ساعت و دسترسی خصوصی SQLite بررسی شوند. مقادیر از تنظیمات اعمال‌شده خوانده شوند. |
| SRG-004 | سلامت schema و قابلیت خواندن/نوشتن پایگاه، سازگاری ledger و رزروها، موجودی غیرمنفی و تطبیق وضعیت مالی بررسی شود. موجودی اولیه فقط در init جدید seed شود؛ restart نباید آن را بازنشانی کند. |
| SRG-005 | Recovery قبل از تحلیل انجام شود. فقط ادامه عملیات مجازی قبلاً پذیرفته‌شده طبق قواعد موجود مجاز است؛ هیچ پذیرش جدیدی در pre-flight انجام نشود. manual_review یا مغایرت حل‌نشده مانع آمادگی است؛ رزروهای نامطمئن حفظ شوند. |
| SRG-006 | Hedera RPC، chain ID 295، Mirror، pool و router، WHBAR/USDC و mapping به HBAR-USDT با پاسخ معتبر بررسی شوند. در صورت تغییر RPC اعتبار شبکه مجدداً بررسی شود. |
| SRG-007 | KuCoin metadata و عمق معتبر برای 1000 HBAR در هر دو سمت بررسی شوند. WS ترجیحی است؛ REST معتبر جایگزین مجاز است. نبود WS به تنهایی مانع شروع نیست. وضعیت هر مسیر جداگانه ثبت شود؛ مسیر تست‌نشده UNKNOWN است. |
| SRG-008 | یک نمونه تازه DEX سپس KuCoin تحت بودجه خواندن مشترک موجود (10s) دریافت شود؛ freshness و skew و provenance و عمق طبق کد کنترل شوند. نمونه pre-flight نباید analysis cycle یا trade بسازد یا شناسه snapshot مصرف کند. |
| SRG-009 | hedera_estimate_sender عمومی معتبر و شواهد read-only Gas برای هر دو مسیر BUY/SELL الزامی است. نبود sender خطای تنظیمات بحرانی است؛ هیچ کلید خصوصی یا Approval واقعی درخواست نشود. Gas باید به snapshot و مسیر مربوط متصل و معتبر باشد. |
| SRG-010 | موجودی واقعی دفتر مجازی و available/reserved برای حجم ثابت، خرید تحت stress و بودجه Gas طبق قواعد فعلی بررسی شود. کمبود پایدار منابع مانع شروع است. مثبت بودن سود بازار شرط آمادگی نیست. |
| SRG-011 | خطاهای موقت (timeout، 429، قطع اتصال، داده stale، عمق ناکافی موقت، تغییر head) موجب WAITING_RETRY و عدم شروع چرخه شوند. خطاهای بحرانی (config نامعتبر، sender غایب، chain/pool/token mismatch قطعی، فساد ledger، latch اضطراری، manual_review، عدم امکان پایدار estimation) موجب STARTUP_BLOCKED و خروج غیرصفر شوند. علت نامشخص fail closed و پس از سقف retry نیازمند بررسی دستی باشد. |
| SRG-012 | retry با backoff محدود، jitter و رعایت Retry-After و بودجه provider انجام شود. پیشنهاد پیش‌فرض برای تأیید: 3 تلاش مجدد پس از تلاش اولیه، تأخیر پایه 5/10/20s، jitter حداکثر 20٪؛ در صورت نیاز provider به انتظار بیشتر، رعایت آن تا deadline یا توقف. شمارنده مستقل از retry داخلی HTTP، restart Worker و تلاش اجرای معامله است. پس از اتمام سقف، STARTUP_BLOCKED و اقدام دستی. |
| SRG-013 | در زمان انتظار، stop/pause/emergency-stop و SIGINT/SIGTERM و deadline باید انتظار و درخواست‌های جاری را لغو کنند؛ هیچ مسیر race نباید اجازه admission پس از لغو بدهد. deadline موجود 24h تغییر نکند؛ زمان انتظار از نشست کسر شود (پیشنهاد). |
| SRG-014 | فقط پس از PASS، حلقه تحلیل فعال شود. قبل از هر admission، readiness و intent و deadline و lease به‌صورت سازگار با تراکنش SQLite بررسی شوند. در crash/recover هیچ PASS قدیمی به‌تنهایی کافی نیست. |
| SRG-015 | در RUNNING، بررسی‌های ثابت تکرار نمایشی نشوند؛ اعتبار داده، Gas، سود، stress، موجودی، dedup و reservation همچنان طبق منطق موجود کنترل شوند. خواندن DEX سپس KuCoin، فاصله حداقل 15s و ترتیب اجرای مجازی DEX سپس CEX حفظ شود. |
| SRG-016 | اختلال موقت حین اجرا موجب رد چرخه و عدم admission شود؛ fallback معتبر مجاز است. خطای بحرانی یا مغایرت مالی admission جدید را مسدود کند؛ عملیات قبلاً پذیرفته‌شده طبق قواعد recovery و emergency موجود تعیین تکلیف شود. هیچ جبران خودکار جدید اضافه نشود. |
| SRG-017 | status وضعیت readiness، آخرین نتیجه هر check، علت، زمان، شماره retry و زمان تلاش بعدی را علاوه بر فیلدهای موجود نشان دهد. وضعیت terminal failure پایدار باشد و با recover خودکار دور زده نشود؛ start صریح پس از رفع علت مجاز باشد، مشروط به latch و recovery. |

## ۳. وضعیت‌ها و ترتیب
STARTING → PREFLIGHT_CHECKING → READY → RUNNING؛ در خطای موقت PREFLIGHT_CHECKING → WAITING_RETRY → PREFLIGHT_CHECKING؛ در خطای بحرانی یا اتمام retry → STARTUP_BLOCKED. وضعیت‌های paused/stopped/expired/emergency موجود حفظ شوند. readiness و intent دو مفهوم مستقل‌اند. ترتیب منطقی: config/ownership → storage → public connections → recovery → balances → market/Gas → readiness gate → cycles. Recovery مجاز نشست منقضی فقط مطابق مسیر drain موجود است و نباید به تحلیل منجر شود.

## ۴. نقاط پیاده‌سازی پیشنهادی
runtime.py (gate، supervision، status)، cli.py (خطا و exit code)، coordinator.py و simulation.py (fence و admission)، market_data.py، saucerswap.py، kucoin.py (نتایج typed checks)، storage.py (پایداری وضعیت و migration در صورت نیاز). استفاده از adapter موجود و جلوگیری از درخواست‌های اضافی غیرضروری. هیچ تغییر API عمومی یا schema بدون migration و تست سازگاری.

## ۵. معیار پذیرش
| Test | نتیجه مورد انتظار |
|---|---|
| SRG-T01 | تمام checks موفق: یک Worker فعال، اولین چرخه فقط بعد از READY. |
| SRG-T02 | timeout سپس بازیابی: retry ثبت شود؛ قبل از PASS صفر چرخه و صفر admission. |
| SRG-T03 | sender غایب، chain اشتباه، ledger ناسازگار: توقف غیرصفر بدون retry غیرضروری و بدون admission. |
| SRG-T04 | WS قطع و REST معتبر: آمادگی با حالت degraded؛ هر دو نامعتبر: انتظار. |
| SRG-T05 | Gas یکی از دو جهت نامعتبر: عدم شروع؛ تغییر head موقت قابل retry. |
| SRG-T06 | سقف retry، rate limit و deadline با ساعت جعلی آزموده شوند. |
| SRG-T07 | pause/stop/emergency و crash در هر مرحله: هیچ چرخه یا admission غیرمجاز؛ حفظ رزروها. |
| SRG-T08 | restart با ledger قبلی: عدم seed مجدد؛ manual_review مانع آمادگی. |
| SRG-T09 | بازار بدون سود ولی معتبر: شروع مجاز؛ خطای موقت حین اجرا: skip و بازیابی. |
| SRG-T10 | هیچ درخواست signed/broadcast/order/approval واقعی؛ تست regression مالی و lifecycle کامل. |

## ۶. تصمیمات و محدودیت‌ها
سیاست retry موقت و توقف بحرانی تأیید شده است. سقف و زمان‌بندی retry، پذیرش REST به‌عنوان حالت degraded، کسر زمان pre-flight از deadline، نام وضعیت‌ها و رفتار کمبود پایدار موجودی پیشنهادهای اجرایی برای بازبینی‌اند. سقف سه restart Worker در سند Lifecycle با retry pre-flight یکی نیست. تنظیم پیش‌فرض مخزن sender ندارد؛ در صورت اعمال این سند شروع عادی عمداً مسدود خواهد شد تا sender عمومی مناسب و شواهد معتبر فراهم شود. هیچ تضمینی درباره قابلیت estimation روی میزبان واقعی داده نمی‌شود.

مراجع: https://github.com/elvito2808/Arbitrage/tree/main/src/arbitrage ؛ config/simulation.toml ؛ docs/operations.md ؛ docs/development.md ؛ اسناد Lifecycle، Execution v1.1 و Security پروژه.
