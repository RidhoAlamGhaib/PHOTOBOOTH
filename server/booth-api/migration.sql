-- Run once in phpMyAdmin (database wwwneoph_Neophoto > SQL). Adds two nullable
-- columns; existing Laravel code keeps working because it ignores them.
ALTER TABLE `customers`
  ADD COLUMN `promo_optin` TINYINT(1) NOT NULL DEFAULT 0 AFTER `instagram`;
ALTER TABLE `orders`
  ADD COLUMN `booth_data` TEXT NULL AFTER `outlet`,
  ADD UNIQUE KEY `orders_order_uc_unique` (`order-UC`);
