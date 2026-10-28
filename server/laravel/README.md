# NEO PHOTO booth ↔ Laravel site

The booth app opens `/booth/bridge` in a hidden browser, so the hosting's anti-bot check
(Imunify360 "One moment, please...") sees a real browser. From inside that page it posts:

- `POST /booth/promo`   `{"code": "..."}`: checks the `promos` table
- `POST /booth/session` session record: writes `customers` / `orders` / `order_items`
- `POST /booth/coupon`  `{"code": "..."}`: coupon mode, finds `order_items.code` whose order is `paid`
  and returns `print_type` (printType) and `print_count` (printCount). Read-only.

All use the page's CSRF token plus the `X-Api-Key` header.

## Files (copy into the Laravel project)

| From this folder | To |
|---|---|
| `resources/views/booth/bridge.blade.php` | `resources/views/booth/bridge.blade.php` |
| `app/Http/Controllers/BoothController.php` | `app/Http/Controllers/BoothController.php` |
| `routes/booth.php` | paste its 4 `Route::` lines (+ the `use` line) into `routes/web.php` |

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

`config.json` on the booth: `"payment": {"mode": "coupon"}` (`"qris"` = normal QRIS flow).
The customer types the code; the booth asks `/booth/coupon` and:

- code unknown, or order not `paid` -> popup, "panggil karyawan"
- `printType` must equal a layout's `"print_type"` in the booth's config (or its `"id"` when
  `print_type` is not set), otherwise popup "Frame tidak tersedia"
- prints `printCount` sheets; no contact form, no QRIS, no paid bonus photos / extra prints,
  nothing is written back to the database

Codes are not marked as used yet: one-time use needs a column on `order_items` (e.g.
`redeemed_at`), checked and set in `BoothController::coupon()`.
