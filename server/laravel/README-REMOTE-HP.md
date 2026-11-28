# Remote HP (foto dari HP customer)

Untuk ruangan yang layar mesinnya di luar (mis. room high angle). Customer scan QR di layar
persiapan pakai kamera HP, halaman remote terbuka di website (tanpa install aplikasi), lalu
foto dari dalam ruangan:

- **FOTO**: hitung mundur singkat (Settings: "Remote HP: hitung mundur", bawaan 5 detik), lalu jepret.
  Tidak ada timer menunggu: mesin diam sampai FOTO ditekan lagi.
- Hasil tiap foto langsung muncul di HP (versi kecil, sudah dengan beauty & sticker).
- Foto sampai **maks. foto per sesi** (bawaan 20). **SELESAI** aktif setelah jumlah foto minimal
  (jumlah slot frame). Setelah itu customer keluar dan memilih foto di layar mesin.
- Kunci acak beda tiap sesi. HP pertama yang membuka link yang pegang kendali; HP lain ditolak.
  Begitu sesi selesai / batal, link mati dan foto kecil di website dihapus (paling lama 1 jam).
- Layar sentuh mesin tetap bisa dipakai (tombol FOTO / SELESAI) kalau HP mati atau tidak scan.
- Pengaman: kalau tidak ada yang ditekan selama "Remote HP: selesai sendiri kalau diam"
  (bawaan 15 menit), mesin lanjut sendiri.

## Pasang di website

| Dari repo PHOTOBOOTH | Ke project Laravel |
|---|---|
| `server/laravel/app/Http/Controllers/BoothRemoteController.php` | `app/Http/Controllers/` |
| `server/laravel/resources/views/booth/remote.blade.php` | `resources/views/booth/` |
| isi `server/laravel/routes/booth.php` (bagian "Remote HP") | `routes/web.php` |

Tidak perlu tabel baru: data sementara disimpan di cache Laravel (`CACHE_STORE`, bawaan `file`
atau `database`). Pakai `BOOTH_API_KEY` yang sama dengan endpoint booth lain.

Lalu `php artisan route:clear` dan `php artisan view:clear`.

**Penting, `throttle`:** route di `routes/booth.php` sekarang punya nama sendiri
(`throttle:60,1,booth-coupon`, dst). Tanpa nama, semua route yang di-throttle berbagi satu hitungan
per IP; HP yang cek status tiap detik (apalagi kalau HP customer pakai WiFi outlet, IP-nya sama
dengan mesin) bisa menghabiskan jatah dan cek kupon / tombol SELESAI ditolak (429). Ganti baris
route booth yang lama dengan yang baru.

Cloudflare / Imunify360: halaman `/r/...` dibuka browser HP biasa, jadi lolos seperti pengunjung lain.

## Tes

1. Settings mesin → Sesi foto → **Remote HP: Ya** (hanya di mesin room itu).
2. Mulai sesi → di layar persiapan muncul QR di kiri tombol MULAI FOTO.
3. Scan dengan HP → halaman "NEO PHOTO · REMOTE" → tekan FOTO.
4. Log mesin: `[REMOTE] open …`, `[REMOTE] phone connected`, `[REMOTE] phone: shoot`,
   `[REMOTE] finished with N photo(s)`, `[REMOTE] close …`.

| Masalah | Penyebab |
|---|---|
| Tidak ada QR, log `[REMOTE] cannot open` | controller / route belum dipasang, atau `BOOTH_SITE_URL` kosong |
| HP: "Remote sudah dipakai" | link sudah dibuka HP lain duluan (pakai HP yang pertama) |
| HP: "Sesi sudah selesai" | sesi sudah selesai / batal, atau lebih dari 1 jam |
| Tombol di HP tidak berpengaruh, log mesin tanpa `phone: shoot` | lihat `storage/logs/laravel.log`; 429 = throttle (lihat di atas) |
