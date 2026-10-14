-- Run this script as a MySQL administrator when Docker is not used.
CREATE DATABASE IF NOT EXISTS moyo_memory
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'moyo'@'localhost' IDENTIFIED BY 'moyo_password';
GRANT ALL PRIVILEGES ON moyo_memory.* TO 'moyo'@'localhost';
FLUSH PRIVILEGES;

