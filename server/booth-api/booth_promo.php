<?php
// Booth -> website: is this promo code valid? POST {"code": "..."} with X-Api-Key.
// Answers {"valid": true, "id", "code", "nama", "diskon", "percent"} or
// 404 / 409 {"valid": false, "message"} (message is shown on the booth screen).
require __DIR__ . '/booth_config.php';
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function out($code, $data) { http_response_code($code); echo json_encode($data); exit; }

if ($_SERVER['REQUEST_METHOD'] !== 'POST') out(405, ['error' => 'POST only']);
$key = $_SERVER['HTTP_X_API_KEY'] ?? '';
if (BOOTH_API_KEY === '' || !hash_equals(BOOTH_API_KEY, $key)) out(401, ['error' => 'unauthorized']);

$in = json_decode(file_get_contents('php://input', false, null, 0, 4096) ?: '', true);
$code = is_array($in) && isset($in['code']) && is_scalar($in['code']) ? trim((string)$in['code']) : '';
if ($code === '' || strlen($code) > 11) out(404, ['valid' => false, 'message' => 'Kode promo tidak ditemukan']);

try {
    $st = db()->prepare('SELECT id, code, nama, diskon, `Type`, order_id FROM promos WHERE code = ? LIMIT 1');
    $st->execute([$code]);
    $p = $st->fetch();
    if (!$p) out(404, ['valid' => false, 'message' => 'Kode promo tidak ditemukan']);
    if (PROMO_SINGLE_USE && $p['order_id'] !== null) {
        out(409, ['valid' => false, 'message' => 'Kode promo sudah pernah dipakai']);
    }
    $type = strtolower(trim((string)$p['Type']));
    $percent = in_array($type, PROMO_PERCENT_TYPES, true) || str_contains((string)$p['diskon'], '%');
    out(200, [
        'valid'   => true,
        'id'      => (int)$p['id'],
        'code'    => $p['code'],
        'nama'    => $p['nama'],
        'diskon'  => (int)preg_replace('/[^0-9]/', '', (string)$p['diskon']),
        'percent' => $percent,
    ]);
} catch (Throwable $e) {
    error_log('[booth_promo] ' . $e->getMessage());
    out(500, ['error' => 'database error']);
}
