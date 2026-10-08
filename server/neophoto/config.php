<?php
// Fill these in on the server (cPanel > File Manager > Edit). Never commit real values.
// Database: cPanel > MySQL Databases (create DB + user, add user to DB with ALL PRIVILEGES).
const DB_HOST = 'localhost';
const DB_NAME = '';   // e.g. 'cpaneluser_neophoto'
const DB_USER = '';   // e.g. 'cpaneluser_neo'
const DB_PASS = '';

// Admin page password, stored as a hash. Make one on any PC with PHP:
//   php -r "echo password_hash('PASSWORD_KAMU', PASSWORD_DEFAULT), PHP_EOL;"
// or open admin.php?hash=PASSWORD_KAMU once (only works while this is empty).
const ADMIN_PASSWORD_HASH = '';

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
