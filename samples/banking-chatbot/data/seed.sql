CREATE TABLE customers (
    customer_id BIGINT PRIMARY KEY,
    display_name TEXT NOT NULL,
    annual_income_cents BIGINT NOT NULL,
    current_card TEXT NOT NULL
);
CREATE TABLE transactions (
    transaction_id BIGINT PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(customer_id),
    category TEXT NOT NULL,
    amount_cents BIGINT NOT NULL CHECK (amount_cents > 0)
);
ALTER TABLE customers REPLICA IDENTITY FULL;
ALTER TABLE transactions REPLICA IDENTITY FULL;

INSERT INTO customers VALUES
    (1, 'Alex', 9000000, 'basic'),
    (2, 'Sam', 4500000, 'basic'),
    (3, 'Taylor', 2000000, 'none');
INSERT INTO transactions VALUES
    (1, 1, 'travel', 120000),
    (2, 1, 'travel', 80000),
    (3, 1, 'dining', 20000),
    (4, 2, 'groceries', 60000),
    (5, 2, 'groceries', 40000),
    (6, 2, 'dining', 10000);
