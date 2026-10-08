# NEO PHOTO product list (cPanel)

Upload this folder to your hosting so booths read products (101, 102, …), prices,
bonus-photo and extra-print prices from one place.

1. **Database**: cPanel → MySQL Databases → create a database + user, add the user to the
   database with ALL PRIVILEGES.
2. **Table**: cPanel → phpMyAdmin → select the database → Import → `schema.sql`
   (creates `products` with 4 example rows).
3. **Upload**: File Manager → `public_html/neophoto/` → upload `config.php`, `products.php`,
   `admin.php`.
4. **config.php** (edit on the server): fill `DB_NAME`, `DB_USER`, `DB_PASS`.
   Then open `https://YOURDOMAIN/neophoto/admin.php?hash=YOURPASSWORD` once, copy the hash
   it shows into `ADMIN_PASSWORD_HASH`, save.
5. **Check**: `https://YOURDOMAIN/neophoto/products.php` shows JSON.
   Manage products at `https://YOURDOMAIN/neophoto/admin.php`.
6. **Booth**: in `NeoPhoto.py` set
   `PRODUCTS_URL = "https://YOURDOMAIN/neophoto/products.php"` and rebuild the exe.

Fields per product: `id`, `name`, `type`, `poses`, `frame_dir` (folder on the booth PC,
e.g. `frames/a`), `cut`, `price`, `free_photos` (kept photos included; 0 = same as poses),
`bonus_price` (per kept photo above that), `extra_print_price` (per extra copy), `active`.

Booths keep the last list in `products_cache.json`, so they keep working offline.
Frames are not downloaded: each `frame_dir` must exist on every booth PC.
Use HTTPS (cPanel → SSL/TLS Status → AutoSSL) so prices can't be tampered with in transit.
