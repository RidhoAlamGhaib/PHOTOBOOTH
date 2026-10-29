-- Coupon mode: mark an order_items code as used when the customer presses MULAI
-- on the booth. Only adds two empty columns; existing rows are untouched. Safe to run twice.
ALTER TABLE `order_items`
  ADD COLUMN IF NOT EXISTS `used_at` TIMESTAMP NULL DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS `used_machine` VARCHAR(60) NULL DEFAULT NULL;
