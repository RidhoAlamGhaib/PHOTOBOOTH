# NEO PHOTO booth ↔ Laravel site

The booth app opens `/booth/bridge` in a hidden browser, so the hosting's anti-bot check
(Imunify360 "One moment, please...") sees a real browser. From inside that page it posts:

- `POST /booth/promo`   `{"code": "..."}`: checks the `promos` table
- `POST /booth/session` session record: writes `customers` / `orders` / `order_items`

Both use the page's CSRF token plus the `X-Api-Key` header.

## Files (copy into the Laravel project)

| From this folder | To |
|---|---|
| `resources/views/booth/bridge.blade.php` | `resources/views/booth/bridge.blade.php` |
| `app/Http/Controllers/BoothController.php` | `app/Http/Controllers/BoothController.php` |
| `routes/booth.php` | paste its 3 `Route::` lines (+ the `use` line) into `routes/web.php` |

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

Database: run `server/booth-api/migration.sql` (and `migration_002_machine.sql` if you ran
an older one) so `customers.promo_optin` and the `orders` booth columns exist.

## Booth

In `NeoPhoto.py`: `BOOTH_SITE_URL = "https://neophotoindonesia.my.id"` and `SESSIONS_API_KEY`.
Build with `PyQtWebEngine` installed (see `build.md`). Log lines: `[WEB] using hidden browser`,
`[WEB] bridge page loaded ok=True`, `[DB] uploaded session ...`.

Check in a normal browser: `https://neophotoindonesia.my.id/booth/bridge` should show
"NEO PHOTO booth bridge".
