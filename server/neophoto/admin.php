<?php
// NEO PHOTO product admin: add / edit / delete products (101, 102, ...).
require __DIR__ . '/config.php';
session_start();

function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }

// One-time helper to create the password hash (only while none is set).
if (ADMIN_PASSWORD_HASH === '') {
    header('Content-Type: text/plain; charset=utf-8');
    if (isset($_GET['hash']) && $_GET['hash'] !== '') {
        echo "Paste this into ADMIN_PASSWORD_HASH in config.php:\n\n" . password_hash($_GET['hash'], PASSWORD_DEFAULT) . "\n";
    } else {
        echo "Set ADMIN_PASSWORD_HASH in config.php first.\nOpen admin.php?hash=YOUR_PASSWORD once to generate it.\n";
    }
    exit;
}

if (empty($_SESSION['csrf'])) { $_SESSION['csrf'] = bin2hex(random_bytes(16)); }
$csrf = $_SESSION['csrf'];
$msg = '';

if (isset($_POST['login'])) {
    if (password_verify($_POST['password'] ?? '', ADMIN_PASSWORD_HASH)) {
        session_regenerate_id(true);
        $_SESSION['ok'] = true;
    } else {
        usleep(800000);
        $msg = 'Password salah';
    }
}
if (isset($_GET['logout'])) { session_destroy(); header('Location: admin.php'); exit; }

$fields = ['id','name','type','poses','frame_dir','cut','paper_name','price','free_photos',
           'bonus_price','extra_print_price','active','sort_order'];
$ints = ['id','poses','price','free_photos','bonus_price','extra_print_price','active','sort_order'];

if (!empty($_SESSION['ok']) && $_SERVER['REQUEST_METHOD'] === 'POST' && !isset($_POST['login'])) {
    if (!hash_equals($csrf, $_POST['csrf'] ?? '')) { http_response_code(400); exit('Bad request'); }
    try {
        if (isset($_POST['delete'])) {
            db()->prepare('DELETE FROM products WHERE id = ?')->execute([(int)$_POST['delete']]);
            $msg = 'Produk dihapus';
        } else {
            $v = [];
            foreach ($fields as $f) {
                $x = trim($_POST[$f] ?? '');
                $v[$f] = in_array($f, $ints, true) ? (int)$x : $x;
            }
            $v['active'] = isset($_POST['active']) ? 1 : 0;
            if ($v['id'] <= 0 || $v['name'] === '' || $v['poses'] <= 0) {
                $msg = 'ID, nama, dan pose wajib diisi';
            } else {
                $cols = implode(',', $fields);
                $ph = implode(',', array_fill(0, count($fields), '?'));
                $upd = implode(',', array_map(fn($f) => "$f=VALUES($f)", array_slice($fields, 1)));
                db()->prepare("INSERT INTO products ($cols) VALUES ($ph) ON DUPLICATE KEY UPDATE $upd")
                    ->execute(array_values($v));
                $msg = 'Produk ' . $v['id'] . ' disimpan';
            }
        }
    } catch (Throwable $e) {
        $msg = 'Gagal: ' . $e->getMessage();
    }
}
?><!doctype html>
<html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NEO PHOTO Produk</title>
<style>
 body{font:15px/1.5 system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#fbf6f8;color:#141114}
 .wrap{max-width:1100px;margin:0 auto;padding:24px 16px}
 h1{font-size:24px;margin:0 0 16px}
 table{border-collapse:collapse;width:100%;background:#fff;font-size:14px}
 th,td{border-bottom:1px solid #eadfe4;padding:8px;text-align:left;vertical-align:top}
 th{background:#fff0f5;font-size:12px;text-transform:uppercase;letter-spacing:.05em}
 .tbl{overflow-x:auto;border:1px solid #eadfe4;border-radius:10px}
 form.edit{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;background:#fff;
   padding:16px;border:1px solid #eadfe4;border-radius:10px;margin:20px 0}
 label{display:grid;gap:4px;font-size:13px;font-weight:600}
 input{font:inherit;padding:8px;border:1px solid #d9c9d3;border-radius:8px}
 button{font:inherit;font-weight:700;padding:9px 16px;border:0;border-radius:8px;background:#e0457f;color:#fff;cursor:pointer}
 button.light{background:#fff;color:#141114;border:1px solid #d9c9d3}
 .msg{padding:10px 14px;background:#ffe3ee;border-radius:8px;margin-bottom:14px}
 .num{text-align:right;font-variant-numeric:tabular-nums}
 .top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
</style></head><body><div class="wrap">
<?php if (empty($_SESSION['ok'])): ?>
  <h1>NEO PHOTO Produk</h1>
  <?php if ($msg): ?><div class="msg"><?= h($msg) ?></div><?php endif; ?>
  <form method="post" style="display:flex;gap:10px;max-width:420px">
    <input type="password" name="password" placeholder="Password admin" required style="flex:1" autofocus>
    <button name="login" value="1">Masuk</button>
  </form>
<?php else:
  $rows = db()->query('SELECT * FROM products ORDER BY sort_order, id')->fetchAll();
  $edit = null;
  if (isset($_GET['edit'])) {
      $st = db()->prepare('SELECT * FROM products WHERE id = ?'); $st->execute([(int)$_GET['edit']]); $edit = $st->fetch();
  }
  $e = $edit ?: ['id'=>'','name'=>'','type'=>'','poses'=>4,'frame_dir'=>'frames/','cut'=>'none','paper_name'=>'',
                 'price'=>0,'free_photos'=>0,'bonus_price'=>0,'extra_print_price'=>0,'active'=>1,'sort_order'=>0];
?>
  <div class="top"><h1>NEO PHOTO Produk</h1><a href="?logout=1">Keluar</a></div>
  <?php if ($msg): ?><div class="msg"><?= h($msg) ?></div><?php endif; ?>
  <div class="tbl"><table>
    <tr><th>ID</th><th>Nama</th><th>Jenis</th><th class="num">Pose</th><th>Folder frame</th>
        <th class="num">Harga</th><th class="num">Foto gratis</th><th class="num">Bonus/foto</th>
        <th class="num">Extra print</th><th>Aktif</th><th></th></tr>
    <?php foreach ($rows as $r): ?>
    <tr><td><?= h($r['id']) ?></td><td><?= h($r['name']) ?></td><td><?= h($r['type']) ?></td>
      <td class="num"><?= h($r['poses']) ?></td><td><?= h($r['frame_dir']) ?></td>
      <td class="num"><?= number_format($r['price'],0,',','.') ?></td>
      <td class="num"><?= $r['free_photos'] ?: h($r['poses']) ?></td>
      <td class="num"><?= number_format($r['bonus_price'],0,',','.') ?></td>
      <td class="num"><?= number_format($r['extra_print_price'],0,',','.') ?></td>
      <td><?= $r['active'] ? 'Ya' : 'Tidak' ?></td>
      <td style="white-space:nowrap"><a href="?edit=<?= (int)$r['id'] ?>">Edit</a>
        <form method="post" style="display:inline" onsubmit="return confirm('Hapus produk <?= (int)$r['id'] ?>?')">
          <input type="hidden" name="csrf" value="<?= h($csrf) ?>">
          <button class="light" name="delete" value="<?= (int)$r['id'] ?>">Hapus</button></form></td></tr>
    <?php endforeach; ?>
  </table></div>

  <form class="edit" method="post">
    <input type="hidden" name="csrf" value="<?= h($csrf) ?>">
    <label>ID (101, 102, …)<input name="id" type="number" min="1" value="<?= h($e['id']) ?>" required></label>
    <label>Nama<input name="name" value="<?= h($e['name']) ?>" required></label>
    <label>Jenis<input name="type" value="<?= h($e['type']) ?>"></label>
    <label>Jumlah pose<input name="poses" type="number" min="1" value="<?= h($e['poses']) ?>" required></label>
    <label>Folder frame di booth<input name="frame_dir" value="<?= h($e['frame_dir']) ?>" placeholder="frames/a"></label>
    <label>Cut (none / 2inch)<input name="cut" value="<?= h($e['cut']) ?>"></label>
    <label>Paper name (opsional)<input name="paper_name" value="<?= h($e['paper_name']) ?>"></label>
    <label>Harga paket (Rp)<input name="price" type="number" min="0" value="<?= h($e['price']) ?>"></label>
    <label>Foto gratis (0 = sama dgn pose)<input name="free_photos" type="number" min="0" value="<?= h($e['free_photos']) ?>"></label>
    <label>Harga bonus / foto (Rp)<input name="bonus_price" type="number" min="0" value="<?= h($e['bonus_price']) ?>"></label>
    <label>Harga extra print / lembar (Rp)<input name="extra_print_price" type="number" min="0" value="<?= h($e['extra_print_price']) ?>"></label>
    <label>Urutan tampil<input name="sort_order" type="number" value="<?= h($e['sort_order']) ?>"></label>
    <label style="align-self:end;display:flex;gap:8px;align-items:center"><input type="checkbox" name="active" <?= $e['active'] ? 'checked' : '' ?>> Aktif</label>
    <div style="align-self:end;display:flex;gap:8px"><button>Simpan</button><a href="admin.php" style="align-self:center">Baru</a></div>
  </form>
  <p style="font-size:13px;color:#6b6168">Booth membaca daftar ini dari <b>products.php</b> tiap beberapa menit. Folder frame harus sudah ada di PC booth.</p>
<?php endif; ?>
</div></body></html>
