-- Тестовые источники для ETL: CRM (клиенты) и телеметрия протезов. База airflow (метаданные) создаётся образом.
CREATE DATABASE crm;
CREATE DATABASE telemetry;

\c crm
CREATE TABLE clients (
    id               serial PRIMARY KEY,
    username         text NOT NULL UNIQUE, -- логин в Keycloak
    full_name        text NOT NULL,
    prosthesis_model text NOT NULL,
    issued_at        date NOT NULL
);
INSERT INTO clients (username, full_name, prosthesis_model, issued_at) VALUES
    ('user1',        'User One',      'BP-Hand v2',  '2025-03-10'),
    ('user2',        'User Two',      'BP-Hand v2',  '2025-05-22'),
    ('prothetic1',   'Prothetic One', 'BP-Arm v3',   '2025-07-01'),
    ('prothetic2',   'Prothetic Two', 'BP-Arm v3',   '2025-08-15'),
    ('prothetic3',   'Prothetic Three','BP-Hand v1', '2024-11-30'),
    ('john.doe',     'John Doe',      'BP-Arm v3',   '2025-09-05'),
    ('jane.smith',   'Jane Smith',    'BP-Hand v2',  '2025-06-18'),
    ('alex.johnson', 'Alex Johnson',  'BP-Hand v1',  '2025-01-20');

\c telemetry
CREATE TABLE events (
    client_id   int NOT NULL,
    ts          timestamptz NOT NULL,
    battery_pct int NOT NULL,
    movements   int NOT NULL,
    errors      int NOT NULL
);
CREATE INDEX ON events (ts);
-- По событию в час на клиента с 1 октября 2026 по текущий момент.
INSERT INTO events
SELECT c, g, 20 + floor(random() * 80), floor(random() * 400), CASE WHEN random() < 0.05 THEN 1 ELSE 0 END
FROM generate_series(1, 8) c, generate_series(timestamptz '2026-10-01 00:00+00', now(), interval '1 hour') g
WHERE random() < 0.9;
