-- Run once in phpMyAdmin (database wwwneoph_Neophoto > SQL). Adds columns with
-- defaults; existing Laravel code keeps working because it ignores them.
ALTER TABLE `customers`
  ADD COLUMN `promo_optin` TINYINT(1) NOT NULL DEFAULT 0 AFTER `instagram`;
ALTER TABLE `orders`
  ADD COLUMN `bonus_photos`  INT NOT NULL DEFAULT 0 AFTER `outlet`,  -- paid photos above the free limit
  ADD COLUMN `bonus_amount`  INT NOT NULL DEFAULT 0 AFTER `bonus_photos`,
  ADD COLUMN `extra_prints`  INT NOT NULL DEFAULT 0 AFTER `bonus_amount`,  -- sheets on top of the 1 included
  ADD COLUMN `extra_amount`  INT NOT NULL DEFAULT 0 AFTER `extra_prints`,
  ADD COLUMN `booth_data` TEXT NULL AFTER `extra_amount`,
  ADD UNIQUE KEY `orders_order_uc_unique` (`order-UC`);
