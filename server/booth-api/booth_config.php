<?php
// Fill in on the server only (cPanel > File Manager > Edit). Never commit real values.
// Same database as the Laravel site (see its .env: DB_DATABASE / DB_USERNAME / DB_PASSWORD).
const DB_HOST = 'localhost';
const DB_NAME = '';   // e.g. 'wwwneoph_Neophoto'
const DB_USER = '';
const DB_PASS = '';

// Long random key, same value as SESSIONS_API_KEY in NeoPhoto.py.
//   php -r "echo bin2hex(random_bytes(24)), PHP_EOL;"
// Empty = all uploads are refused.
const BOOTH_API_KEY = '';

// orders.status for a session uploaded by a booth.
const BOOTH_ORDER_STATUS = 'paid';

// products.id used as order_item_addons.addon_id. 0 = don't write that add-on row
// (the amounts are still in orders.total and orders.booth_data).
const ADDON_BONUS_PHOTO_PRODUCT_ID = 0;
const ADDON_EXTRA_PRINT_PRODUCT_ID = 0;

function db(): PDO {
    static $pdo = null;
    if ($pdo === null) {
        $pdo = new PDO('mysql:host=' . DB_HOST . ';dbname=' . DB_NAME . ';charset=utf8mb4',
                       DB_USER, DB_PASS,
                       [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]);
    }
    return $pdo;
}
