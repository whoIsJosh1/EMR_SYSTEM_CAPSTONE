-- ============================================================
-- EMR SYSTEM v2 — DUAL BARANGAY DATABASES
-- Run this script TWICE, once for each barangay:
--   1. Change DB name to emr_veinte_reales
--   2. Change DB name to emr_dalandanan
-- Each barangay has its own standalone database.
-- ============================================================
--
-- NOTE:
-- This version creates BOTH databases in one script.
-- Therefore, if you run this exact combined script, run it ONCE.
-- ============================================================


-- ============================================================
-- DATABASE 1: BARANGAY VEINTE REALES
-- ============================================================
CREATE DATABASE IF NOT EXISTS emr_veinte_reales
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE emr_veinte_reales;

-- USERS TABLE
-- Mga account ng Admin at BHW
CREATE TABLE IF NOT EXISTS users (
    user_id         INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(150) NOT NULL,
    email           VARCHAR(150) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    role ENUM('admin','bhw','midwife','doctor','nurse') NOT NULL DEFAULT 'bhw',
    position        VARCHAR(100) NULL,
    status          ENUM('active','inactive','locked') NOT NULL DEFAULT 'active',
    is_first_login  TINYINT(1) NOT NULL DEFAULT 1,
    failed_attempts INT NOT NULL DEFAULT 0,
    locked_until    DATETIME NULL,
    last_login      DATETIME NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- PATIENT TABLE
CREATE TABLE IF NOT EXISTS patient (
    patient_id          INT AUTO_INCREMENT PRIMARY KEY,
    last_name           VARCHAR(100) NOT NULL,
    first_name          VARCHAR(100) NOT NULL,
    middle_name         VARCHAR(100) NULL,
    birthdate           DATE NOT NULL,
    sex                 ENUM('Male','Female') NOT NULL,
    civil_status        ENUM('Single','Married','Widowed','Separated') NULL,
    address             TEXT NOT NULL,
    contact_number      VARCHAR(20) NULL,
    philhealth_no       VARCHAR(30) NULL,
    occupation          VARCHAR(150) NULL,
    mother_name         VARCHAR(150) NULL,
    father_name         VARCHAR(150) NULL,
    guardian_contact    VARCHAR(20) NULL,
    is_archived         TINYINT(1) NOT NULL DEFAULT 0,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE INDEX idx_patient_name ON patient (last_name, first_name);

-- HEALTH PROBLEMS TABLE
-- Allergies, chronic diseases — for patient records only, NOT for case counting
CREATE TABLE IF NOT EXISTS health_problems (
    problem_id          INT AUTO_INCREMENT PRIMARY KEY,
    patient_id          INT NOT NULL,
    allergies           TEXT NULL,
    has_asthma          TINYINT(1) NOT NULL DEFAULT 0,
    chronic_diseases    TEXT NULL,
    other_concerns      TEXT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE,
    UNIQUE KEY uq_patient_health (patient_id)
) ENGINE=InnoDB;

-- PREGNANCY TABLE (Female patients only)
CREATE TABLE IF NOT EXISTS pregnancy (
    pregnancy_id        INT AUTO_INCREMENT PRIMARY KEY,
    patient_id          INT NOT NULL,
    status              ENUM('Pregnant','Delivered','Miscarriage','Unknown') NOT NULL DEFAULT 'Pregnant',
    gravida             INT NULL COMMENT 'Number of pregnancies',
    para                INT NULL COMMENT 'Number of deliveries',
    lmp                 DATE NULL COMMENT 'Last Menstrual Period',
    expected_due_date   DATE NULL,
    delivery_date       DATE NULL,
    delivery_type       ENUM('Normal','CS','Assisted') NULL,
    birth_outcome       VARCHAR(200) NULL,
    prenatal_visits     INT NOT NULL DEFAULT 0,
    last_prenatal_date  DATE NULL,
    attending_physician VARCHAR(150) NULL,
    remarks             TEXT NULL,
    user_id             INT NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users(user_id)
) ENGINE=InnoDB;

CREATE INDEX idx_pregnancy_patient ON pregnancy (patient_id);

-- MEDICAL RECORDS TABLE
CREATE TABLE IF NOT EXISTS medical_records (
    record_id           INT AUTO_INCREMENT PRIMARY KEY,
    patient_id          INT NOT NULL,
    visit_date          DATE NOT NULL,
    chief_complaint     VARCHAR(500) NULL,
    symptoms            TEXT NULL,
    diagnosis           TEXT NULL,
    treatment           TEXT NULL,
    blood_pressure      VARCHAR(20) NULL,
    temperature         DECIMAL(4,1) NULL,
    weight_kg           DECIMAL(5,2) NULL,
    height_cm           DECIMAL(5,2) NULL,
    heart_rate          INT NULL,
    respiratory_rate    INT NULL,
    lmp                 DATE NULL COMMENT 'Female patients only',
    follow_up_required BOOLEAN NOT NULL DEFAULT FALSE,
    follow_up_date DATE DEFAULT NULL,
    notes               TEXT NULL,
    user_id             INT NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users(user_id)
) ENGINE=InnoDB;

CREATE INDEX idx_record_patient   ON medical_records (patient_id);
CREATE INDEX idx_record_visitdate ON medical_records (visit_date);

-- IMMUNIZATION TABLE
CREATE TABLE IF NOT EXISTS immunization (
    immunization_id     INT AUTO_INCREMENT PRIMARY KEY,
    patient_id          INT NOT NULL,
    vaccine_name        VARCHAR(150) NOT NULL,
    date_given          DATE NOT NULL,
    dose_number         INT NULL DEFAULT 1,
    administered_by     VARCHAR(150) NULL,
    batch_number        VARCHAR(50) NULL,
    next_schedule       DATE NULL,
    remarks             TEXT NULL,
    user_id             INT NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users(user_id)
) ENGINE=InnoDB;

CREATE INDEX idx_immun_patient ON immunization (patient_id);

-- DISEASE TABLE (master list)
CREATE TABLE IF NOT EXISTS disease (
    disease_id      INT AUTO_INCREMENT PRIMARY KEY,
    disease_name    VARCHAR(200) NOT NULL UNIQUE,
    icd_code        VARCHAR(20) NULL,
    category        VARCHAR(100) NULL,
    is_notifiable   TINYINT(1) NOT NULL DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- DISEASE CASES TABLE (auto-counted from diagnosis field)
CREATE TABLE IF NOT EXISTS disease_cases (
    case_id             INT AUTO_INCREMENT PRIMARY KEY,
    disease_id          INT NOT NULL,
    patient_id          INT NULL,
    date_recorded       DATE NOT NULL,
    number_of_cases     INT NOT NULL DEFAULT 1,
    remarks             TEXT NULL,
    user_id             INT NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (disease_id)  REFERENCES disease(disease_id),
    FOREIGN KEY (patient_id)  REFERENCES patient(patient_id) ON DELETE SET NULL,
    FOREIGN KEY (user_id)     REFERENCES users(user_id)
) ENGINE=InnoDB;

CREATE INDEX idx_cases_disease ON disease_cases (disease_id);
CREATE INDEX idx_cases_date    ON disease_cases (date_recorded);

-- AUDIT LOG TABLE (Login/Logout only)
CREATE TABLE IF NOT EXISTS audit_log (
    log_id      INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NULL,
    action      VARCHAR(100) NOT NULL,
    ip_address  VARCHAR(45) NULL,
    user_agent  VARCHAR(500) NULL,
    date_time   DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB;


-- ============================================================
-- INVENTORY MODULE
-- ============================================================

-- INVENTORY ITEMS TABLE
CREATE TABLE IF NOT EXISTS inventory_items (
    item_id          INT AUTO_INCREMENT PRIMARY KEY,
    item_code        VARCHAR(50) NOT NULL UNIQUE,
    item_name        VARCHAR(150) NOT NULL,
    category         ENUM('Medicine', 'Vaccine', 'Medical Supply') NOT NULL,
    description      TEXT NULL,
    unit             VARCHAR(50) NOT NULL,
    current_stock    INT NOT NULL DEFAULT 0,
    reorder_level    INT NOT NULL DEFAULT 10,
    batch_number     VARCHAR(100) NULL,
    expiration_date  DATE NULL,
    is_active        TINYINT(1) NOT NULL DEFAULT 1,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
                     ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE INDEX idx_inventory_item_name
    ON inventory_items (item_name);

CREATE INDEX idx_inventory_category
    ON inventory_items (category);


-- ============================================================
-- MEDICAL RECORD PRESCRIPTIONS
-- Doctor prescriptions for medicines / vaccines / supplies
-- ============================================================

CREATE TABLE IF NOT EXISTS medical_record_prescriptions (
    prescription_id INT AUTO_INCREMENT PRIMARY KEY,

    record_id INT NOT NULL,
    item_id INT NOT NULL,

    quantity INT NOT NULL,
    instructions TEXT NULL,

    status ENUM('pending', 'dispensed', 'cancelled')
        NOT NULL DEFAULT 'pending',

    dispensed_quantity INT NOT NULL DEFAULT 0,
    dispensed_by INT NULL,
    dispensed_at DATETIME NULL,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_mrp_record_id (record_id),
    INDEX idx_mrp_item_id (item_id),
    INDEX idx_mrp_dispensed_by (dispensed_by),

    CONSTRAINT fk_mrp_record
        FOREIGN KEY (record_id)
        REFERENCES medical_records(record_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_mrp_item
        FOREIGN KEY (item_id)
        REFERENCES inventory_items(item_id),

    CONSTRAINT fk_mrp_dispensed_by
        FOREIGN KEY (dispensed_by)
        REFERENCES users(user_id)
);


-- INVENTORY TRANSACTIONS TABLE
CREATE TABLE IF NOT EXISTS inventory_transactions (
    transaction_id   INT AUTO_INCREMENT PRIMARY KEY,
    item_id          INT NOT NULL,

    transaction_type ENUM(
        'import',
        'stock_in',
        'dispense',
        'adjustment'
    ) NOT NULL,

    quantity         INT NOT NULL,
    stock_before     INT NOT NULL,
    stock_after      INT NOT NULL,

    remarks          TEXT NULL,
    user_id          INT NULL,

    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (item_id)
        REFERENCES inventory_items(item_id)
        ON DELETE CASCADE,

    FOREIGN KEY (user_id)
        REFERENCES users(user_id)
) ENGINE=InnoDB;

CREATE INDEX idx_inventory_transaction_item
    ON inventory_transactions (item_id);

CREATE INDEX idx_inventory_transaction_date
    ON inventory_transactions (created_at);


-- ============================================================
-- SEED DATA FOR VEINTE REALES
-- ============================================================

INSERT INTO disease (disease_name, icd_code, category, is_notifiable) VALUES
('Influenza',                   'J11',   'Communicable',     0),
('Dengue Fever',                'A90',   'Communicable',     1),
('Tuberculosis',                'A15',   'Communicable',     1),
('COVID-19',                    'U07.1', 'Communicable',     1),
('Hypertension',                'I10',   'Non-communicable', 0),
('Diabetes Mellitus',           'E11',   'Non-communicable', 0),
('Pneumonia',                   'J18',   'Communicable',     1),
('Diarrhea',                    'A09',   'Communicable',     0),
('Leptospirosis',               'A27',   'Communicable',     1),
('Typhoid Fever',               'A01',   'Communicable',     1),
('Acute Respiratory Infection', 'J06',   'Communicable',     0),
('Chickenpox',                  'B01',   'Communicable',     0),
('Measles',                     'B05',   'Communicable',     1),
('Asthma',                      'J45',   'Non-communicable', 0),
('Malnutrition',                'E46',   'Non-communicable', 1);

-- Default admin (password: Admin@123 — run reset_admin_password.py after setup)
INSERT INTO users (name, email, password_hash, role, position, status)
VALUES ('System Administrator', 'admin@vientereales.gov.ph',
        '$2b$12$placeholder_run_reset_script', 'admin', 'Health Information Officer', 'active');


-- ============================================================
-- DATABASE 2: BARANGAY DALANDANAN
-- Same structure, separate database
-- ============================================================
CREATE DATABASE IF NOT EXISTS emr_dalandanan
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE emr_dalandanan;

CREATE TABLE IF NOT EXISTS users (
    user_id         INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(150) NOT NULL,
    email           VARCHAR(150) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    role ENUM('admin','bhw','midwife','doctor','nurse') NOT NULL DEFAULT 'bhw',
    position        VARCHAR(100) NULL,
    status          ENUM('active','inactive','locked') NOT NULL DEFAULT 'active',
    is_first_login  TINYINT(1) NOT NULL DEFAULT 1,
    failed_attempts INT NOT NULL DEFAULT 0,
    locked_until    DATETIME NULL,
    last_login      DATETIME NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS patient (
    patient_id          INT AUTO_INCREMENT PRIMARY KEY,
    last_name           VARCHAR(100) NOT NULL,
    first_name          VARCHAR(100) NOT NULL,
    middle_name         VARCHAR(100) NULL,
    birthdate           DATE NOT NULL,
    sex                 ENUM('Male','Female') NOT NULL,
    civil_status        ENUM('Single','Married','Widowed','Separated') NULL,
    address             TEXT NOT NULL,
    contact_number      VARCHAR(20) NULL,
    philhealth_no       VARCHAR(30) NULL,
    occupation          VARCHAR(150) NULL,
    mother_name         VARCHAR(150) NULL,
    father_name         VARCHAR(150) NULL,
    guardian_contact    VARCHAR(20) NULL,
    is_archived         TINYINT(1) NOT NULL DEFAULT 0,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE INDEX idx_patient_name_d ON patient (last_name, first_name);

CREATE TABLE IF NOT EXISTS health_problems (
    problem_id          INT AUTO_INCREMENT PRIMARY KEY,
    patient_id          INT NOT NULL,
    allergies           TEXT NULL,
    has_asthma          TINYINT(1) NOT NULL DEFAULT 0,
    chronic_diseases    TEXT NULL,
    other_concerns      TEXT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE,
    UNIQUE KEY uq_patient_health_d (patient_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS pregnancy (
    pregnancy_id        INT AUTO_INCREMENT PRIMARY KEY,
    patient_id          INT NOT NULL,
    status              ENUM('Pregnant','Delivered','Miscarriage','Unknown') NOT NULL DEFAULT 'Pregnant',
    gravida             INT NULL,
    para                INT NULL,
    lmp                 DATE NULL,
    expected_due_date   DATE NULL,
    delivery_date       DATE NULL,
    delivery_type       ENUM('Normal','CS','Assisted') NULL,
    birth_outcome       VARCHAR(200) NULL,
    prenatal_visits     INT NOT NULL DEFAULT 0,
    last_prenatal_date  DATE NULL,
    attending_physician VARCHAR(150) NULL,
    remarks             TEXT NULL,
    user_id             INT NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users(user_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS medical_records (
    record_id           INT AUTO_INCREMENT PRIMARY KEY,
    patient_id          INT NOT NULL,
    visit_date          DATE NOT NULL,
    chief_complaint     VARCHAR(500) NULL,
    symptoms            TEXT NULL,
    diagnosis           TEXT NULL,
    treatment           TEXT NULL,
    blood_pressure      VARCHAR(20) NULL,
    temperature         DECIMAL(4,1) NULL,
    weight_kg           DECIMAL(5,2) NULL,
    height_cm           DECIMAL(5,2) NULL,
    heart_rate          INT NULL,
    respiratory_rate    INT NULL,
    lmp                 DATE NULL,
    notes               TEXT NULL,
    user_id             INT NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users(user_id)
) ENGINE=InnoDB;


CREATE TABLE IF NOT EXISTS immunization (
    immunization_id     INT AUTO_INCREMENT PRIMARY KEY,
    patient_id          INT NOT NULL,
    vaccine_name        VARCHAR(150) NOT NULL,
    date_given          DATE NOT NULL,
    dose_number         INT NULL DEFAULT 1,
    administered_by     VARCHAR(150) NULL,
    batch_number        VARCHAR(50) NULL,
    next_schedule       DATE NULL,
    remarks             TEXT NULL,
    user_id             INT NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users(user_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS disease (
    disease_id      INT AUTO_INCREMENT PRIMARY KEY,
    disease_name    VARCHAR(200) NOT NULL UNIQUE,
    icd_code        VARCHAR(20) NULL,
    category        VARCHAR(100) NULL,
    is_notifiable   TINYINT(1) NOT NULL DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS disease_cases (
    case_id             INT AUTO_INCREMENT PRIMARY KEY,
    disease_id          INT NOT NULL,
    patient_id          INT NULL,
    date_recorded       DATE NOT NULL,
    number_of_cases     INT NOT NULL DEFAULT 1,
    remarks             TEXT NULL,
    user_id             INT NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (disease_id) REFERENCES disease(disease_id),
    FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE SET NULL,
    FOREIGN KEY (user_id)    REFERENCES users(user_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS audit_log (
    log_id      INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NULL,
    action      VARCHAR(100) NOT NULL,
    ip_address  VARCHAR(45) NULL,
    user_agent  VARCHAR(500) NULL,
    date_time   DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB;


-- ============================================================
-- INVENTORY MODULE
-- ============================================================

-- INVENTORY ITEMS TABLE
CREATE TABLE IF NOT EXISTS inventory_items (
    item_id          INT AUTO_INCREMENT PRIMARY KEY,
    item_code        VARCHAR(50) NOT NULL UNIQUE,
    item_name        VARCHAR(150) NOT NULL,
    category         ENUM('Medicine', 'Vaccine', 'Medical Supply') NOT NULL,
    description      TEXT NULL,
    unit             VARCHAR(50) NOT NULL,
    current_stock    INT NOT NULL DEFAULT 0,
    reorder_level    INT NOT NULL DEFAULT 10,
    batch_number     VARCHAR(100) NULL,
    expiration_date  DATE NULL,
    is_active        TINYINT(1) NOT NULL DEFAULT 1,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
                     ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE INDEX idx_inventory_item_name
    ON inventory_items (item_name);

CREATE INDEX idx_inventory_category
    ON inventory_items (category);


-- ============================================================
-- MEDICAL RECORD PRESCRIPTIONS
-- Doctor prescriptions for medicines / vaccines / supplies
-- ============================================================

CREATE TABLE IF NOT EXISTS medical_record_prescriptions (
    prescription_id INT AUTO_INCREMENT PRIMARY KEY,

    record_id INT NOT NULL,
    item_id INT NOT NULL,

    quantity INT NOT NULL,
    instructions TEXT NULL,

    status ENUM('pending', 'dispensed', 'cancelled')
        NOT NULL DEFAULT 'pending',

    dispensed_quantity INT NOT NULL DEFAULT 0,
    dispensed_by INT NULL,
    dispensed_at DATETIME NULL,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_mrp_record_id (record_id),
    INDEX idx_mrp_item_id (item_id),
    INDEX idx_mrp_dispensed_by (dispensed_by),

    CONSTRAINT fk_mrp_record
        FOREIGN KEY (record_id)
        REFERENCES medical_records(record_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_mrp_item
        FOREIGN KEY (item_id)
        REFERENCES inventory_items(item_id),

    CONSTRAINT fk_mrp_dispensed_by
        FOREIGN KEY (dispensed_by)
        REFERENCES users(user_id)
);


-- INVENTORY TRANSACTIONS TABLE
CREATE TABLE IF NOT EXISTS inventory_transactions (
    transaction_id   INT AUTO_INCREMENT PRIMARY KEY,
    item_id          INT NOT NULL,

    transaction_type ENUM(
        'import',
        'stock_in',
        'dispense',
        'adjustment'
    ) NOT NULL,

    quantity         INT NOT NULL,
    stock_before     INT NOT NULL,
    stock_after      INT NOT NULL,

    remarks          TEXT NULL,
    user_id          INT NULL,

    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (item_id)
        REFERENCES inventory_items(item_id)
        ON DELETE CASCADE,

    FOREIGN KEY (user_id)
        REFERENCES users(user_id)
) ENGINE=InnoDB;

CREATE INDEX idx_inventory_transaction_item
    ON inventory_transactions (item_id);

CREATE INDEX idx_inventory_transaction_date
    ON inventory_transactions (created_at);


INSERT INTO disease (disease_name, icd_code, category, is_notifiable) VALUES
('Influenza',                   'J11',   'Communicable',     0),
('Dengue Fever',                'A90',   'Communicable',     1),
('Tuberculosis',                'A15',   'Communicable',     1),
('COVID-19',                    'U07.1', 'Communicable',     1),
('Hypertension',                'I10',   'Non-communicable', 0),
('Diabetes Mellitus',           'E11',   'Non-communicable', 0),
('Pneumonia',                   'J18',   'Communicable',     1),
('Diarrhea',                    'A09',   'Communicable',     0),
('Leptospirosis',               'A27',   'Communicable',     1),
('Typhoid Fever',               'A01',   'Communicable',     1),
('Acute Respiratory Infection', 'J06',   'Communicable',     0),
('Chickenpox',                  'B01',   'Communicable',     0),
('Measles',                     'B05',   'Communicable',     1),
('Asthma',                      'J45',   'Non-communicable', 0),
('Malnutrition',                'E46',   'Non-communicable', 1);

INSERT INTO users (name, email, password_hash, role, position, status)
VALUES ('System Administrator', 'admin@dalandanan.gov.ph',
        '$2b$12$placeholder_run_reset_script', 'admin', 'Health Information Officer', 'active');

SELECT 'Both databases created successfully!' AS status;


-- ============================================================
-- MIGRATION: Add is_first_login column to users table
-- Run this in MySQL before restarting the server
-- ============================================================

-- NOTE:
-- is_first_login is now included directly in the users table
-- above, so the ALTER TABLE statements from the old migration
-- are no longer needed.

USE emr_veinte_reales;
ALTER TABLE users ADD COLUMN is_first_login TINYINT(1) NOT NULL DEFAULT 1 AFTER status;
UPDATE users SET is_first_login = 0 WHERE role = 'admin';

USE emr_dalandanan;
ALTER TABLE users ADD COLUMN is_first_login TINYINT(1) NOT NULL DEFAULT 1 AFTER status;
UPDATE users SET is_first_login = 0 WHERE role = 'admin';

SELECT 'Migration complete!' AS status;


-- ============================================================
-- MIGRATION: Update role ENUM to include midwife and doctor
-- Run this BEFORE restarting the server
-- ============================================================

-- NOTE:
-- The role ENUM already includes admin, bhw, midwife, doctor,
-- and nurse in both users tables above.
-- Therefore, the old ALTER TABLE migration is no longer needed.

USE emr_veinte_reales;
ALTER TABLE users MODIFY COLUMN role ENUM('admin','bhw','midwife','doctor','nurse') NOT NULL DEFAULT 'bhw';

USE emr_dalandanan;
ALTER TABLE users MODIFY COLUMN role ENUM('admin','bhw','midwife','doctor','nurse') NOT NULL DEFAULT 'bhw';

SELECT 'Role enum migration complete!' AS status;