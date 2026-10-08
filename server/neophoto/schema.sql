-- NEO PHOTO product list. Import in cPanel > phpMyAdmin > (your database) > Import.
CREATE TABLE IF NOT EXISTS products (
  id                INT          NOT NULL PRIMARY KEY,      -- 101, 102, ...
  name              VARCHAR(100) NOT NULL,                  -- shown on the booth
  type              VARCHAR(50)  NOT NULL DEFAULT '',       -- e.g. "Strip", "Grid", "Polaroid"
  poses             INT          NOT NULL DEFAULT 4,        -- photo slots in the frame
  frame_dir         VARCHAR(150) NOT NULL DEFAULT 'frames', -- folder on the booth PC, e.g. frames/a
  cut               VARCHAR(20)  NOT NULL DEFAULT 'none',   -- "none" or "2inch"
  paper_name        VARCHAR(60)  NOT NULL DEFAULT '',
  price             INT          NOT NULL DEFAULT 0,        -- package price (Rupiah)
  free_photos       INT          NOT NULL DEFAULT 0,        -- kept photos included (0 = same as poses)
  bonus_price       INT          NOT NULL DEFAULT 0,        -- per extra kept photo above free_photos
  extra_print_price INT          NOT NULL DEFAULT 0,        -- per extra printed copy
  active            TINYINT(1)   NOT NULL DEFAULT 1,
  sort_order        INT          NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Example rows (edit or delete in the admin page).
INSERT IGNORE INTO products (id, name, type, poses, frame_dir, price, free_photos, bonus_price, extra_print_price, sort_order) VALUES
 (101, '4 Foto - Grid 2x2',           'Grid',   4, 'frames/ad', 35000, 4, 5000, 10000, 1),
 (102, '5 FOTO, Beda dari yang lain', 'Grid',   5, 'frames/a',  40000, 5, 5000, 10000, 2),
 (103, '2 Pose Besar',                'Large',  2, 'frames/b',  30000, 2, 5000, 10000, 3),
 (104, '1 Pose 1 Memori',             'Single', 1, 'frames/c',  25000, 1, 5000, 10000, 4);
