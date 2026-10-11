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
| Machine name (`booth_name` in config.json) | `orders.outlet` (max 25 chars) |
| Layout / frame / filter / photos / per-item amounts / Drive link | `orders.booth_data` (new column, JSON) |
| Package (`product_id` of the layout) + sheets printed | `order_items.product_id`, `printCount` |
| Foto bonus / cetak tambahan | `order_item_addons` (only when the add-on product ids are set) |

## Setup (cPanel)

1. **phpMyAdmin** → database `wwwneoph_Neophoto` → SQL → run `migration.sql` once.
2. **File Manager** → upload `booth_session.php` and `booth_config.php` into the Laravel
   `public` folder (the one the domain points to). Plain PHP files there are served directly.
3. Edit `booth_config.php` on the server: DB name/user/password (same as Laravel's `.env`),
   `BOOTH_API_KEY` (long random string), and optionally the products ids used for the
   bonus-photo / extra-print add-ons.
4. **Booth PC**, in `NeoPhoto.py`:
   `SESSIONS_URL = "https://YOURDOMAIN/booth_session.php"` and
   `SESSIONS_API_KEY` = the same key. In `config.json`: `"booth_name"` per machine and
   `"product_id"` on each layout (the `products.id` of that package). Rebuild the exe.

Test from any PC:

```
curl -X POST https://YOURDOMAIN/booth_session.php -H "X-Api-Key: KEY" ^
     -H "Content-Type: application/json" -d "{\"session_id\":\"test-1\",\"customer_name\":\"Test\",\"phone\":\"0812\"}"
```

Answers: `200` saved (or already saved), `401` wrong key, `422` the layout's `product_id`
is not in `products` (the booth keeps the record and retries; fix the id), `500` database error.

Offline booths keep unsent sessions in `pending_sessions.json` and retry every 5 minutes.
Times are the booth's local time. Use HTTPS so customer data is encrypted in transit.
