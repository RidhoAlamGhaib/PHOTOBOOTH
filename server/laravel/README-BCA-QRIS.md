# BCA QRIS (SNAP) di website Laravel

BCA memanggil dua endpoint di `neophotoindonesia.my.id`:

| URL yang didaftarkan ke BCA | Fungsi |
|---|---|
| `https://neophotoindonesia.my.id/openapi/v1.0/access-token/b2b` | **URL Production OAuth**: BCA minta access token (OAuth & Signature v2.0) |
| `https://neophotoindonesia.my.id/openapi/v1.0/qr/qr-mpm-notify` | **URL Production services**: BCA kirim notifikasi pembayaran QRIS (SNAP QRIS Notification v2.1) |

Setiap pembayaran yang dilaporkan BCA masuk ke tabel `bca_qris_notifications`. Kolom
`partner_reference_no` = nomor order dari mesin booth (`partnerReferenceNo` waktu QR dibuat).

## 1. File

| Dari repo PHOTOBOOTH | Ke project Laravel |
|---|---|
| `server/laravel/app/Http/Controllers/BcaQrisController.php` | `app/Http/Controllers/BcaQrisController.php` |
| isi `server/laravel/routes/bca.php` | akhir `routes/web.php` (baris `use` taruh di atas file) |

## 2. Tabel

Jalankan `server/booth-api/migration_004_bca_qris.sql` di phpMyAdmin. Isinya cuma bikin tabel baru
`bca_qris_notifications`; tabel lain tidak disentuh. Aman dijalankan dua kali.

## 3. Setting

`config/services.php`, di dalam array yang di-return:

```php
'bca' => [
    'client_id'     => env('BCA_SNAP_CLIENT_ID', ''),      // X-CLIENT-KEY yang dipakai BCA
    'client_secret' => env('BCA_SNAP_CLIENT_SECRET', ''),  // kunci HMAC notifikasi
    'public_key'    => env('BCA_SNAP_PUBLIC_KEY', ''),     // public key BCA (PEM, atau path file .pem)
    'partner_id'    => env('BCA_SNAP_PARTNER_ID', ''),     // opsional: cek header X-PARTNER-ID
    'token_ttl'     => 900,                                // detik
],
```

`.env`:

```
BCA_SNAP_CLIENT_ID=...
BCA_SNAP_CLIENT_SECRET=...
BCA_SNAP_PUBLIC_KEY=/home/USER/bca_public.pem
BCA_SNAP_PARTNER_ID=
```

- `BCA_SNAP_PUBLIC_KEY` paling gampang diisi path file `.pem` di luar folder `public_html`.
  Bisa juga isi PEM satu baris dengan `\n` sebagai pengganti enter.
- Client ID / secret dan public key-nya dari BCA (portal developer / tim onboarding BCA). Jangan
  di-commit ke GitHub.

Habis itu:

```
php artisan config:clear
php artisan route:clear
php artisan route:list --path=openapi
```

Harus muncul 2 route `POST openapi/v1.0/...`.

## 4. CSRF

BCA tidak kirim token CSRF. Route di `routes/bca.php` sudah melewati cek CSRF. Kalau tes di bawah masih
dapat **419 Page Expired**, tambahkan pengecualian manual:

- Laravel 11 ke atas, `bootstrap/app.php`:
  ```php
  ->withMiddleware(function ($middleware) {
      $middleware->validateCsrfTokens(except: ['openapi/*']);
  })
  ```
- Laravel 10 ke bawah, `app/Http/Middleware/VerifyCsrfToken.php`:
  ```php
  protected $except = ['openapi/*'];
  ```

## 5. Anti-bot hosting & Cloudflare (WAJIB)

Server BCA bukan browser, jadi tidak bisa lolos halaman "One moment, please..." (Imunify360) atau
challenge Cloudflare. Kalau ini tidak diatur, BCA selalu gagal dan pembayaran tidak tercatat.

- **Hosting (Imunify360):** minta whitelist **IP server BCA** (minta daftar IP callback ke BCA).
  Hosting bilang cuma bisa whitelist IP, dan ini memang IP, jadi bisa.
- **Cloudflare:** Security > WAF > Custom rules > buat rule *Skip* untuk
  `URI Path starts with "/openapi/"` (skip Bot Fight Mode / managed challenge / rate limit).

## 6. Tes

Dari PC mana pun:

```
curl -X POST https://neophotoindonesia.my.id/openapi/v1.0/access-token/b2b -H "Content-Type: application/json" -d "{\"grantType\":\"client_credentials\"}"
```

| Hasil | Arti |
|---|---|
| `{"responseCode":"4007302","responseMessage":"Invalid Mandatory Field X-CLIENT-KEY"}` | Benar: endpoint jalan, cuma header BCA tidak dikirim |
| `419 Page Expired` | CSRF belum dikecualikan (langkah 4) |
| HTML "One moment, please" / Cloudflare | Anti-bot masih menghadang (langkah 5) |
| `404` | Route belum ditempel / `route:clear` |
| `5007300` / `5005200` | Setting `bca` kosong atau salah; cek `storage/logs/laravel.log` (baris `[bca]`) |

Setelah itu minta BCA kirim notifikasi tes dari sandbox / simulator mereka, lalu cek tabel
`bca_qris_notifications`.

## Cara kerja

**Token** (`access-token/b2b`)
- Header dari BCA: `X-TIMESTAMP`, `X-CLIENT-KEY`, `X-SIGNATURE`. Body: `{"grantType":"client_credentials"}`.
- `X-SIGNATURE` dicek dengan **SHA256withRSA** atas `X-CLIENT-KEY + "|" + X-TIMESTAMP`, pakai public key BCA.
- Balasan: `{"responseCode":"2007300","accessToken":"...","tokenType":"Bearer","expiresIn":"900"}`.
  Token disimpan di cache Laravel selama 15 menit.

**Notifikasi** (`qr-mpm-notify`)
- Header: `Authorization: Bearer <token di atas>`, `X-TIMESTAMP`, `X-SIGNATURE`, `X-PARTNER-ID`,
  `X-EXTERNAL-ID`, `CHANNEL-ID`.
- `X-SIGNATURE` dicek dengan **HMAC-SHA512** (kunci: client secret) atas
  `POST:/openapi/v1.0/qr/qr-mpm-notify:<token>:<sha256 hex body yang di-minify>:<X-TIMESTAMP>`.
- `X-EXTERNAL-ID` yang sama dua kali di hari yang sama ditolak `409`.
- Data yang disimpan: `originalPartnerReferenceNo`, `originalReferenceNo`, `latestTransactionStatus`
  (`00` = sukses), `transactionStatusDesc`, `amount.value`, `approvalCode`, dan seluruh JSON di `payload`.
- Balasan sukses: `{"responseCode":"2005200","responseMessage":"Successful"}`.

| responseCode | Arti |
|---|---|
| `2007300` / `2005200` | Sukses (token / notifikasi) |
| `4007301` / `4005201` | Format field salah (mis. `X-TIMESTAMP`) |
| `4007302` / `4005202` | Field / header wajib kosong |
| `4017300` / `4015200` | Signature salah atau client tidak dikenal |
| `4015201` | Token (B2B) tidak valid / kedaluwarsa |
| `4095201` | `X-EXTERNAL-ID` dobel |
| `5007300` / `5005200` | Error server / setting |

## Sesuai dokumen BCA

Dicek ke [developer.bca.co.id/id/Dokumentasi](https://developer.bca.co.id/id/Dokumentasi), bagian
"OAuth2.0 (SNAP)", "Signature (SNAP)", "Headers (SNAP)" dan "QRIS MPM > SNAP QRIS Notification":

- Signature token: `SHA256withRSA(client_ID + "|" + X-TIMESTAMP)`, base64. Gagal: `4017300 Unathorized. [Signature]`.
- Signature notifikasi: `HMAC-SHA512(ClientSecret, METHOD:RelativeUrl:AccessToken:lowercase(hex(sha256(minify(body)))):Timestamp)`.
- Token berlaku 900 detik, respons `2007300` dengan `tokenType: "bearer"`.
- Notifikasi: service code `52`, sukses `2005200`, `X-EXTERNAL-ID` dobel `4095200`, header `CHANNEL-ID` `95251`,
  `X-PARTNER-ID` = Kode Grup merchant. `approvalCode`, `issuerName`, `transactionDate` ada di `additionalInfo`.
- **Notifikasi belum tersedia di sandbox BCA**: tes notifikasi hanya bisa di production / dengan bantuan tim BCA.

## Kunci yang dipakai (jangan tertukar)

| Arah | Siapa tanda tangan | Kunci di sisi kita |
|---|---|---|
| Mesin booth → BCA (generate / query QR) | mesin, private key di `NeoPhoto.py` (`BCA_PRIVATE_KEY_PEM`) | kirim **public key** pasangannya (format x.509 PEM) ke BCA |
| BCA → website (token + notifikasi) | BCA, private key BCA | **public key BCA** di `BCA_SNAP_PUBLIC_KEY`, client id/secret di `.env` |

Client ID / secret untuk arah BCA → website disepakati waktu onboarding dengan BCA; isi ke
`BCA_SNAP_CLIENT_ID` / `BCA_SNAP_CLIENT_SECRET`.

## Mesin booth

`NeoPhoto.py` (bagian `BCA_*`) sudah mengikuti dokumen yang sama: `qrOption: "C"` supaya BCA mengirim
isi QR (bawaannya cuma gambar), `additionalInfo.terminalId` di inquiry, `validityPeriod` minimal 6 menit,
`CHANNEL-ID` `95251`, production `https://api.klikbca.com` (`BCA_IS_PRODUCTION = True`).
Pakai dengan `config.json`: `"payment": {"mode": "qris", "qris_provider": "bca"}`.
Mesin tetap cek status lewat inquiry ke BCA; tabel notifikasi jadi catatan cadangan di website.
