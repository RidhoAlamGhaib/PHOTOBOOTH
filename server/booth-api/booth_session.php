<?php
// Booth -> website: photobooth session (JSON POST), written into the existing
// tables: customers -> orders -> order_items. Bonus photos and extra prints are
// columns on orders (see migration.sql).
// The booth sends each session twice with the same session_id (orders.`order-UC`):
//   stage "paid": right after payment (customer, package, amount)
//   stage "done": after printing (frame, sheets, bonus photos, extra prints, total)
// The second one updates the same order instead of adding a new one.
require __DIR__ . '/booth_config.php';
// Defaults for settings added later, so an older booth_config.php keeps working.
if (!defined('PROMO_SINGLE_USE')) define('PROMO_SINGLE_USE', true);
if (!defined('PROMO_PERCENT_TYPES')) define('PROMO_PERCENT_TYPES', ['percent', 'persen', 'percentage', '%']);
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
$promoId = $int('promo_id') ?: null;

// Everything the existing columns have no place for.
$meta = [
    'stage' => $str('stage', 10) ?: 'done',
    'booth' => $str('booth_name', 60), 'outlet' => $str('outlet', 25), 'layout_id' => $str('layout_id', 60),
    'layout_name' => $str('layout_name', 120), 'frame' => $str('frame', 120),
    'filter' => $str('filter', 40), 'shots_total' => $int('shots_total'),
    'photos_kept' => $int('photos_kept'), 'bonus_photos' => $bonus,
    'prints' => $prints, 'extra_prints' => $extra,
    'amount_package' => $int('amount_package'), 'amount_bonus' => $int('amount_bonus'),
    'amount_extras' => $int('amount_extras'), 'payment_mode' => $str('payment_mode', 20),
    'promo_code' => $str('promo_code', 11), 'amount_discount' => $int('amount_discount'),
    'folder_url' => $str('folder_url', 255),
];

try {
    $pdo = db();
    $st = $pdo->prepare('SELECT id, customer_id, booth_data FROM orders WHERE `order-UC` = ? LIMIT 1');
    $st->execute([$sid]);
    $existing = $st->fetch() ?: null;
    if ($existing && $meta['stage'] === 'paid') {
        $old = json_decode($existing['booth_data'] ?? '', true);
        if (($old['stage'] ?? '') === 'done') {      // late "paid" resend: keep the final data
            out(200, ['ok' => true, 'order_id' => (int)$existing['id'], 'duplicate' => true]);
        }
    }

    if ($productId > 0) {
        $st = $pdo->prepare('SELECT id FROM products WHERE id = ?');
        $st->execute([$productId]);
        if (!$st->fetch()) out(422, ['error' => "product_id $productId not in products"]);
    }

    $pdo->beginTransaction();

    // Customer: reuse the one with the same phone number, refresh their details.
    $custId = null;
    if ($telp !== '') {
        $st = $pdo->prepare('SELECT id FROM customers WHERE telp = ? ORDER BY id DESC LIMIT 1');
        $st->execute([$telp]);
        $custId = $st->fetchColumn() ?: null;
    }
    if (!$custId && $existing) $custId = (int)$existing['customer_id'];   // second post, no phone
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

    $metaJson = json_encode($meta, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    $machine = $meta['booth'] ?: null;
    $outlet = $meta['outlet'] ?: null;
    $now = date('Y-m-d H:i:s');
    if ($existing) {
        $orderId = (int)$existing['id'];
        $pdo->prepare('UPDATE orders SET customer_id = ?, status = ?, promo_id = ?, apprcode = ?, total = ?,
                              outlet = ?, machine = ?, bonus_photos = ?, bonus_amount = ?, extra_prints = ?,
                              extra_amount = ?, booth_data = ?, updated_at = ? WHERE id = ?')
            ->execute([$custId, BOOTH_ORDER_STATUS, $promoId, $str('order_ids', 255) ?: null,
                       (string)$int('amount_total'), $outlet, $machine, $bonus, $int('amount_bonus'),
                       $extra, $int('amount_extras'), $metaJson, $now, $orderId]);
    } else {
        $pdo->prepare('INSERT INTO orders (customer_id, status, `order-UC`, promo_id, apprcode, total, outlet,
                                           machine, bonus_photos, bonus_amount, extra_prints, extra_amount,
                                           booth_data, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
            ->execute([$custId, BOOTH_ORDER_STATUS, $sid, $promoId, $str('order_ids', 255) ?: null,
                       (string)$int('amount_total'), $outlet, $machine, $bonus, $int('amount_bonus'),
                       $extra, $int('amount_extras'), $metaJson, $at, $at]);
        $orderId = (int)$pdo->lastInsertId();
    }

    if ($productId > 0) {
        $st = $pdo->prepare('SELECT id FROM order_items WHERE order_id = ? ORDER BY id LIMIT 1');
        $st->execute([$orderId]);
        if ($itemId = (int)$st->fetchColumn()) {
            $pdo->prepare('UPDATE order_items SET product_id = ?, printCount = ?, updated_at = ? WHERE id = ?')
                ->execute([$productId, $prints, $now, $itemId]);
        } else {
            $pdo->prepare('INSERT INTO order_items (order_id, product_id, printCount, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?)')
                ->execute([$orderId, $productId, $prints, $at, $at]);
            $itemId = (int)$pdo->lastInsertId();
        }
    }

    // Mark a single-use promo as used by this order.
    if ($promoId && PROMO_SINGLE_USE) {
        $pdo->prepare('UPDATE promos SET order_id = ?, updated_at = ? WHERE id = ? AND order_id IS NULL')
            ->execute([$orderId, $now, $promoId]);
    }

    $pdo->commit();
    out(200, ['ok' => true, 'order_id' => $orderId, 'customer_id' => $custId]);
} catch (Throwable $e) {
    if (isset($pdo) && $pdo->inTransaction()) $pdo->rollBack();
    error_log('[booth_session] ' . $e->getMessage());
    out(500, ['error' => 'database error']);
}
