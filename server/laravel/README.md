# NEO PHOTO booth ↔ Laravel site

The booth app opens `/booth/bridge` in a hidden browser, so the hosting's anti-bot check
(Imunify360 "One moment, please...") sees a real browser. From inside that page it posts:

- `POST /booth/promo`   `{"code": "..."}`: checks the `promos` table
- `POST /booth/session` session record: writes `customers` / `orders` / `order_items`
- `POST /booth/coupon`  `{"code": "..."}`: coupon mode, finds `order_items.code` whose order is `paid`
  and returns `print_type` (printType) and `print_count` (printCount). Read-only.
- `POST /booth/coupon/use` `{"code", "item_id", "machine", "outlet"}`: customer pressed MULAI FOTO,
  sets `order_items.used_at` (+ `used_machine`). Without that column it just answers ok.

All use the page's CSRF token plus the `X-Api-Key` header.

## Files (copy into the Laravel project)

| From this folder | To |
|---|---|
| `resources/views/booth/bridge.blade.php` | `resources/views/booth/bridge.blade.php` |
| `app/Http/Controllers/BoothController.php` | `app/Http/Controllers/BoothController.php` |
| `routes/booth.php` | paste its 5 `Route::` lines (+ the `use` line) into `routes/web.php` |

## Settings

`config/services.php`, inside the returned array:

```php
'booth' => [
    'key' => env('BOOTH_API_KEY', ''),
    'order_status' => env('BOOTH_ORDER_STATUS', 'paid'),
    'promo_single_use' => env('BOOTH_PROMO_SINGLE_USE', true),
],
```

`.env`: `BOOTH_API_KEY=<long random string>` (same as `SESSIONS_API_KEY` in `NeoPhoto.py`).
If the site caches config, run `php artisan config:clear` (or `config:cache`) afterwards.

Database: run `server/booth-api/migration_safe.sql` on the live database (only adds columns
with defaults; existing rows untouched; safe to run twice).

## Booth

In `NeoPhoto.py`: `BOOTH_SITE_URL = "https://neophotoindonesia.my.id"` and `SESSIONS_API_KEY`.
Build with `PyQtWebEngine` installed (see `build.md`). Log lines: `[WEB] using hidden browser`,
`[WEB] bridge page loaded ok=True`, `[DB] uploaded session ...`.

Check in a normal browser: `https://neophotoindonesia.my.id/booth/bridge` should show
"NEO PHOTO booth bridge".

## Coupon mode

`config.json` on the booth: `"payment": {"mode": "coupon", "coupon_start_minutes": 5}`
(`"qris"` = normal QRIS flow). The customer types the code; the booth asks `/booth/coupon`:

- code unknown, order not `paid`, or `used_at` already set -> popup, "panggil karyawan"
- `printType` must equal a layout's `"print_type"` in the booth's config (or its `"id"` when
  `print_type` is not set), otherwise popup "Frame tidak tersedia"
- valid -> "Kode Valid!" screen with a MULAI FOTO button and a countdown
  (`coupon_start_minutes`). Only MULAI uses the code (`/booth/coupon/use`); Batal or the
  countdown running out sends the customer home with the code still unused
- prints `printCount` sheets; no contact form, no QRIS, no paid bonus photos / extra prints

One-time use needs the columns from `server/booth-api/migration_003_coupon_used.sql`
(`order_items.used_at`, `used_machine`). Until they exist, codes work but are never marked.
