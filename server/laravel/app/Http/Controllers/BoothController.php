<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;

/**
 * NEO PHOTO booth <-> website.
 *
 * The booth app opens GET /booth/bridge in a hidden browser (so the hosting's
 * anti-bot check sees a real browser), then posts from inside that page:
 *   POST /booth/promo    {"code": "..."}            -> is the promo code valid?
 *   POST /booth/session  {session_id, customer, ...} -> customers / orders / order_items
 *   POST /booth/coupon   {"code": "..."}            -> order_items row for a paid order
 * All need the X-Api-Key header (BOOTH_API_KEY in .env) and the page's CSRF token.
 */
class BoothController extends Controller
{
    public function bridge()
    {
        return response()->view('booth.bridge')->header('Cache-Control', 'no-store');
    }

    private function authorized(Request $r): bool
    {
        $key = (string) config('services.booth.key', '');
        return $key !== '' && hash_equals($key, (string) $r->header('X-Api-Key', ''));
    }

    private static function str(array $in, string $k, int $max): string
    {
        $v = isset($in[$k]) && is_scalar($in[$k]) ? trim((string) $in[$k]) : '';
        return mb_substr($v, 0, $max, 'UTF-8');
    }

    private static function int(array $in, string $k): int
    {
        return isset($in[$k]) && is_numeric($in[$k]) ? max(0, (int) $in[$k]) : 0;
    }

    private static function percentTypes(): array
    {
        return ['percent', 'persen', 'percentage', '%'];
    }

    public function promo(Request $r)
    {
        if (!$this->authorized($r)) {
            return response()->json(['error' => 'unauthorized'], 401);
        }
        $code = trim((string) $r->input('code', ''));
        if ($code === '' || strlen($code) > 11) {
            return response()->json(['valid' => false, 'message' => 'Kode promo tidak ditemukan'], 404);
        }
        $p = DB::table('promos')->where('code', $code)->first();
        if (!$p) {
            return response()->json(['valid' => false, 'message' => 'Kode promo tidak ditemukan'], 404);
        }
        if (config('services.booth.promo_single_use', true) && $p->order_id !== null) {
            return response()->json(['valid' => false, 'message' => 'Kode promo sudah pernah dipakai'], 409);
        }
        $type = strtolower(trim((string) $p->Type));
        return response()->json([
            'valid'   => true,
            'id'      => (int) $p->id,
            'code'    => $p->code,
            'nama'    => $p->nama,
            'diskon'  => (int) preg_replace('/[^0-9]/', '', (string) $p->diskon),
            'percent' => in_array($type, self::percentTypes(), true) || str_contains((string) $p->diskon, '%'),
        ]);
    }

    /**
     * Coupon mode: the code the website generated for an order (order_items.code).
     * Valid when its order is paid; the booth picks the layout from printType and
     * prints printCount sheets. Read-only: nothing is marked used here.
     */
    public function coupon(Request $r)
    {
        if (!$this->authorized($r)) {
            return response()->json(['error' => 'unauthorized'], 401);
        }
        $code = trim((string) $r->input('code', ''));
        $notFound = ['valid' => false, 'reason' => 'not_found', 'message' => 'Kode tidak ditemukan'];
        if ($code === '' || mb_strlen($code) > 255) {
            return response()->json($notFound, 404);
        }
        $item = DB::table('order_items')
            ->join('orders', 'orders.id', '=', 'order_items.order_id')
            ->where('order_items.code', $code)
            ->orderByDesc('order_items.id')
            ->select('order_items.id', 'order_items.order_id', 'order_items.product_id',
                     'order_items.printType', 'order_items.printCount', 'orders.status')
            ->first();
        if (!$item) {
            return response()->json($notFound, 404);
        }
        if (strtolower(trim((string) $item->status)) !== 'paid') {
            return response()->json(['valid' => false, 'reason' => 'not_paid',
                                     'message' => 'Pesanan belum lunas'], 409);
        }
        return response()->json([
            'valid'       => true,
            'order_id'    => (int) $item->order_id,
            'item_id'     => (int) $item->id,
            'product_id'  => $item->product_id !== null ? (int) $item->product_id : null,
            'print_type'  => $item->printType,
            'print_count' => (int) $item->printCount,
        ]);
    }

    /**
     * Sent twice per session with the same session_id (orders.`order-UC`):
     * "paid" right after payment, "done" after printing; the second updates the order.
     */
    public function session(Request $r)
    {
        if (!$this->authorized($r)) {
            return response()->json(['error' => 'unauthorized'], 401);
        }
        $in = $r->json()->all();
        $sid = self::str($in, 'session_id', 255);
        if ($sid === '') {
            return response()->json(['error' => 'session_id required'], 400);
        }
        $tz = 'Asia/Jakarta';
        $at = self::str($in, 'created_at', 19);
        if (!preg_match('/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/', $at)) {
            $at = Carbon::now($tz)->format('Y-m-d H:i:s');
        }
        $now = Carbon::now($tz)->format('Y-m-d H:i:s');

        $name = self::str($in, 'customer_name', 255);
        $telp = preg_replace('/[^0-9+]/', '', self::str($in, 'phone', 30));
        $email = self::str($in, 'email', 255);
        $ig = ltrim(self::str($in, 'instagram', 255), '@');
        $optin = empty($in['promo_optin']) ? 0 : 1;
        $productId = self::int($in, 'product_id');
        $prints = max(1, self::int($in, 'prints'));
        $bonus = self::int($in, 'bonus_photos');
        $extra = self::int($in, 'extra_prints');
        $promoId = self::int($in, 'promo_id') ?: null;

        $meta = [
            'stage' => self::str($in, 'stage', 10) ?: 'done',
            'booth' => self::str($in, 'booth_name', 60), 'outlet' => self::str($in, 'outlet', 25),
            'layout_id' => self::str($in, 'layout_id', 60), 'layout_name' => self::str($in, 'layout_name', 120),
            'frame' => self::str($in, 'frame', 120), 'filter' => self::str($in, 'filter', 40),
            'shots_total' => self::int($in, 'shots_total'), 'photos_kept' => self::int($in, 'photos_kept'),
            'bonus_photos' => $bonus, 'prints' => $prints, 'extra_prints' => $extra,
            'amount_package' => self::int($in, 'amount_package'), 'amount_bonus' => self::int($in, 'amount_bonus'),
            'amount_extras' => self::int($in, 'amount_extras'), 'payment_mode' => self::str($in, 'payment_mode', 20),
            'order_ids' => self::str($in, 'order_ids', 255),
            'promo_code' => self::str($in, 'promo_code', 11), 'amount_discount' => self::int($in, 'amount_discount'),
            'folder_url' => self::str($in, 'folder_url', 255),
        ];

        $existing = DB::table('orders')->where('order-UC', $sid)->first();
        if ($existing && $meta['stage'] === 'paid') {
            $old = json_decode($existing->booth_data ?? '', true);
            if (($old['stage'] ?? '') === 'done') {      // late "paid" resend: keep the final data
                return response()->json(['ok' => true, 'order_id' => (int) $existing->id, 'duplicate' => true]);
            }
        }
        if ($productId > 0 && !DB::table('products')->where('id', $productId)->exists()) {
            return response()->json(['error' => "product_id $productId not in products"], 422);
        }

        try {
            $result = DB::transaction(function () use (
                $existing, $sid, $at, $now, $name, $telp, $email, $ig, $optin, $productId,
                $prints, $bonus, $extra, $promoId, $meta, $in
            ) {
                // Customer: same phone number = same customer, details refreshed.
                $custId = null;
                if ($telp !== '') {
                    $custId = DB::table('customers')->where('telp', $telp)->orderByDesc('id')->value('id');
                }
                if (!$custId && $existing) {
                    $custId = $existing->customer_id;      // second post without a phone
                }
                if ($custId) {
                    $c = DB::table('customers')->where('id', $custId)->first();
                    DB::table('customers')->where('id', $custId)->update([
                        'name' => $name !== '' ? $name : $c->name,
                        'email' => $email !== '' ? $email : $c->email,
                        'instagram' => $ig !== '' ? $ig : $c->instagram,
                        'promo_optin' => max((int) $c->promo_optin, $optin),
                        'updated_at' => $now,
                    ]);
                } else {
                    $custId = DB::table('customers')->insertGetId([
                        'name' => $name !== '' ? $name : '-', 'email' => $email !== '' ? $email : null,
                        'telp' => $telp, 'instagram' => $ig !== '' ? $ig : null, 'promo_optin' => $optin,
                        'created_at' => $at, 'updated_at' => $at,
                    ]);
                }

                $order = [
                    'customer_id' => $custId,
                    'status' => config('services.booth.order_status', 'paid'),
                    'promo_id' => $promoId,
                    'apprcode' => (self::str($in, 'apprcode', 255) ?: self::str($in, 'order_ids', 255)) ?: null,
                    'total' => (string) self::int($in, 'amount_total'),
                    'outlet' => $meta['outlet'] ?: null,
                    'machine' => $meta['booth'] ?: null,
                    'bonus_photos' => $bonus, 'bonus_amount' => self::int($in, 'amount_bonus'),
                    'extra_prints' => $extra, 'extra_amount' => self::int($in, 'amount_extras'),
                    'booth_data' => json_encode($meta, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
                ];
                if ($existing) {
                    $orderId = (int) $existing->id;
                    DB::table('orders')->where('id', $orderId)->update($order + ['updated_at' => $now]);
                } else {
                    $orderId = DB::table('orders')->insertGetId(
                        $order + ['order-UC' => $sid, 'created_at' => $at, 'updated_at' => $at]);
                }

                if ($productId > 0) {
                    $itemId = DB::table('order_items')->where('order_id', $orderId)->orderBy('id')->value('id');
                    if ($itemId) {
                        DB::table('order_items')->where('id', $itemId)->update(
                            ['product_id' => $productId, 'printCount' => $prints, 'updated_at' => $now]);
                    } else {
                        DB::table('order_items')->insert([
                            'order_id' => $orderId, 'product_id' => $productId, 'printCount' => $prints,
                            'created_at' => $at, 'updated_at' => $at,
                        ]);
                    }
                }

                // A single-use promo is used up by this order.
                if ($promoId && config('services.booth.promo_single_use', true)) {
                    DB::table('promos')->where('id', $promoId)->whereNull('order_id')
                        ->update(['order_id' => $orderId, 'updated_at' => $now]);
                }
                return ['order_id' => $orderId, 'customer_id' => $custId];
            });
        } catch (\Throwable $e) {
            Log::error('[booth] session ' . $sid . ': ' . $e->getMessage());
            // Only an authorised booth gets here, so it may see why (shows up in the booth log).
            return response()->json(['error' => 'database error',
                                     'detail' => mb_substr($e->getMessage(), 0, 300)], 500);
        }
        return response()->json(['ok' => true] + $result);
    }
}
