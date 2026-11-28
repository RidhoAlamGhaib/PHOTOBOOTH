<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Str;

/**
 * NEO PHOTO "remote HP": the guest scans a QR on the booth screen and shoots
 * from their phone (no app, just this website). One random key per session;
 * the booth closes it when the session ends (and it expires after an hour).
 *
 * Booth (through the hidden bridge page, X-Api-Key + CSRF, like BoothController):
 *   POST /booth/remote/open  {key, machine, max, min}   -> start, returns the phone URL
 *   POST /booth/remote/sync  {key, ack, state, photo?}  -> booth state + last photo up,
 *                                                         phone commands (after ack) down
 *   POST /booth/remote/close {key}                      -> end: key and photos deleted
 * Phone (normal web page):
 *   GET  /r/{key}            the remote page; the first phone that opens it owns it
 *   GET  /r/{key}/state      booth state (JSON)
 *   POST /r/{key}/cmd        {"cmd": "shoot" | "finish"}
 *   GET  /r/{key}/photo/{n}  photo n (small JPEG, only while the session is open)
 *
 * Everything lives in the Laravel cache (no table needed).
 */
class BoothRemoteController extends Controller
{
    private const TTL = 3600;            // seconds a session may stay open
    private const MAX_PHOTO = 700000;    // bytes of one phone-size JPEG

    private function authorized(Request $r): bool
    {
        $key = (string) config('services.booth.key', '');
        return $key !== '' && hash_equals($key, (string) $r->header('X-Api-Key', ''));
    }

    private static function validKey($key): bool
    {
        return is_string($key) && preg_match('/^[A-Za-z0-9_-]{24,64}$/', $key) === 1;
    }

    private static function ck(string $key, string $part = ''): string
    {
        return 'neo_remote:' . $key . $part;
    }

    private static function cookieName(string $key): string
    {
        return 'neo_r_' . substr(hash('sha256', $key), 0, 16);
    }

    // ------------------------------------------------------------------ booth

    public function open(Request $r)
    {
        if (!$this->authorized($r)) {
            return response()->json(['error' => 'unauthorized'], 401);
        }
        $key = $r->input('key');
        if (!self::validKey($key)) {
            return response()->json(['error' => 'bad key'], 422);
        }
        Cache::put(self::ck($key), [
            'machine' => mb_substr((string) $r->input('machine', ''), 0, 40),
            'max' => max(1, min(60, (int) $r->input('max', 20))),
            'min' => max(1, min(60, (int) $r->input('min', 1))),
            'opened' => time(),
        ], self::TTL);
        Cache::put(self::ck($key, ':state'), ['phase' => 'ready', 'message' => 'Siap!', 'taken' => 0], self::TTL);
        Cache::put(self::ck($key, ':seq'), 0, self::TTL);
        Cache::put(self::ck($key, ':photos'), 0, self::TTL);
        Log::info('[remote] open ' . substr($key, 0, 6) . '… ' . (string) $r->input('machine', ''));
        return response()->json(['ok' => true, 'url' => url('/r/' . $key)]);
    }

    public function sync(Request $r)
    {
        if (!$this->authorized($r)) {
            return response()->json(['error' => 'unauthorized'], 401);
        }
        $key = $r->input('key');
        if (!self::validKey($key) || !Cache::has(self::ck($key))) {
            return response()->json(['error' => 'closed'], 404);
        }
        $state = $r->input('state');
        if (is_array($state)) {
            $clean = [];
            foreach (['phase' => 20, 'message' => 160] as $f => $len) {
                $clean[$f] = mb_substr((string) ($state[$f] ?? ''), 0, $len);
            }
            foreach (['taken', 'max', 'min', 'count'] as $f) {
                $clean[$f] = (int) ($state[$f] ?? 0);
            }
            foreach (['can_shoot', 'can_finish'] as $f) {
                $clean[$f] = (bool) ($state[$f] ?? false);
            }
            Cache::put(self::ck($key, ':state'), $clean, self::TTL);
        }
        $photo = $r->input('photo');
        if (is_array($photo) && isset($photo['n'], $photo['jpeg'])) {
            $n = (int) $photo['n'];
            $bytes = base64_decode((string) $photo['jpeg'], true);
            if ($n >= 1 && $n <= 60 && $bytes !== false && strlen($bytes) <= self::MAX_PHOTO
                && strncmp($bytes, "\xFF\xD8", 2) === 0) {
                Cache::put(self::ck($key, ':p' . $n), base64_encode($bytes), self::TTL);
                Cache::put(self::ck($key, ':photos'), max($n, (int) Cache::get(self::ck($key, ':photos'), 0)), self::TTL);
            }
        }
        $ack = max(0, (int) $r->input('ack', 0));
        $last = (int) Cache::get(self::ck($key, ':seq'), 0);
        $cmds = [];
        for ($i = $ack + 1; $i <= $last && count($cmds) < 20; $i++) {
            $c = Cache::get(self::ck($key, ':c' . $i));
            if ($c) {
                $cmds[] = ['seq' => $i, 'cmd' => $c];
            }
        }
        return response()->json(['ok' => true, 'cmds' => $cmds,
            'phone' => Cache::has(self::ck($key, ':owner'))]);
    }

    public function close(Request $r)
    {
        if (!$this->authorized($r)) {
            return response()->json(['error' => 'unauthorized'], 401);
        }
        $key = $r->input('key');
        if (self::validKey($key)) {
            self::forget($key);
        }
        return response()->json(['ok' => true]);
    }

    private static function forget(string $key): void
    {
        $seq = (int) Cache::get(self::ck($key, ':seq'), 0);
        $photos = (int) Cache::get(self::ck($key, ':photos'), 0);
        for ($i = 1; $i <= $seq; $i++) {
            Cache::forget(self::ck($key, ':c' . $i));
        }
        for ($i = 1; $i <= max($photos, 60); $i++) {
            Cache::forget(self::ck($key, ':p' . $i));
        }
        foreach (['', ':state', ':seq', ':photos', ':owner'] as $part) {
            Cache::forget(self::ck($key, $part));
        }
    }

    // ------------------------------------------------------------------ phone

    /** True when this browser is the phone that claimed the session. */
    private static function isOwner(Request $r, string $key): bool
    {
        $owner = Cache::get(self::ck($key, ':owner'));
        $mine = (string) $r->cookie(self::cookieName($key), '');
        return is_string($owner) && $mine !== '' && hash_equals($owner, hash('sha256', $mine));
    }

    public function page(Request $r, string $key)
    {
        $headers = ['Cache-Control' => 'no-store', 'X-Robots-Tag' => 'noindex'];
        if (!self::validKey($key) || !Cache::has(self::ck($key))) {
            return response()->view('booth.remote', ['key' => null, 'reason' => 'closed'], 404)->withHeaders($headers);
        }
        if (self::isOwner($r, $key)) {
            return response()->view('booth.remote', ['key' => $key, 'reason' => null])->withHeaders($headers);
        }
        // First phone to open the link owns it; anyone else gets "already in use".
        $secret = Str::random(40);
        if (Cache::add(self::ck($key, ':owner'), hash('sha256', $secret), self::TTL)) {
            return response()->view('booth.remote', ['key' => $key, 'reason' => null])->withHeaders($headers)
                ->cookie(self::cookieName($key), $secret, 120, '/', null, $r->isSecure(), true, false, 'lax');
        }
        return response()->view('booth.remote', ['key' => null, 'reason' => 'taken'], 403)->withHeaders($headers);
    }

    public function state(Request $r, string $key)
    {
        if (!self::validKey($key) || !Cache::has(self::ck($key))) {
            return response()->json(['phase' => 'closed'], 404);
        }
        if (!self::isOwner($r, $key)) {
            return response()->json(['error' => 'not yours'], 403);
        }
        $state = Cache::get(self::ck($key, ':state'), []);
        $state['photos'] = (int) Cache::get(self::ck($key, ':photos'), 0);
        return response()->json($state)->header('Cache-Control', 'no-store');
    }

    public function cmd(Request $r, string $key)
    {
        if (!self::validKey($key) || !Cache::has(self::ck($key))) {
            return response()->json(['phase' => 'closed'], 404);
        }
        if (!self::isOwner($r, $key)) {
            return response()->json(['error' => 'not yours'], 403);
        }
        $cmd = (string) $r->input('cmd', '');
        if (!in_array($cmd, ['shoot', 'finish'], true)) {
            return response()->json(['error' => 'bad cmd'], 422);
        }
        $seq = (int) Cache::increment(self::ck($key, ':seq'));
        Cache::put(self::ck($key, ':c' . $seq), $cmd, self::TTL);
        return response()->json(['ok' => true, 'seq' => $seq]);
    }

    public function photo(Request $r, string $key, int $n)
    {
        if (!self::validKey($key) || !self::isOwner($r, $key)) {
            abort(404);
        }
        $b64 = Cache::get(self::ck($key, ':p' . $n));
        if (!$b64) {
            abort(404);
        }
        return response(base64_decode($b64), 200, [
            'Content-Type' => 'image/jpeg', 'Cache-Control' => 'private, no-store']);
    }
}
