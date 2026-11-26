# Bikin sticker wajah baru

Sticker = gambar PNG transparan yang ditempel otomatis ke **setiap wajah** di foto.
Ukurannya ikut besar wajah, miringnya ikut kepala. Nambah sticker **tidak perlu ngoding**:
cukup taruh PNG di folder ini dan tambah satu entri di `stickers.json`.

## 1. Siapkan gambar

| Aturan | Kenapa |
|---|---|
| **PNG dengan background transparan** | Bagian transparan tidak menutupi wajah. JPG tidak bisa. |
| Lebar **600–1000 px** | Cukup tajam untuk dicetak, tidak bikin berat. |
| **Potong ruang kosong** di sekeliling gambar (crop rapat) | Posisi dihitung dari **titik tengah gambar**; ruang kosong bikin posisinya meleset. |
| Gambar **menghadap lurus** (tidak miring) | Kemiringan ditambahkan otomatis sesuai kepala. |
| Tanpa drop shadow / glow yang melebar | Bayangan ikut dihitung sebagai bagian gambar. |
| Nama file huruf kecil, tanpa spasi (`topi_pantai.png`) | Gampang ditulis di `stickers.json`. |

Satu sticker untuk **dua sisi** (misal pipi kiri + kanan) cukup **satu gambar**: dipakai di dua tempat.
Dua kuping kelinci/kucing digambar **dalam satu PNG** (keduanya, berdampingan).

## 2. Pilih titik tempelnya (`anchor`)

Titik tengah gambar ditaruh di titik tempel:

```
              ( head )            <- di atas dahi, ±setengah lebar wajah di atas mata
          .-----------.
         /  (o)   (o)  \         <- eyes: tengah-tengah kedua mata
        |      (nose)   |        <- nose: ujung hidung
        | (cheeks)  (cheeks)     <- cheeks: dua pipi (gambar yang sama di kiri & kanan)
         \   ( mouth )  /        <- mouth: tengah mulut
          '-----------'
```

| anchor | Cocok untuk |
|---|---|
| `head` | pita, bando, kuping, topi, mahkota, hati melayang |
| `eyes` | kacamata, masker mata |
| `cheeks` | blush, stiker pipi, bintang kecil |
| `nose` | hidung kelinci/anjing, kumis |
| `mouth` | lidah, bibir, permen |

## 3. Daftarkan di `stickers.json`

Tambah satu entri di dalam `"stickers": [ ... ]` (jangan lupa koma di antara entri):

```json
{
  "id": "topi_pantai",
  "label": "Topi Pantai",
  "parts": [
    {"file": "topi_pantai.png", "anchor": "head", "width": 1.3, "dy": -0.1}
  ]
}
```

| Kolom | Isi |
|---|---|
| `id` | nama unik, huruf kecil tanpa spasi |
| `label` | tulisan di tombol (pendek, maks ±12 huruf) |
| `parts` | satu atau lebih gambar; sticker gabungan (pita + blush) = dua part |
| `file` | nama PNG di folder ini |
| `anchor` | `head` / `eyes` / `cheeks` / `nose` / `mouth` |
| `width` | lebar sticker dibanding lebar wajah. `1.0` = selebar wajah |
| `dy` | geser **naik / turun** (dalam lebar wajah). Minus = naik. `-0.1` = naik sedikit |
| `dx` | geser **kiri / kanan** (opsional). Minus = kiri |

Contoh sticker gabungan (dua bagian sekaligus):

```json
{"id": "kucing", "label": "Kucing", "parts": [
  {"file": "cat_ears.png", "anchor": "head", "width": 1.4, "dy": 0.0},
  {"file": "blush.png", "anchor": "cheeks", "width": 0.34}
]}
```

Angka awal yang biasanya pas:

| Jenis | width | dy |
|---|---|---|
| pita / jepit rambut | 0.7 – 0.9 | -0.1 – 0 |
| kuping (sepasang) | 1.2 – 1.5 | -0.3 – 0 |
| topi / mahkota | 0.9 – 1.4 | -0.2 – 0 |
| kacamata | 1.0 – 1.1 | 0 – 0.05 |
| blush / pipi | 0.3 – 0.4 | 0 |
| hidung / kumis | 0.3 – 0.6 | 0 – 0.05 |

## 4. Tes dulu di foto sendiri

Pakai foto biasa (wajah menghadap kamera, terang), di folder project:

```
python tools/test_sticker.py foto_tes.jpg topi_pantai
```

Hasilnya `sticker_test.jpg` di sebelah foto: kotak pertama menunjukkan titik wajah yang ketemu
(hijau = mata, biru = hidung, merah = mulut), lalu satu kotak per sticker.
Kurang pas? Ubah `width` / `dy` / `dx` di `stickers.json`, jalankan lagi. Tanpa nama sticker =
semua sticker dites.

Coba juga foto **ramai-ramai** dan foto **kepala miring**, biar yakin.

## 5. Pasang di mesin

1. Salin PNG + `stickers.json` yang baru ke folder `stickers` di sebelah `NeoPhoto.exe`.
2. **Tutup dan buka lagi aplikasinya** (daftar sticker dibaca sekali saat app dibuka).
3. Sticker baru muncul di kolom STICKER (layar kamera) dan panel STICKER (layar pilih frame).

## Kalau ada masalah

| Gejala | Penyebab / solusi |
|---|---|
| Sticker tidak muncul di daftar | Nama `file` salah ketik, PNG tidak ada di folder, atau `stickers.json` rusak (koma kurang / lebih). Cek log: baris `[STICKER]`. |
| Ada kotak putih / hitam di sekitar sticker | PNG tidak transparan. Export ulang dengan background transparan. |
| Posisi meleset jauh | Gambar masih punya ruang kosong di pinggir: crop rapat. Lalu atur `dy` / `dx`. |
| Sticker tidak nempel di sebagian wajah | Wajah terlalu kecil, terlalu miring/nyamping, atau gelap: tidak terdeteksi. |
| Ukuran terlalu besar / kecil | Ubah `width`. |

Set bawaan (pita, kuping kucing, kuping kelinci, kacamata hati, blush, tiara, hati) dibuat oleh
`tools/make_stickers.py`; jalankan `python tools/make_stickers.py stickers` untuk menggambar ulang
(menimpa PNG bawaan dan `stickers.json`, jadi simpan dulu sticker buatanmu).
