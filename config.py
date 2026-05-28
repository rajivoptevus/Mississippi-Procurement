# config.py
# PostgreSQL connection parameters (from Azure ConnectionStrings)

# DB_HOST = "rfpwyer-postgresql.postgres.database.azure.com"
# DB_PORT = 5432
# DB_NAME = "masterwyber"
# DB_USER = "rfppgadmin"
# DB_PASSWORD = "H@Sh1CoR3!"

# config.py
DB_HOST = "rfpwyer-postgresql.postgres.database.azure.com"
DB_PORT = 5432
DB_NAME = "masterwyber"
DB_USER = "XXXXXXX"
DB_PASSWORD = "XXXXXXX"

# Azure PostgreSQL requires SSL
DB_SSLMODE = "require"

# Optional: Connection pooling parameters (for reference)
POOLING = True
MIN_POOL_SIZE = 5
MAX_POOL_SIZE = 100
CONNECTION_LIFETIME = 300
COMMAND_TIMEOUT = 30