-- Only if you already ran migration.sql before: adds orders.machine (photo machine
-- name / number). Fresh installs get it from migration.sql.
ALTER TABLE `orders`
  ADD COLUMN `machine` VARCHAR(60) NULL AFTER `outlet`,
  ADD KEY `orders_machine_index` (`machine`);
