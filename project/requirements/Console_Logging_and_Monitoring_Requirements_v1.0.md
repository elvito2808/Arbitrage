# Console Logging and Monitoring Requirements — CLM v1.0

**وضعیت:** پیش‌نویس آماده پیاده‌سازی و بازبینی مالک
**دامنه:** مکمل Logging_and_Monitoring_Requirements_v1.0 و SRG v1.0؛ SIMULATION ONLY.

## ۱. هدف
نمایش زنده و خوانای مراحل واقعی عملیات در foreground بدون تغییر منطق مالی. نسخه فعلی operational_log.py رویدادهای محدود JSON را در صف و runtime_health نگهداری می‌کند و start در مسیر عادی ساکت است. این سند نمایشگر کنسول و رویدادهای typed موردنیاز را اضافه می‌کند؛ status، report و سوابق مالی فعلی حفظ شوند.

## ۲. الزامات
| ID | الزام |
|---|---|
| CLM-001 | قالب انگلیسی `[HH:MM:SS.mmm] LEVEL [MODULE] Message`؛ UTC با اعلام تاریخ و timezone در آغاز نشست؛ timestamp کامل UTC در رکورد ساختاریافته. شناسه worker-1، session، cycle و execution در رویدادهای مرتبط قابل همبستگی باشند. |
| CLM-002 | INFO پیش‌فرض کنسول؛ DEBUG قابل انتخاب برای جزئیات فنی؛ WARNING اختلال قابل بازیابی؛ ERROR شکست؛ CRITICAL تهدید ایمنی. هیچ پیام موفقیت قبل از نتیجه واقعی عملیات چاپ نشود. |
| CLM-003 | در startup فقط یک بار config، logger، storage، mode، ظرفیت/تعداد فعال، network، pair، حجم، threshold، loss limit، slippage، fee، gas cap، TTL، interval، deadline و parity نمایش داده شوند. مقادیر از config اعمال‌شده باشند. |
| CLM-004 | هر check آمادگی پس از نتیجه با PASS/FAIL/UNKNOWN یا DEGRADED ثبت شود؛ retry شامل reason، attempt و next delay؛ STARTUP_BLOCKED و علت نهایی واضح باشند. All checks passed فقط پس از تحقق همه شروط SRG. |
| CLM-005 | موجودی آغازین از ledger جاری، نه config seed، با WHBAR و HBAR Gas جدا، USDC و HBAR/USDT KuCoin و reserved/available نمایش داده شود. reconciliation فقط پس از بررسی واقعی PASS اعلام شود. |
| CLM-006 | هر چرخه یک شماره خوانا و شناسه پایدار داشته باشد؛ آغاز و پایان، مدت، چهار quote برای کل 1000 HBAR، ارز quote، منبع CEX و وضعیت اعتبار نمایش داده شوند. شماره نمایشی با UUID واقعی مرتبط باشد؛ شماره به‌تنهایی شناسه مالی نیست. |
| CLM-007 | DEX BUY/SELL gross شامل AMM fee و price impact، CEX BUY/SELL شامل fee و عمق؛ Gas جاری و capped جدا با واحد HBAR/USDT در صورت وجود. سود indicative و net capped و stressed هر دو جهت با دقت مناسب از مدل واقعی خوانده شوند؛ هزینه دوباره کسر نشود. مقدار ناموجود UNKNOWN، نه صفر. |
| CLM-008 | نتیجه واقعی هر جهت (market_candidate، insufficient_data، rejected، eligible_for_simulation) و reason enum ثبت شود. نتیجه کلی NO_OPPORTUNITY فقط وقتی قابل اثبات است؛ gas_unknown/invalid نباید به عنوان عدم فرصت قطعی یا سود خالص نمایش داده شود. اولویت دلیل رد مطابق coordinator موجود حفظ شود. |
| CLM-009 | در چرخه عادی، startup checks دوباره چاپ نشوند. تغییر منبع WS/REST، stale، timeout، rate limit، duplicate، insufficient depth، skew و سایر خطاها با دلیل واقعی نمایش داده شوند. خطای تکراری قابل تجمیع است ولی نتیجه هر چرخه حذف نشود. |
| CLM-010 | هنگام فرصت، direction، estimated/stressed profit، نتیجه guard و admission، reservation و execution ID ثبت شوند. تفاوت eligibility با admission مشخص باشد. ادعای quote جدید یا revalidation شبکه فقط اگر واقعاً انجام شده باشد. |
| CLM-011 | اجرای مجازی: DEX قبل از CEX، attempt، outcome، fill، fee، Gas، retry، partial، incomplete، manual_review و hold باقی‌مانده از journal واقعی نمایش داده شوند. پیام transaction sent/order sent واقعی ممنوع؛ برچسب SIMULATED صریح. |
| CLM-012 | پس از commit مالی، تغییر موجودی و مانده total/reserved/available و نتیجه reconciliation نمایش داده شود. در حالت نامشخص، مانده نامطمئن و hold حفظ‌شده گزارش شود؛ سود تحقق‌یافته واقعی ادعا نشود. Stablecoin delta و Gas جدا و سود مدل‌شده فقط با مبنای ارزش‌گذاری مستند و قابل محاسبه. |
| CLM-013 | pause، stop، emergency، expiry، recovery و crash قابل مشاهده باشند؛ خلاصه نشست شامل تعداد چرخه، admission، نتیجه trades و خطاها از داده پایدار و با scope روشن استخراج شود. شمارنده lifetime با session اشتباه نشود. |
| CLM-014 | نمایش زنده با flush مناسب و sink مستقل از producer؛ صف محدود، عدم blocking حلقه مالی، شمارنده dropped و هشدار بازیابی sink. هیچ I/O کنسول داخل تراکنش مالی؛ رویداد مالی پس از commit یا از journal خوانده شود. |
| CLM-015 | schema بسته و allowlist برای event/fields؛ عدم چاپ secret، private key، auth header، URL حساس، payload خام یا exception آزاد. sanitize کنترل‌کاراکترها. آدرس sender در کنسول لازم نیست. |
| CLM-016 | حفظ status JSON و report CLI بدون آلودگی stdout به لاگ؛ نمایش foreground به stderr پیشنهاد می‌شود. تاریخچه ساختاریافته محدود 256 فعلی و دفتر مالی پایدار حفظ شوند. ذخیره بلندمدت کنسول با rotation/retention نیازمند سیاست جداگانه میزبان است؛ deployment انجام نشود. |
| CLM-017 | logging failure نباید سبب تکرار معامله یا rollback commit موفق شود؛ خرابی ledger با خرابی sink متفاوت است. قابلیت بازیابی مالی مستقل از لاگ متنی باقی بماند. |

## ۳. نمونه قرارداد نمایش (فرضی، نه خروجی اجراشده)
```text
[10:00:00.001] INFO  [SYSTEM] Starting SIMULATION (UTC)
[10:00:00.012] INFO  [CONFIG] Workers: 3; active limit: 1
[10:00:00.020] INFO  [SECURITY] Real transactions disabled
[10:00:00.030] INFO  [STORAGE] SQLite ready
[10:00:01.000] INFO  [HEDERA] Chain ID verified: 295
[10:00:02.000] INFO  [KUCOIN] Market data: REST (degraded)
[10:00:03.000] INFO  [RECOVERY] Unresolved virtual trades: 0
[10:00:03.100] INFO  [BALANCE] DEX WHBAR=50000 USDC=3000 HBAR(gas)=1000
[10:00:03.101] INFO  [BALANCE] KuCoin HBAR=50000 USDT=3000
[10:00:04.000] INFO  [GAS] BUY and SELL evidence verified
[10:00:04.001] INFO  [HEALTH] All required checks passed
[10:00:04.002] INFO  [WORKER-01] RUNNING

[10:00:05.000] INFO  [WORKER-01] CHECK #001
[10:00:07.000] INFO  [DEX] BUY=201.20 SELL=200.10 USDC (gross)
[10:00:07.001] INFO  [KUCOIN] BUY=200.80 SELL=200.40 USDT (fee included)
[10:00:07.002] INFO  [GAS] Capped BUY=0.05 SELL=0.04 USDT
[10:00:07.003] INFO  [ARB] buy_dex net=-0.85; sell_dex net=-0.74 USDT
[10:00:07.004] INFO  [ARB] buy_dex=rejected; sell_dex=rejected
[10:00:07.005] INFO  [ARB] NO_OPPORTUNITY
```
نمونه فوق فرض می‌کند sender مناسب و Gas معتبر فراهم شده است؛ تنظیم پیش‌فرض مخزن این شرط را ندارد. در پیاده‌سازی، reason و stressed profit واقعی هر جهت نیز چاپ شوند. زمان‌ها و قیمت‌ها نمونه‌اند.

```text
[10:00:04.000] WARN  [HEALTH] KuCoin market data unavailable
[10:00:04.001] WARN  [STARTUP] WAITING_RETRY attempt=1/3 delay=5s
[10:00:09.001] INFO  [STARTUP] Retrying failed checks
```
```text
[10:00:04.000] ERROR [GAS] Estimation sender not configured
[10:00:04.001] ERROR [STARTUP] STARTUP_BLOCKED
[10:00:04.002] INFO  [SYSTEM] New analysis cycles: 0; new admissions: 0
```

## ۴. نقاط پیاده‌سازی
operational_log.py: typed schema و renderer؛ runtime.py: sink lifecycle و readiness/summary؛ cli.py: انتخاب level و سازگاری stdout؛ coordinator.py: نتیجه چرخه؛ simulation.py و storage.py: نمایش journal و مانده پس از commit؛ adapters: source transitions و check results. reporting.py و status موجود حفظ شوند.

## ۵. معیار پذیرش
| Test | نتیجه |
|---|---|
| CLM-T01 | ترتیب startup و عدم پیام RUNNING پیش از PASS؛ شکست و retry قابل مشاهده. |
| CLM-T02 | 10 چرخه کنترل‌شده، 10 شماره و دو جهت در هر رکورد، بدون تکرار startup. |
| CLM-T03 | چهار quote، Gas و سود دقیقاً مطابق persisted evaluation؛ unknown به صفر تبدیل نشود. |
| CLM-T04 | fallback، duplicate، stale، insufficient depth و gas_unknown با reason واقعی. |
| CLM-T05 | اجرای موفق fixture مجاز، DEX failure، CEX partial و unknown: ترتیب و hold و ledger درست؛ fixture به runtime production راه نیابد. |
| CLM-T06 | crash پس از commit، restart و recovery: عدم چاپ موفقیت کاذب و عدم اجرای تکراری. |
| CLM-T07 | sink کند/قطع و queue full: تحلیل و ledger ایمن، drops قابل مشاهده. |
| CLM-T08 | secret injection و کنترل‌کاراکتر: عدم افشا؛ status JSON و report بدون تغییر قرارداد. |
| CLM-T09 | INFO/DEBUG و زمان UTC، شناسه‌های همبسته و خلاصه نشست صحیح. |
| CLM-T10 | اجرای تست‌های regression آفلاین؛ public smoke فقط با مجوز جدا؛ بدون معامله واقعی یا deployment. |

## ۶. تصمیمات باز
INFO پیش‌فرض، stderr، قالب UTC، نام رویدادها و سیاست تجمیع پیشنهاد فنی‌اند. retention بلندمدت و اندازه فایل هنوز تعیین نشده‌اند. این سند جایگزین دفتر مالی یا گزارش Excel نیست. هیچ کد یا تنظیمات مخزن با تهیه این سند تغییر نکرده است.

مراجع: https://github.com/elvito2808/Arbitrage/tree/main/src/arbitrage ؛ operational_log.py، runtime.py، coordinator.py، simulation.py، reporting.py، docs/operations.md و سند Logging v1.0 پروژه.
