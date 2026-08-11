import re
import secrets
from datetime import datetime
from typing import List, Optional

from pydantic import field_validator
from sqlmodel import Field, Relationship, SQLModel

DOMAIN_PATTERN = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)
EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$")


def _validate_domain(value: Optional[str]) -> Optional[str]:
    if value is not None and not DOMAIN_PATTERN.match(value):
        raise ValueError("domain must be a valid hostname (letters, digits, dots and hyphens)")
    return value


def _validate_email(value: Optional[str]) -> Optional[str]:
    if value is not None and not EMAIL_PATTERN.match(value):
        raise ValueError("email_from must be a valid email address")
    return value


class NodeTableBase(SQLModel):
    hostname: str
    ip: str
    role: str = Field(default="sender")
    ssh_port: int = Field(default=22)
    ssh_user: str
    auth_method: str = Field(default="key")
    ssh_password: Optional[str] = None
    ssh_private_key: Optional[str] = None
    tags: Optional[str] = None
    cloudflare_domain_id: Optional[int] = None
    domain: Optional[str] = None
    email_from: Optional[str] = None
    dkim_selector: Optional[str] = None
    dkim_dns_record: Optional[str] = None
    dmarc_dns_record: Optional[str] = None
    bootstrap_status: Optional[str] = None
    bootstrap_log: Optional[str] = None
    agent_token: Optional[str] = None
    agent_status: Optional[str] = None
    agent_last_seen: Optional[datetime] = None
    agent_panel_url: Optional[str] = None


class Node(NodeTableBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    tasks: List["Task"] = Relationship(back_populates="node")


class NodeCreate(SQLModel):
    hostname: str
    ip: str
    role: str = Field(default="sender")
    ssh_port: int = Field(default=22)
    ssh_user: str
    auth_method: str = Field(default="key")
    ssh_password: Optional[str] = None
    ssh_private_key: Optional[str] = None
    tags: Optional[str] = None
    cloudflare_domain_id: Optional[int] = None
    domain: Optional[str] = None
    email_from: Optional[str] = None

    _check_domain = field_validator("domain")(_validate_domain)
    _check_email = field_validator("email_from")(_validate_email)


class NodeUpdate(SQLModel):
    hostname: Optional[str] = None
    ip: Optional[str] = None
    role: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None
    auth_method: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_private_key: Optional[str] = None
    tags: Optional[str] = None
    cloudflare_domain_id: Optional[int] = None
    domain: Optional[str] = None
    email_from: Optional[str] = None

    _check_domain = field_validator("domain")(_validate_domain)
    _check_email = field_validator("email_from")(_validate_email)


class NodeRead(SQLModel):
    id: int
    hostname: str
    ip: str
    role: str
    ssh_port: int
    ssh_user: str
    auth_method: str
    tags: Optional[str] = None
    cloudflare_domain_id: Optional[int] = None
    domain: Optional[str] = None
    email_from: Optional[str] = None
    dkim_selector: Optional[str] = None
    dkim_dns_record: Optional[str] = None
    dmarc_dns_record: Optional[str] = None
    bootstrap_status: Optional[str] = None
    bootstrap_log: Optional[str] = None
    agent_token: Optional[str] = None
    agent_status: Optional[str] = None
    agent_last_seen: Optional[datetime] = None
    agent_panel_url: Optional[str] = None
    created_at: datetime


# ── Task ─────────────────────────────────────────────────────────────────────

class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: int = Field(foreign_key="node.id")
    campaign_id: Optional[int] = Field(default=None, foreign_key="campaign.id")
    shard_id: Optional[int] = Field(default=None)  # FK to campaignshard (no ORM rel to avoid circular)
    status: str = Field(default="pending")
    is_test: bool = False
    subject: str
    subjects: str = Field(default="[]")  # JSON array of subjects for A/B testing
    sender_name: Optional[str] = None
    body: str
    html: Optional[str] = None
    plain_text: Optional[str] = None
    from_address: str
    recipients: str           # JSON array stored as text
    rate_per_hour: int = 0
    unsubscribe_url: Optional[str] = None
    feedback_id: Optional[str] = None
    cta_url: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    sent_count: int = 0
    error_count: int = 0
    task_log: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    node: Optional[Node] = Relationship(back_populates="tasks")


class TaskCreate(SQLModel):
    node_id: int
    subject: str
    subjects: List[str] = []
    sender_name: Optional[str] = None
    body: str
    html: Optional[str] = None
    plain_text: Optional[str] = None
    from_address: str
    recipients: List[str]
    rate_per_hour: int = 0
    unsubscribe_url: Optional[str] = None
    feedback_id: Optional[str] = None
    cta_url: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None


class TaskRead(SQLModel):
    id: int
    node_id: int
    campaign_id: Optional[int] = None
    shard_id: Optional[int] = None
    status: str
    is_test: bool = False
    subject: str
    subjects: str = "[]"
    sender_name: Optional[str] = None
    body: str
    html: Optional[str] = None
    plain_text: Optional[str] = None
    from_address: str
    recipients: str
    rate_per_hour: int
    unsubscribe_url: Optional[str] = None
    feedback_id: Optional[str] = None
    cta_url: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    sent_count: int
    error_count: int
    task_log: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None


def generate_token() -> str:
    return secrets.token_urlsafe(32)


# ── Recipient Lists ──────────────────────────────────────────────────────────

class RecipientList(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    total_count: int = 0
    active_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RecipientListRead(SQLModel):
    id: int
    name: str
    total_count: int
    active_count: int
    created_at: datetime


class Recipient(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    list_id: int = Field(foreign_key="recipientlist.id", index=True)
    email: str
    status: str = Field(default="active")  # active | unsubscribed | bounced
    row_index: int = Field(index=True)     # stable position within list, 0-based


# ── Campaign Shards ──────────────────────────────────────────────────────────

class CampaignShard(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    node_id: int = Field(foreign_key="node.id", index=True)
    list_id: int = Field(foreign_key="recipientlist.id")
    offset_start: int       # absolute row_index of first recipient for this shard
    offset_end: int         # absolute row_index of last recipient (exclusive)
    chunk_size: int = 2000
    next_row_index: int = Field(default=0)  # current cursor (starts at offset_start)
    status: str = Field(default="pending")  # pending | running | paused | done | failed
    sent_count: int = 0
    error_count: int = 0
    current_task_id: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None


class CampaignShardRead(SQLModel):
    id: int
    campaign_id: int
    node_id: int
    list_id: int
    offset_start: int
    offset_end: int
    chunk_size: int
    next_row_index: int
    status: str
    sent_count: int
    error_count: int
    current_task_id: Optional[int] = None
    created_at: datetime
    finished_at: Optional[datetime] = None


# ── Campaigns ────────────────────────────────────────────────────────────────

class Campaign(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    parent_campaign_id: Optional[int] = Field(default=None, foreign_key="campaign.id")
    template_id: Optional[int] = Field(default=None, foreign_key="emailtemplate.id")
    list_id: Optional[int] = Field(default=None, foreign_key="recipientlist.id")
    subject: str = ""
    subjects: str = Field(default="[]")  # JSON array for A/B testing
    sender_name: Optional[str] = None
    cta_url: Optional[str] = None
    rate_per_hour: int = 0
    chunk_size: int = 2000
    scheduled_at: Optional[datetime] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    test_recipient: Optional[str] = None
    is_test: bool = False
    is_draft: bool = False
    status: str = Field(default="draft")  # draft | ready | scheduled | running | paused | done
    total_recipients: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None


class CampaignCreate(SQLModel):
    name: str
    parent_campaign_id: Optional[int] = None
    template_id: Optional[int] = None
    list_id: Optional[int] = None
    subject: Optional[str] = None
    subjects: List[str] = []  # List of subjects for A/B testing
    sender_name: Optional[str] = None
    cta_url: Optional[str] = None
    rate_per_hour: int = 0
    chunk_size: int = 2000
    scheduled_at: Optional[datetime] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    node_ids: List[int] = []
    recipients: List[str] = []
    test_recipient: Optional[str] = None
    is_draft: bool = False


class CampaignRead(SQLModel):
    id: int
    name: str
    parent_campaign_id: Optional[int] = None
    template_id: Optional[int] = None
    list_id: Optional[int] = None
    subject: str
    subjects: str = "[]"
    sender_name: Optional[str] = None
    cta_url: Optional[str] = None
    rate_per_hour: int
    chunk_size: int
    scheduled_at: Optional[datetime] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    test_recipient: Optional[str] = None
    is_test: bool
    is_draft: bool
    status: str
    total_recipients: int
    created_at: datetime


# â”€â”€ Email Templates â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class EmailTemplate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    subject: str = ""
    html: Optional[str] = None
    plain_text: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class EmailTemplateCreate(SQLModel):
    name: str
    subject: str = ""
    html: Optional[str] = None
    plain_text: Optional[str] = None


class EmailTemplateRead(SQLModel):
    id: int
    name: str
    subject: str
    html: Optional[str] = None
    plain_text: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# â”€â”€ Unsubscribe â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class UnsubscribeEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: Optional[int] = Field(default=None, foreign_key="node.id")
    domain: Optional[str] = None
    email: str = Field(index=True)
    source: str = Field(default="link")  # "link" (GET click) or "one-click" (POST RFC 8058)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Webhook Endpoints ────────────────────────────────────────────────────────

class WebhookEndpoint(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    token: str = Field(index=True)
    status: str = Field(default="pending_config")  # pending_config | configuring | active
    sample_payload: Optional[str] = None
    total_received: int = 0
    last_exported_at: Optional[datetime] = None
    last_exported_lead_id: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WebhookColumnMapping(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    webhook_id: int = Field(index=True)
    column_name: str        # name used as key in stored lead data
    json_path: str          # dot-notation path in incoming JSON
    is_email: bool = Field(default=False)  # used for deduplication
    sort_order: int = Field(default=0)


class WebhookLead(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    webhook_id: int = Field(index=True)
    data: str               # JSON string: {column_name: extracted_value}
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WebhookEndpointCreate(SQLModel):
    name: str


class WebhookMappingItem(SQLModel):
    column_name: str
    json_path: str
    is_email: bool = False
    sort_order: int = 0


class WebhookConfigurePayload(SQLModel):
    mappings: List[WebhookMappingItem]


class WebhookEndpointRead(SQLModel):
    id: int
    name: str
    token: str
    status: str
    sample_payload: Optional[str] = None
    total_received: int
    last_exported_at: Optional[datetime] = None
    last_exported_lead_id: Optional[int] = None
    new_leads_count: int = 0
    created_at: datetime
    mappings: List[dict] = []


class CloudflareConfig(SQLModel, table=True):
    id: Optional[int] = Field(default=1, primary_key=True)
    api_token: Optional[str] = None
    zone_id: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CloudflareConfigUpdate(SQLModel):
    api_token: Optional[str] = None
    zone_id: Optional[str] = None
    clear_token: bool = False


class CloudflareConfigRead(SQLModel):
    has_token: bool
    zone_id: Optional[str] = None
    updated_at: Optional[datetime] = None


class CloudflareDomain(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    domain: str
    zone_id: Optional[str] = None
    account_id: Optional[int] = Field(default=None)  # FK to cloudflareaccount.id
    created_at: datetime = Field(default_factory=datetime.utcnow)

    _check_domain = field_validator("domain")(_validate_domain)


class CloudflareDomainCreate(SQLModel):
    domain: str
    zone_id: Optional[str] = None

    _check_domain = field_validator("domain")(_validate_domain)


class CloudflareDomainRead(SQLModel):
    id: int
    domain: str
    zone_id: Optional[str] = None
    account_id: Optional[int] = None
    created_at: datetime


# ── Cloudflare Accounts ───────────────────────────────────────────────────────

class CloudflareAccount(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    api_token: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CloudflareAccountCreate(SQLModel):
    name: str
    api_token: str


class CloudflareAccountRead(SQLModel):
    id: int
    name: str
    domain_count: int = 0
    created_at: datetime
