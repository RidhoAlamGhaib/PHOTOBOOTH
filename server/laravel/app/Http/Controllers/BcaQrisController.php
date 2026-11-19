<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Str;

/**
 * BCA SNAP QRIS (MPM): the two endpoints BCA calls on this website.
 *
 *   POST /openapi/v1.0/access-token/b2b   BCA asks for a token (OAuth & Signature v2.0)
 *   POST /openapi/v1.0/qr/qr-mpm-notify   BCA reports a QRIS payment (SNAP QRIS Notification v2.1)
 *
 * Token request:  X-SIGNATURE = SHA256withRSA(X-CLIENT-KEY + "|" + X-TIMESTAMP), signed
 *                 with BCA's private key, checked here with BCA's public key.
 * Notification:   X-SIGNATURE = HMAC-SHA512(client secret,
 *                   METHOD + ":" + path + ":" + token + ":" + sha256hex(minified body) + ":" + X-TIMESTAMP)
 * Settings: config/services.php "bca" (see server/laravel/README-BCA-QRIS.md).
 * Payments land in the bca_qris_notifications table (migration_004_bca_qris.sql).
 */
class BcaQrisController extends Controller
{
    private const TOKEN_SERVICE = '73';     // SNAP service code: access token B2B
    private const NOTIFY_SERVICE = '52';    // SNAP service code: QRIS MPM notify

    private static function snap(string $http, string $service, string $case, string $message, array $extra = [])
    {
        return response()->json(array_merge([
            'responseCode' => $http . $service . $case,
            'responseMessage' => $message,
        ], $extra), (int) $http);
    }

    private static function cfg(string $key, $default = '')
    {
        return config('services.bca.' . $key, $default);
    }

    /** BCA's public key: a PEM string, or a path to a .pem file. */
    private static function bcaPublicKey()
    {
        $pem = trim((string) self::cfg('public_key'));
        if ($pem !== '' && !str_contains($pem, '-----BEGIN') && is_file($pem)) {
            $pem = trim((string) file_get_contents($pem));
        }
        $pem = str_replace('\n', "\n", $pem);         // a .env value written on one line
        return $pem !== '' ? openssl_pkey_get_public($pem) : false;
    }

    private static function validTimestamp(string $ts): bool
    {
        if (!preg_match('/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?([+-]\d{2}:\d{2}|Z)$/', $ts)) {
            return false;
        }
        try {
            $t = Carbon::parse($ts);
        } catch (\Throwable $e) {
            return false;
        }
        // Allow some clock drift between BCA and this server.
        return abs($t->diffInSeconds(Carbon::now(), false)) <= (int) self::cfg('timestamp_tolerance', 600);
    }

    /** The body with whitespace outside strings removed (byte-exact otherwise). */
    private static function minifiedBody(Request $r): string
    {
        $raw = (string) $r->getContent();
        $out = '';
        $inString = false;
        $len = strlen($raw);
        for ($i = 0; $i < $len; $i++) {
            $c = $raw[$i];
            if ($inString) {
                $out .= $c;
                if ($c === '\\' && $i + 1 < $len) {
                    $out .= $raw[++$i];
                } elseif ($c === '"') {
                    $inString = false;
                }
            } elseif ($c === '"') {
                $inString = true;
                $out .= $c;
            } elseif (!ctype_space($c)) {
                $out .= $c;
            }
        }
        return $out;
    }

    /** POST /openapi/v1.0/access-token/b2b */
    public function token(Request $r)
    {
        $clientKey = (string) $r->header('X-CLIENT-KEY', '');
        $ts = (string) $r->header('X-TIMESTAMP', '');
        $sig = (string) $r->header('X-SIGNATURE', '');
        foreach (['X-CLIENT-KEY' => $clientKey, 'X-TIMESTAMP' => $ts, 'X-SIGNATURE' => $sig] as $name => $v) {
            if ($v === '') {
                return self::snap('400', self::TOKEN_SERVICE, '02', "Invalid Mandatory Field {$name}");
            }
        }
        if (!self::validTimestamp($ts)) {
            return self::snap('400', self::TOKEN_SERVICE, '01', 'Invalid Field Format X-TIMESTAMP');
        }
        if ((string) $r->input('grantType', '') !== 'client_credentials') {
            return self::snap('400', self::TOKEN_SERVICE, '01', 'Invalid Field Format grantType');
        }
        $clientId = (string) self::cfg('client_id');
        if ($clientId === '' || !hash_equals($clientId, $clientKey)) {
            Log::warning('[bca] token: unknown X-CLIENT-KEY ' . Str::limit($clientKey, 40));
            return self::snap('401', self::TOKEN_SERVICE, '00', 'Unauthorized. Unknown client');
        }
        $key = self::bcaPublicKey();
        if (!$key) {
            Log::error('[bca] token: services.bca.public_key missing or not a valid PEM public key');
            return self::snap('500', self::TOKEN_SERVICE, '00', 'General Error');
        }
        $ok = openssl_verify($clientKey . '|' . $ts, (string) base64_decode($sig, true), $key, OPENSSL_ALGO_SHA256);
        if ($ok !== 1) {
            Log::warning('[bca] token: bad X-SIGNATURE');
            return self::snap('401', self::TOKEN_SERVICE, '00', 'Unauthorized. Signature');
        }
        $ttl = (int) self::cfg('token_ttl', 900);
        $token = Str::random(64);
        Cache::put('bca:token:' . hash('sha256', $token), $clientKey, $ttl);
        return self::snap('200', self::TOKEN_SERVICE, '00', 'Successful', [
            'accessToken' => $token,
            'tokenType' => 'Bearer',
            'expiresIn' => (string) $ttl,
        ]);
    }

    /** POST /openapi/v1.0/qr/qr-mpm-notify */
    public function notify(Request $r)
    {
        $s = self::NOTIFY_SERVICE;
        $auth = (string) $r->header('Authorization', '');
        $token = preg_match('/^Bearer\s+(\S+)$/i', $auth, $m) ? $m[1] : '';
        if ($token === '' || !Cache::has('bca:token:' . hash('sha256', $token))) {
            return self::snap('401', $s, '01', 'Invalid Token (B2B)');
        }
        $ts = (string) $r->header('X-TIMESTAMP', '');
        $sig = (string) $r->header('X-SIGNATURE', '');
        $extId = (string) $r->header('X-EXTERNAL-ID', '');
        foreach (['X-TIMESTAMP' => $ts, 'X-SIGNATURE' => $sig, 'X-EXTERNAL-ID' => $extId,
                  'X-PARTNER-ID' => (string) $r->header('X-PARTNER-ID', ''),
                  'CHANNEL-ID' => (string) $r->header('CHANNEL-ID', '')] as $name => $v) {
            if ($v === '') {
                return self::snap('400', $s, '02', "Invalid Mandatory Field {$name}");
            }
        }
        if (!self::validTimestamp($ts)) {
            return self::snap('400', $s, '01', 'Invalid Field Format X-TIMESTAMP');
        }
        $secret = (string) self::cfg('client_secret');
        if ($secret === '') {
            Log::error('[bca] notify: services.bca.client_secret missing');
            return self::snap('500', $s, '00', 'General Error');
        }
        $path = '/' . ltrim($r->getPathInfo(), '/');
        $stringToSign = strtoupper($r->getMethod()) . ':' . $path . ':' . $token . ':'
            . strtolower(hash('sha256', self::minifiedBody($r))) . ':' . $ts;
        $expected = base64_encode(hash_hmac('sha512', $stringToSign, $secret, true));
        if (!hash_equals($expected, $sig)) {
            Log::warning('[bca] notify: bad X-SIGNATURE');
            return self::snap('401', $s, '00', 'Unauthorized. Signature');
        }
        $partnerId = (string) self::cfg('partner_id');
        if ($partnerId !== '' && !hash_equals($partnerId, (string) $r->header('X-PARTNER-ID'))) {
            return self::snap('401', $s, '00', 'Unauthorized. Unknown partner');
        }
        // X-EXTERNAL-ID is unique per day: a second request with the same one is a conflict.
        $extKey = 'bca:ext:' . Carbon::now('Asia/Jakarta')->format('Ymd') . ':' . $extId;
        if (!Cache::add($extKey, 1, 86400)) {
            return self::snap('409', $s, '01', 'Conflict');
        }

        $in = $r->json()->all();
        $partnerRef = trim((string) ($in['originalPartnerReferenceNo'] ?? ''));
        $status = trim((string) ($in['latestTransactionStatus'] ?? ''));
        if ($partnerRef === '' || $status === '') {
            Cache::forget($extKey);
            return self::snap('400', $s, '02', 'Invalid Mandatory Field '
                . ($partnerRef === '' ? 'originalPartnerReferenceNo' : 'latestTransactionStatus'));
        }
        $amount = $in['amount']['value'] ?? null;
        $info = is_array($in['additionalInfo'] ?? null) ? $in['additionalInfo'] : [];
        $now = Carbon::now('Asia/Jakarta')->format('Y-m-d H:i:s');
        $row = [
            'reference_no' => mb_substr((string) ($in['originalReferenceNo'] ?? ''), 0, 64) ?: null,
            'status' => mb_substr($status, 0, 4),
            'status_desc' => mb_substr((string) ($in['transactionStatusDesc'] ?? ''), 0, 64) ?: null,
            'amount' => is_numeric($amount) ? (int) round((float) $amount) : null,
            'approval_code' => mb_substr((string) ($in['approvalCode'] ?? $info['approvalCode'] ?? ''), 0, 32) ?: null,
            'external_id' => mb_substr($extId, 0, 64),
            'payload' => json_encode($in, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE),
            'updated_at' => $now,
        ];
        try {
            $exists = DB::table('bca_qris_notifications')->where('partner_reference_no', $partnerRef)->exists();
            if ($exists) {
                DB::table('bca_qris_notifications')->where('partner_reference_no', $partnerRef)->update($row);
            } else {
                DB::table('bca_qris_notifications')->insert($row + [
                    'partner_reference_no' => mb_substr($partnerRef, 0, 64), 'created_at' => $now]);
            }
        } catch (\Throwable $e) {
            Cache::forget($extKey);                      // let BCA's retry through
            Log::error('[bca] notify ' . $partnerRef . ': ' . $e->getMessage());
            return self::snap('500', $s, '00', 'General Error');
        }
        Log::info("[bca] notify {$partnerRef} status {$status}");
        return self::snap('200', $s, '00', 'Successful');
    }
}
