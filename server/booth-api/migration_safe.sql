-- NEO PHOTO booth columns for the LIVE database (wwwneoph_Neophoto).
-- Only ADDs columns/indexes: existing rows and the purchase app keep working
-- (every new column has a default). Safe to run more than once (IF NOT EXISTS).
-- phpMyAdmin > database wwwneoph_Neophoto > SQL > paste > Go.

ALTER TABLE `customers`
  ADD COLUMN IF NOT EXISTS `promo_optin` TINYINT(1) NOT NULL DEFAULT 0 AFTER `instagram`;

ALTER TABLE `orders`
  ADD COLUMN IF NOT EXISTS `machine`      VARCHAR(60) NULL AFTER `outlet`,
  ADD COLUMN IF NOT EXISTS `bonus_photos` INT NOT NULL DEFAULT 0 AFTER `machine`,
  ADD COLUMN IF NOT EXISTS `bonus_amount` INT NOT NULL DEFAULT 0 AFTER `bonus_photos`,
  ADD COLUMN IF NOT EXISTS `extra_prints` INT NOT NULL DEFAULT 0 AFTER `bonus_amount`,
  ADD COLUMN IF NOT EXISTS `extra_amount` INT NOT NULL DEFAULT 0 AFTER `extra_prints`,
  ADD COLUMN IF NOT EXISTS `booth_data`   TEXT NULL AFTER `extra_amount`;

-- Faster lookups by session id / machine. A normal (not UNIQUE) index, so existing
-- orders with repeated `order-UC` values can't make this fail.
ALTER TABLE `orders`
  ADD INDEX IF NOT EXISTS `orders_order_uc_index` (`order-UC`),
  ADD INDEX IF NOT EXISTS `orders_machine_index` (`machine`);
