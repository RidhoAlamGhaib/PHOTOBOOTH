<?php
// Public, read-only product list for the booths: GET -> {"products":[...]}
require __DIR__ . '/config.php';
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
try {
    $rows = db()->query(
        'SELECT id, name, type, poses, frame_dir, cut, paper_name, price, free_photos,
                bonus_price, extra_print_price, active
           FROM products WHERE active = 1 ORDER BY sort_order, id')->fetchAll();
    foreach ($rows as &$r) {
        foreach (['id','poses','price','free_photos','bonus_price','extra_print_price','active'] as $k) {
            $r[$k] = (int)$r[$k];
        }
    }
    echo json_encode(['products' => $rows, 'updated' => date('c')], JSON_UNESCAPED_UNICODE);
} catch (Throwable $e) {
    http_response_code(500);
    echo json_encode(['error' => 'database unavailable']);
}
