<?php
// Booth -> website: one photobooth session per POST (JSON), written into the
// existing tables: customers -> orders -> order_items (-> order_item_addons).
// The same session_id sent twice is stored once (orders.`order-UC`).
require __DIR__ . '/booth_config.php';
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function out($code, $data) { http_response_code($code); echo json_encode($data); exit; }

if ($_SERVER['REQUEST_METHOD'] !== 'POST') out(405, ['error' => 'POST only']);
$key = $_SERVER['HTTP_X_API_KEY'] ?? '';
if (BOOTH_API_KEY === '' || !hash_equals(BOOTH_API_KEY, $key)) out(401, ['error' => 'unauthorized']);

$in = json_decode(file_get_contents('php://input', false, null, 0, 65536) ?: '', true);
if (!is_array($in)) out(400, ['error' => 'bad json']);

$str = function ($k, $max) use ($in) {
    $v = isset($in[$k]) && is_scalar($in[$k]) ? trim((string)$in[$k]) : '';
    return mb_substr($v, 0, $max, 'UTF-8');
};
$int = function ($k) use ($in) {
    return isset($in[$k]) && is_numeric($in[$k]) ? max(0, (int)$in[$k]) : 0;
};

$sid = $str('session_id', 255);
if ($sid === '') out(400, ['error' => 'session_id required']);
$at = $str('created_at', 19);
if (!preg_match('/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/', $at)) $at = date('Y-m-d H:i:s');

$name = $str('customer_name', 255);
$telp = preg_replace('/[^0-9+]/', '', $str('phone', 30));
$email = $str('email', 255);
$ig = ltrim($str('instagram', 255), '@');
$productId = $int('product_id');
$prints = max(1, $int('prints'));
$bonus = $int('bonus_photos');
$extra = $int('extra_prints');

// Everything the existing columns have no place for.
$meta = [
    'booth' => $str('booth_name', 60), 'layout_id' => $str('layout_id', 60),
    'layout_name' => $str('layout_name', 120), 'frame' => $str('frame', 120),
    'filter' => $str('filter', 40), 'shots_total' => $int('shots_total'),
    'photos_kept' => $int('photos_kept'), 'bonus_photos' => $bonus,
    'prints' => $prints, 'extra_prints' => $extra,
    'amount_package' => $int('amount_package'), 'amount_bonus' => $int('amount_bonus'),
    'amount_extras' => $int('amount_extras'), 'payment_mode' => $str('payment_mode', 20),
    'folder_url' => $str('folder_url', 255),
];

try {
    $pdo = db();
    $st = $pdo->prepare('SELECT id FROM orders WHERE `order-UC` = ? LIMIT 1');
    $st->execute([$sid]);
    if ($row = $st->fetch()) out(200, ['ok' => true, 'order_id' => (int)$row['id'], 'duplicate' => true]);

    if ($productId > 0) {
        $st = $pdo->prepare('SELECT id FROM products WHERE id = ?');
        $st->execute([$productId]);
        if (!$st->fetch()) out(422, ['error' => "product_id $productId not in products"]);
    }
    $addonOk = function ($id) use ($pdo) {
        if ($id <= 0) return false;
        $st = $pdo->prepare('SELECT id FROM products WHERE id = ?');
        $st->execute([$id]);
        return (bool)$st->fetch();
    };

    $pdo->beginTransaction();

    // Customer: reuse the one with the same phone number, refresh their details.
    $custId = null;
    if ($telp !== '') {
        $st = $pdo->prepare('SELECT id FROM customers WHERE telp = ? ORDER BY id DESC LIMIT 1');
        $st->execute([$telp]);
        $custId = $st->fetchColumn() ?: null;
    }
    if ($custId) {
        $pdo->prepare('UPDATE customers SET name = COALESCE(NULLIF(?, \'\'), name),
                         email = COALESCE(NULLIF(?, \'\'), email),
                         instagram = COALESCE(NULLIF(?, \'\'), instagram),
                         promo_optin = GREATEST(promo_optin, ?), updated_at = ? WHERE id = ?')
            ->execute([$name, $email, $ig, empty($in['promo_optin']) ? 0 : 1, $at, $custId]);
    } else {
        $pdo->prepare('INSERT INTO customers (name, email, telp, instagram, promo_optin, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)')
            ->execute([$name !== '' ? $name : '-', $email !== '' ? $email : null, $telp,
                       $ig !== '' ? $ig : null, empty($in['promo_optin']) ? 0 : 1, $at, $at]);
        $custId = (int)$pdo->lastInsertId();
    }

    $pdo->prepare('INSERT INTO orders (customer_id, status, `order-UC`, apprcode, total, outlet,
                                       booth_data, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)')
        ->execute([$custId, BOOTH_ORDER_STATUS, $sid, $str('order_ids', 255) ?: null,
                   (string)$int('amount_total'), mb_substr($meta['booth'], 0, 25, 'UTF-8') ?: null,
                   json_encode($meta, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), $at, $at]);
    $orderId = (int)$pdo->lastInsertId();

    if ($productId > 0) {
        $pdo->prepare('INSERT INTO order_items (order_id, product_id, printCount, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)')
            ->execute([$orderId, $productId, $prints, $at, $at]);
        $itemId = (int)$pdo->lastInsertId();
        $addon = $pdo->prepare('INSERT INTO order_item_addons (order_item_id, addon_id, qty, created_at, updated_at)
                                VALUES (?, ?, ?, ?, ?)');
        if ($bonus > 0 && $addonOk(ADDON_BONUS_PHOTO_PRODUCT_ID)) {
            $addon->execute([$itemId, ADDON_BONUS_PHOTO_PRODUCT_ID, (string)$bonus, $at, $at]);
        }
        if ($extra > 0 && $addonOk(ADDON_EXTRA_PRINT_PRODUCT_ID)) {
            $addon->execute([$itemId, ADDON_EXTRA_PRINT_PRODUCT_ID, (string)$extra, $at, $at]);
        }
    }

    $pdo->commit();
    out(200, ['ok' => true, 'order_id' => $orderId, 'customer_id' => $custId]);
} catch (Throwable $e) {
    if (isset($pdo) && $pdo->inTransaction()) $pdo->rollBack();
    error_log('[booth_session] ' . $e->getMessage());
    out(500, ['error' => 'database error']);
}
