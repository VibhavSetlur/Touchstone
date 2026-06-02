-- Sample schema for the Touchstone postgres-quickstart example.
-- Realistic-shaped data so the profiler / PII detector have something to chew on.

CREATE TABLE customers (
    customer_id  BIGSERIAL PRIMARY KEY,
    email        VARCHAR(255) NOT NULL UNIQUE,
    full_name    VARCHAR(255) NOT NULL,
    phone        VARCHAR(32),
    address      TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login   TIMESTAMPTZ,
    is_active    BOOLEAN NOT NULL DEFAULT true,
    tier         VARCHAR(16) NOT NULL DEFAULT 'standard'
);

CREATE TABLE orders (
    order_id     BIGSERIAL PRIMARY KEY,
    customer_id  BIGINT NOT NULL REFERENCES customers(customer_id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    status       VARCHAR(16) NOT NULL,
    currency     VARCHAR(3)  NOT NULL,
    total_amount NUMERIC(12, 2) NOT NULL
);

CREATE TABLE order_items (
    item_id      BIGSERIAL PRIMARY KEY,
    order_id     BIGINT NOT NULL REFERENCES orders(order_id),
    sku          VARCHAR(64) NOT NULL,
    quantity     INTEGER NOT NULL,
    unit_price   NUMERIC(12, 2) NOT NULL
);

CREATE TABLE payments (
    payment_id   BIGSERIAL PRIMARY KEY,
    order_id     BIGINT NOT NULL REFERENCES orders(order_id),
    method       VARCHAR(16) NOT NULL,
    -- Last 4 only; full PAN never persisted.
    card_last4   VARCHAR(4),
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    amount       NUMERIC(12, 2) NOT NULL
);

INSERT INTO customers (email, full_name, phone, address, tier) VALUES
    ('jane.doe@example.com',  'Jane Doe',   '+15551112233', '1 Main St, Springfield', 'gold'),
    ('john.smith@example.com','John Smith', '+15552223344', '42 Elm Ave, Riverside',  'standard'),
    ('alice@example.com',     'Alice Liu',  '+15553334455', '7 Oak Ln, Lakeview',     'standard'),
    ('bob@example.com',       'Bob Chen',   NULL,           NULL,                     'platinum');

INSERT INTO orders (customer_id, status, currency, total_amount) VALUES
    (1, 'paid',     'USD', 129.99),
    (1, 'paid',     'USD',  39.50),
    (2, 'shipped',  'EUR',  89.00),
    (3, 'pending',  'USD',  12.00),
    (4, 'paid',     'GBP', 410.75);

INSERT INTO order_items (order_id, sku, quantity, unit_price) VALUES
    (1, 'BOOK-001', 1, 29.99),
    (1, 'BOOK-002', 1, 49.99),
    (1, 'BOOK-003', 1, 49.99),
    (2, 'PEN-RED',  5,  7.90),
    (3, 'BOOK-001', 1, 89.00),
    (4, 'COFFEE',   2,  6.00),
    (5, 'CHAIR',    1, 410.75);

INSERT INTO payments (order_id, method, card_last4, amount) VALUES
    (1, 'card',    '4242', 129.99),
    (2, 'card',    '4242',  39.50),
    (3, 'paypal',  NULL,    89.00),
    (5, 'card',    '0005', 410.75);

ANALYZE;
