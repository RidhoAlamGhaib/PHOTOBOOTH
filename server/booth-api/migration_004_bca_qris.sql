-- BCA SNAP QRIS: one row per QRIS payment BCA reports to /openapi/v1.0/qr/qr-mpm-notify.
-- partner_reference_no = the booth's order id (partnerReferenceNo sent when the QR was made).
-- New table only; nothing existing is touched. Safe to run twice.
CREATE TABLE IF NOT EXISTS `bca_qris_notifications` (
  `id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `partner_reference_no` varchar(64) NOT NULL,
  `reference_no` varchar(64) DEFAULT NULL,
  `status` varchar(4) NOT NULL,
  `status_desc` varchar(64) DEFAULT NULL,
  `amount` int(11) DEFAULT NULL,
  `approval_code` varchar(32) DEFAULT NULL,
  `external_id` varchar(64) DEFAULT NULL,
  `payload` text DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `bca_qris_partner_ref_unique` (`partner_reference_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
