-- Sample Cloud SQL for MySQL schema for exercising tishift-cloudsql.
--
-- Every construct here is annotated with the rule it should trigger. Running
--   tishift-cloudsql convert --ddl-file sql/sample-schema.sql --dry-run
-- should fire CSQL-DDL-1 through CSQL-DDL-7 at least once each, and running
-- convert a second time over its own output must produce no new findings.

CREATE DATABASE IF NOT EXISTS tishift_demo;
USE tishift_demo;

-- Baseline: plain InnoDB, utf8mb4, no findings expected beyond CSQL-DDL-7.
CREATE TABLE customers (
  id BIGINT NOT NULL AUTO_INCREMENT,          -- CSQL-DDL-7 (single-column PK -> AUTO_RANDOM suggestion)
  email VARCHAR(255) NOT NULL,
  full_name VARCHAR(255) DEFAULT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_customers_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- CSQL-DDL-3: storage-only table options with no TiDB analog.
-- CSQL-DDL-4: legacy utf8 charset and collation.
CREATE TABLE legacy_orders (
  order_id INT NOT NULL AUTO_INCREMENT,       -- CSQL-DDL-7
  customer_id BIGINT NOT NULL,
  note TEXT CHARACTER SET utf8 COLLATE utf8_general_ci,   -- CSQL-DDL-4 (x2)
  total DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  PRIMARY KEY (order_id),
  KEY idx_customer (customer_id),
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (id)
) ENGINE=InnoDB
  ROW_FORMAT=COMPRESSED                       -- CSQL-DDL-3
  KEY_BLOCK_SIZE=8                            -- CSQL-DDL-3
  STATS_PERSISTENT=1                          -- CSQL-DDL-3
  DEFAULT CHARSET=utf8                        -- CSQL-DDL-4
  COLLATE=utf8_general_ci;                    -- CSQL-DDL-4

-- CSQL-DDL-2: MyISAM is permitted on Cloud SQL and has no TiDB equivalent.
CREATE TABLE access_log (
  log_id BIGINT NOT NULL AUTO_INCREMENT,      -- CSQL-DDL-7
  path VARCHAR(512) NOT NULL,
  hit_at DATETIME NOT NULL,
  PRIMARY KEY (log_id)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4;

-- CSQL-DDL-2 (MEMORY) plus an ENCRYPTION option (CSQL-DDL-3).
CREATE TABLE session_cache (
  session_id CHAR(36) NOT NULL,
  payload BLOB,
  PRIMARY KEY (session_id)
) ENGINE=MEMORY ENCRYPTION='N' DEFAULT CHARSET=utf8mb4;

-- CSQL-DDL-5: FULLTEXT index — kept, but a TiFlash replica is emitted.
CREATE TABLE articles (
  article_id BIGINT NOT NULL AUTO_INCREMENT,  -- CSQL-DDL-7
  title VARCHAR(255) NOT NULL,
  body LONGTEXT,
  PRIMARY KEY (article_id),
  FULLTEXT KEY ft_articles_body (title, body)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- CSQL-DDL-6 + BLOCKER-4: spatial index and spatial column types.
CREATE TABLE store_locations (
  store_id INT NOT NULL AUTO_INCREMENT,       -- CSQL-DDL-7
  name VARCHAR(128) NOT NULL,
  coords POINT NOT NULL SRID 4326,
  service_area POLYGON DEFAULT NULL,
  PRIMARY KEY (store_id),
  SPATIAL KEY sp_store_coords (coords)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- A composite primary key: CSQL-DDL-7 must NOT fire here, because AUTO_RANDOM
-- cannot replace the leading column of a composite key.
CREATE TABLE order_items (
  order_id INT NOT NULL,
  line_no INT NOT NULL AUTO_INCREMENT,
  sku VARCHAR(64) NOT NULL,
  qty INT NOT NULL DEFAULT 1,
  PRIMARY KEY (order_id, line_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- CSQL-DDL-1: DEFINER naming a Cloud SQL principal, on a view.
CREATE DEFINER=`cloudsqlsuperuser`@`%` SQL SECURITY DEFINER VIEW active_customers AS
  SELECT id, email, full_name FROM customers WHERE created_at > '2024-01-01';

-- CSQL-DDL-1 + BLOCKER-1: DEFINER naming an IAM service account, on a procedure.
CREATE DEFINER=`svc-migrate@example-project.iam.gserviceaccount.com`@`%`
PROCEDURE recalc_totals()
BEGIN
  UPDATE legacy_orders o
     SET total = (SELECT COALESCE(SUM(qty), 0) FROM order_items i WHERE i.order_id = o.order_id);
END;

-- CSQL-DDL-1 + BLOCKER-2: trigger with a DEFINER.
CREATE DEFINER=`cloudsqladmin`@`localhost` TRIGGER trg_orders_touch
BEFORE INSERT ON legacy_orders
FOR EACH ROW SET NEW.total = COALESCE(NEW.total, 0);

-- BLOCKER-3: scheduled event.
CREATE DEFINER=`appuser`@`%` EVENT ev_purge_sessions
ON SCHEDULE EVERY 1 DAY
DO DELETE FROM session_cache WHERE session_id = '';

-- Idempotency probe: text that looks like a rule trigger but lives inside a
-- string literal must never match. Convert must leave this row untouched.
CREATE TABLE quoting_edge_cases (
  id INT NOT NULL AUTO_INCREMENT,
  description VARCHAR(255) DEFAULT 'ENGINE=MyISAM ROW_FORMAT=COMPRESSED DEFINER=`x`@`y`',
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
