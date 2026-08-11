import os

from sqlalchemy import text
from sqlmodel import SQLModel, Session, create_engine

from sqlalchemy import event

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./panel.db")
ENGINE_KWARGS = {"connect_args": {"check_same_thread": False}} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, **ENGINE_KWARGS)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

NEW_NODE_COLUMNS = {
    "domain": "TEXT",
    "email_from": "TEXT",
    "dkim_selector": "TEXT",
    "dkim_dns_record": "TEXT",
    "dmarc_dns_record": "TEXT",
    "bootstrap_status": "TEXT",
    "bootstrap_log": "TEXT",
    "agent_token": "TEXT",
    "agent_status": "TEXT",
    "agent_last_seen": "TEXT",
    "agent_panel_url": "TEXT",
    "cloudflare_domain_id": "INTEGER",
}

NEW_TASK_COLUMNS = {
    "campaign_id": "INTEGER",
    "shard_id": "INTEGER",
    "is_test": "BOOLEAN DEFAULT 0",
    "html": "TEXT",
    "plain_text": "TEXT",
    "unsubscribe_url": "TEXT",
    "feedback_id": "TEXT",
    "cta_url": "TEXT",
    "scheduled_at": "TEXT",
    "window_start": "TEXT",
    "window_end": "TEXT",
    "subjects": "TEXT DEFAULT '[]'",
    "sender_name": "TEXT",
}

NEW_CAMPAIGN_COLUMNS = {
    "is_draft": "BOOLEAN DEFAULT 0",
    "parent_campaign_id": "INTEGER",
    "list_id": "INTEGER",
    "chunk_size": "INTEGER DEFAULT 2000",
    "status": "TEXT DEFAULT 'draft'",
    "started_at": "TEXT",
    "scheduled_at": "TEXT",
    "window_start": "TEXT",
    "window_end": "TEXT",
    "subjects": "TEXT DEFAULT '[]'",
    "sender_name": "TEXT",
}

NEW_CLOUDFLAREDOMAIN_COLUMNS = {
    "account_id": "INTEGER",
}


def _migrate_table_columns(table_name: str, columns_dict: dict):
    try:
        with engine.connect() as conn:
            existing = {
                row[1].lower()
                for row in conn.execute(text(f"PRAGMA table_info('{table_name}')")).fetchall()
            }
            for col_name, col_type in columns_dict.items():
                if col_name.lower() not in existing:
                    try:
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))
                        conn.commit()
                    except Exception as e:
                        print(f"Notice: Migration of {table_name}.{col_name}: {e}")
    except Exception as e:
        print(f"Notice: Table migration check for {table_name}: {e}")


def _migrate_node_columns():
    _migrate_table_columns("node", NEW_NODE_COLUMNS)


def _migrate_task_columns():
    _migrate_table_columns("task", NEW_TASK_COLUMNS)


def _migrate_campaign_columns():
    _migrate_table_columns("campaign", NEW_CAMPAIGN_COLUMNS)


def _migrate_cloudflare_columns():
    _migrate_table_columns("cloudflaredomain", NEW_CLOUDFLAREDOMAIN_COLUMNS)
    # Ensure cloudflareaccount table exists (handled by SQLModel.metadata.create_all)


NEW_WEBHOOK_COLUMNS = {
    "status": "TEXT DEFAULT 'pending_config'",
    "sample_payload": "TEXT",
    "list_id": "INTEGER",
    "email_field": "TEXT",
    "last_exported_at": "TEXT",
    "last_exported_lead_id": "INTEGER",
}


def _migrate_webhook_columns():
    _migrate_table_columns("webhookendpoint", NEW_WEBHOOK_COLUMNS)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    _migrate_node_columns()
    _migrate_task_columns()
    _migrate_campaign_columns()
    _migrate_webhook_columns()
    _migrate_cloudflare_columns()


def get_session():
    with Session(engine) as session:
        yield session
