# NEO PHOTO booth → website database

Booth flow: pick layout → contact form → pay (QRIS) → **saved here** → photos → print.

The booth posts each session to `booth_session.php` twice, with the same session id:

1. **right after payment**: customer, package, amount paid (cancelled payments are never saved);
2. **after printing**: frame, filter, sheets, bonus photos, extra prints and the final total.
   This updates the same order; it does not add a second one.

Where it lands in the existing Laravel tables:

| Booth data | Table / column |
|---|---|
| Nama, Telpon, Email, IG | `customers.name`, `telp`, `email`, `instagram` (same phone = same customer, details refreshed) |
| Centang promo | `customers.promo_optin` (new column) |
| Session id | `orders.order-UC` (unique: one order per session) |
| Total paid (paket + foto bonus + cetak tambahan) | `orders.total` |
| Midtrans order ids | `orders.apprcode` |
| Machine name / number (`booth_name` in config.json) | `orders.machine` (new column) |
| Outlet / location (`outlet` in config.json) | `orders.outlet` (max 25 chars) |
| Kode promo | `orders.promo_id`; single-use codes get `promos.order_id` set (see `PROMO_SINGLE_USE`) |
| Foto bonus: jumlah, Rp | `orders.bonus_photos`, `orders.bonus_amount` (new columns) |
| Cetak tambahan: lembar, Rp | `orders.extra_prints`, `orders.extra_amount` (new columns) |
| Layout / frame / filter / photos / Drive link | `orders.booth_data` (new column, JSON) |
| Package (`product_id` of the layout = `products.id`) + sheets printed | `order_items.product_id`, `printCount` |

## Setup (cPanel)

1. **phpMyAdmin** → database `wwwneoph_Neophoto` → SQL → run `migration.sql` once
   (already ran an older one? run only `migration_002_machine.sql`).
2. **File Manager** → upload `booth_session.php`, `booth_promo.php` and `booth_config.php` into the Laravel
   `public` folder (the one the domain points to). Plain PHP files there are served directly.
3. Edit `booth_config.php` on the server: DB name/user/password (same as Laravel's `.env`),
   `BOOTH_API_KEY` (long random string).
4. **Booth PC**, in `NeoPhoto.py`:
   `SESSIONS_URL = "https://YOURDOMAIN/booth_session.php"` and
   `SESSIONS_API_KEY` = the same key (one key for all booths). In `config.json`: `"booth_name"`
   (machine name / number), `"outlet"` (location) per machine and
   `"product_id"` on each layout (the `products.id` of that package). Rebuild the exe.

Test from the booth PC (replace YOURDOMAIN and KEY):

PowerShell:

```powershell
$body = @{ session_id = "test-1"; customer_name = "Test"; phone = "0812" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "https://YOURDOMAIN/booth_session.php" `
  -Headers @{ "X-Api-Key" = "KEY" } -ContentType "application/json" -Body $body
```

CMD:

```
curl -X POST https://YOURDOMAIN/booth_session.php -H "X-Api-Key: KEY" -H "Content-Type: application/json" -d "{\"session_id\":\"test-1\",\"customer_name\":\"Test\",\"phone\":\"0812\"}"
```

Answers: `200` saved (or already saved), `401` wrong key, `422` the layout's `product_id`
is not in `products` (the booth keeps the record and retries; fix the id), `500` database error.

Offline booths keep unsent sessions in `pending_sessions.json` and retry every 5 minutes.
Times are WIB (booth local time; the endpoint also runs in Asia/Jakarta). Use HTTPS so customer data is encrypted in transit.

## Promo codes

On the contact form the customer can type a code from the `promos` table. The booth asks
`booth_promo.php` (same API key) and takes the discount off the package price:
`Type` = `percent` / `persen` (or a `%` in `diskon`) → percentage, anything else → Rupiah off.
A promo that covers the whole price skips the QRIS. With `PROMO_SINGLE_USE = true` a code
works once: `promos.order_id` is set to the order that used it. Note your schema's foreign key
`promos.order_id → orders ON DELETE CASCADE`: deleting that order also deletes the promo row.
