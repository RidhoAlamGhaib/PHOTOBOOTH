# BCA QRIS (SNAP) di website Laravel

BCA memanggil dua endpoint di `neophotoindonesia.my.id`:

| URL yang didaftarkan ke BCA | Fungsi |
|---|---|
| `https://neophotoindonesia.my.id/openapi/v1.0/access-token/b2b` | **URL Production OAuth**: BCA minta access token (OAuth & Signature v2.0) |
| `https://neophotoindonesia.my.id/openapi/v1.0/qr/qr-mpm-notify` | **URL Production services**: BCA kirim notifikasi pembayaran QRIS (SNAP QRIS Notification v2.1) |

Dua URL di atas **sudah persis** sama dengan yang diminta BCA (domain + endpoint sesuai doktek
SNAP QRIS Notification v2.1 dan OAuth & Signature v2.0). Route-nya ada di `routes/bca.php`.

## Checklist go-live

| # | Apa | Di mana | Status |
|---|---|---|---|
| 1 | URL Production OAuth + services didaftarkan ke BCA | form BCA | URL di tabel atas |
| 2 | Controller + route + tabel di-deploy | langkah 1, 2, 4 di bawah | |
| 3 | `.env` website: client id/secret + public key BCA (BCA → website) | langkah 3 | dari BCA |
| 4 | Cloudflare / Imunify360 tidak menghadang `/openapi/*` | langkah 5 | |
| 5 | Tes curl: balasan JSON `4007302` | langkah 6 | |
| 6 | **Merchant ID** → `BCA_MERCHANT_ID` di `NeoPhoto.py` (mesin) | lokal, jangan di-commit | sudah dapat |
| 7 | **Terminal ID** → `BCA_TERMINAL_ID` di `NeoPhoto.py`; kalau tiap mesin punya Terminal ID sendiri: Settings admin → Pembayaran → "Terminal ID BCA mesin ini" | lokal / Settings | sudah dapat |
| 8 | Client ID / secret mesin → BCA (`BCA_CLIENT_ID`, `BCA_CLIENT_SECRET`), Kode Grup → `BCA_PARTNER_ID`, private key → `BCA_PRIVATE_KEY_PEM`; public key pasangannya dikirim ke BCA | lokal | dari BCA |
| 9 | `BCA_IS_PRODUCTION = True`, build ulang exe | lokal | |
| 10 | `config.json` / Settings: Penyedia QRIS = BCA | Settings admin | |

Cek di log mesin waktu sesi bayar dibuka: `[QRIS-BCA] live production https://api.klikbca.com
merchant=... terminal=...`. Kalau tertulis `MOCK`, salah satu dari client id, secret, private key,
Merchant ID atau Terminal ID masih kosong (QR yang muncul QR demo, bukan pembayaran asli).

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

Dicek ke dokumen teknis BCA **QRIS Notification v2.1** dan **OpenAPI OAuth & Signature v2.0**:

- Signature token: `SHA256withRSA(client_ID + "|" + X-TIMESTAMP)`, base64. Gagal: `4017300 Unathorized. [Signature]`.
- Signature notifikasi: `HMAC-SHA512(ClientSecret, METHOD:RelativeUrl:AccessToken:lowercase(hex(sha256(minify(body)))):Timestamp)`.
- Token berlaku 900 detik, respons `2007300` dengan `tokenType: "bearer"`.
- Notifikasi: service code `52`, sukses `2005200`, `X-EXTERNAL-ID` dobel `4095200`, header `CHANNEL-ID` `95251`,
  `X-PARTNER-ID` = Kode Grup merchant. `approvalCode`, `issuerName`, `transactionDate` ada di `additionalInfo`.
- **Notifikasi belum tersedia di sandbox BCA**: tes notifikasi hanya bisa di production / dengan bantuan tim BCA.
- Signature dicek dari body **persis seperti yang dikirim BCA** (cuma spasi di luar string yang dibuang),
  tidak di-encode ulang, sesuai "Important Notice" di dokumen OAuth & Signature v2.0.
- BCA mengulang notifikasi kalau dapat HTTP 401, 500, 503 atau 504 (maks 4 kali). Kalau database
  gagal, website membalas 500 supaya BCA mengirim ulang.

## Kunci yang dipakai (jangan tertukar)

| Arah | Siapa tanda tangan | Kunci di sisi kita |
|---|---|---|
| Mesin booth → BCA (generate / query QR) | mesin, private key di `NeoPhoto.py` (`BCA_PRIVATE_KEY_PEM`) | kirim **public key** pasangannya (format x.509 PEM) ke BCA |
| BCA → website (token + notifikasi) | BCA, private key BCA | **public key BCA** di `BCA_SNAP_PUBLIC_KEY`, client id/secret di `.env` |

Client ID / secret untuk arah BCA → website disepakati waktu onboarding dengan BCA; isi ke
`BCA_SNAP_CLIENT_ID` / `BCA_SNAP_CLIENT_SECRET`.

## Mesin booth

`NeoPhoto.py` (bagian `BCA_*`) dicek ke dokumen teknis BCA: **SNAP QR MPM Generate QR v2.4**,
**QR MPM Inquiry v2.4**, **QRIS Notification v2.1**, **OpenAPI OAuth & Signature v2.0**.

| Aturan BCA | Di mesin |
|---|---|
| URL UAT `https://devapi.klikbca.com`, production `https://api.klikbca.com` | `BCA_IS_PRODUCTION = False` = UAT, `True` = production |
| `partnerReferenceNo` alfanumerik, maks 64 | nomor order tanpa tanda `-`: `NEO-20261127083000-ab12` dikirim sebagai `NEO20261127083000ab12` |
| `X-EXTERNAL-ID` angka 32 / 36 digit, unik per hari | 32 digit (waktu ms + urutan + acak) |
| `validityPeriod` minimal +5 menit, maks +120 menit | hitung mundur layar bayar (min 5 menit) + 20 detik |
| `qrOption: "C"` supaya respons berisi isi QR (`qrContent`) | dipakai |
| Inquiry wajib `originalReferenceNo`, `serviceCode` `47`, `merchantId`, `additionalInfo.terminalId` | dikirim semua |
| Inquiry `2005100` + `latestTransactionStatus`: `00` lunas, `03` belum, `04` refund, `06` gagal | dipakai; status hanya dipercaya kalau `2005100` |
| Inquiry `403 4035100` = QR kedaluwarsa | dianggap kedaluwarsa (gagal) |
| Inquiry `5005100` dll (error sistem, walau ada `06`) | diabaikan, cek lagi 2 detik kemudian |

Setelah hitung mundur habis, layar bayar menyembunyikan QR dan masih **cek status 45 detik** lagi
(sampai QR BCA benar-benar kedaluwarsa), jadi tamu yang bayar di detik terakhir tetap lanjut foto.

Di tabel `bca_qris_notifications`, `partner_reference_no` berisi nomor order **tanpa `-`**
(seperti yang dikirim ke BCA). Mencocokkan dengan order di website: hapus `-` dari nomor order dulu.

Refund (QR MPM Refund v2.3, `/openapi/v1.0/qr/qr-mpm-refund`) **belum** dipakai mesin; refund lewat BCA.

Pakai dengan `config.json` / Settings: Cara mulai sesi = Bayar QRIS, Penyedia QRIS = BCA.
Mesin tetap cek status lewat inquiry ke BCA; tabel notifikasi jadi catatan cadangan di website.
