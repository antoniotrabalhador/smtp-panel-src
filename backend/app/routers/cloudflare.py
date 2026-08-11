from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.cloudflare_api import cf_api_request, cf_delete_record, cf_find_zone_id
from app.db import get_session
from app.models import (
    CloudflareAccount,
    CloudflareAccountCreate,
    CloudflareAccountRead,
    CloudflareConfig,
    CloudflareConfigRead,
    CloudflareConfigUpdate,
    CloudflareDomain,
    CloudflareDomainCreate,
    CloudflareDomainRead,
    Node,
)

router = APIRouter(prefix="/api/cloudflare", tags=["cloudflare"])


# ── Legacy singleton config (kept for bootstrap/VPS compatibility) ─────────────

def _get_or_create_config(session: Session) -> CloudflareConfig:
    cfg = session.get(CloudflareConfig, 1)
    if cfg:
        return cfg
    cfg = CloudflareConfig(id=1)
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    return cfg


@router.get("/config", response_model=CloudflareConfigRead)
def get_config(session: Session = Depends(get_session)):
    cfg = _get_or_create_config(session)
    return CloudflareConfigRead(
        has_token=bool(cfg.api_token),
        zone_id=cfg.zone_id,
        updated_at=cfg.updated_at,
    )


@router.put("/config", response_model=CloudflareConfigRead)
def update_config(payload: CloudflareConfigUpdate, session: Session = Depends(get_session)):
    cfg = _get_or_create_config(session)
    changed = False

    if payload.clear_token:
        cfg.api_token = None
        cfg.zone_id = None
        changed = True

    if payload.api_token is not None:
        cfg.api_token = payload.api_token.strip() or None
        changed = True

    if payload.zone_id is not None:
        cfg.zone_id = payload.zone_id.strip() or None
        changed = True

    if not changed:
        raise HTTPException(status_code=400, detail="Nenhuma alteração enviada")

    cfg.updated_at = datetime.utcnow()
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    return CloudflareConfigRead(
        has_token=bool(cfg.api_token),
        zone_id=cfg.zone_id,
        updated_at=cfg.updated_at,
    )


@router.post("/config/test")
def test_config(payload: dict | None = None, session: Session = Depends(get_session)):
    cfg = _get_or_create_config(session)
    if not cfg.api_token:
        raise HTTPException(status_code=400, detail="Token não configurado")

    try:
        verify = cf_api_request(cfg.api_token, "GET", "/user/tokens/verify")
        token_status = (verify.get("result") or {}).get("status", "unknown")
        zone_id = cfg.zone_id
        if not zone_id and payload and payload.get("domain"):
            zone_id = cf_find_zone_id(cfg.api_token, str(payload["domain"]).strip())
        zone_name = None
        if zone_id:
            zone_data = cf_api_request(cfg.api_token, "GET", f"/zones/{zone_id}")
            zone_name = (zone_data.get("result") or {}).get("name")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "success": True,
        "token_status": token_status,
        "zone_id": zone_id,
        "zone_name": zone_name,
    }


# ── Multi-Account Cloudflare ──────────────────────────────────────────────────

def _resolve_token(account_id: int, session: Session) -> str:
    """Return API token for given account id, raising 404/400 if missing."""
    account = session.get(CloudflareAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Conta Cloudflare não encontrada")
    if not account.api_token:
        raise HTTPException(status_code=400, detail="Token não configurado nessa conta")
    return account.api_token


@router.get("/accounts", response_model=list[CloudflareAccountRead])
def list_accounts(session: Session = Depends(get_session)):
    """List all Cloudflare accounts (without exposing tokens)."""
    accounts = session.exec(select(CloudflareAccount).order_by(CloudflareAccount.created_at)).all()
    result = []
    for acc in accounts:
        domain_count = len(
            session.exec(select(CloudflareDomain).where(CloudflareDomain.account_id == acc.id)).all()
        )
        result.append(CloudflareAccountRead(
            id=acc.id,
            name=acc.name,
            domain_count=domain_count,
            created_at=acc.created_at,
        ))
    return result


@router.post("/accounts", response_model=CloudflareAccountRead)
def add_account(payload: CloudflareAccountCreate, session: Session = Depends(get_session)):
    """Register a new Cloudflare account (name + token)."""
    token = payload.api_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token não pode ser vazio")

    # Validate token against Cloudflare API
    try:
        verify = cf_api_request(token, "GET", "/user/tokens/verify")
        status = (verify.get("result") or {}).get("status", "unknown")
        if status != "active":
            raise HTTPException(status_code=400, detail=f"Token inválido (status: {status})")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    account = CloudflareAccount(name=payload.name.strip() or "Conta sem nome", api_token=token)
    session.add(account)
    session.commit()
    session.refresh(account)
    return CloudflareAccountRead(id=account.id, name=account.name, domain_count=0, created_at=account.created_at)


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(account_id: int, session: Session = Depends(get_session)):
    """Delete a Cloudflare account. Domains linked to VPS cannot be deleted."""
    account = session.get(CloudflareAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada")

    domains = session.exec(
        select(CloudflareDomain).where(CloudflareDomain.account_id == account_id)
    ).all()

    for domain in domains:
        linked_node = session.exec(
            select(Node).where(Node.cloudflare_domain_id == domain.id)
        ).first()
        if linked_node:
            raise HTTPException(
                status_code=400,
                detail=f"Domínio '{domain.domain}' está vinculado a uma VPS. Remova o vínculo antes de excluir a conta.",
            )
        session.delete(domain)

    session.delete(account)
    session.commit()


@router.get("/accounts/{account_id}/preview-zones")
def preview_zones(account_id: int, session: Session = Depends(get_session)):
    """Fetch zones from Cloudflare for this account without saving anything."""
    token = _resolve_token(account_id, session)
    try:
        data = cf_api_request(token, "GET", "/zones?per_page=200")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    existing_domains = {
        d.domain.lower()
        for d in session.exec(
            select(CloudflareDomain).where(CloudflareDomain.account_id == account_id)
        ).all()
    }

    zones = []
    for z in data.get("result", []):
        name = (z.get("name") or "").strip().lower()
        zones.append({
            "id": z.get("id"),
            "name": name,
            "status": z.get("status"),
            "already_imported": name in existing_domains,
        })
    zones.sort(key=lambda x: x["name"])
    return {"account_id": account_id, "zones": zones}


@router.post("/accounts/{account_id}/import-zones")
def import_selected_zones(account_id: int, payload: dict, session: Session = Depends(get_session)):
    """Import only the selected zone_ids for this account."""
    token = _resolve_token(account_id, session)
    zone_ids_to_import: list[str] = payload.get("zone_ids", [])
    if not zone_ids_to_import:
        raise HTTPException(status_code=400, detail="Nenhuma zone selecionada")

    # Fetch all zones to map id → name
    try:
        data = cf_api_request(token, "GET", "/zones?per_page=200")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    zones_map = {z["id"]: z for z in data.get("result", [])}
    existing = {
        d.domain.lower(): d
        for d in session.exec(select(CloudflareDomain)).all()
    }

    created = 0
    updated = 0
    for zone_id in zone_ids_to_import:
        z = zones_map.get(zone_id)
        if not z:
            continue
        name = (z.get("name") or "").strip().lower()
        if not name:
            continue
        if name in existing:
            db_domain = existing[name]
            changed = False
            if db_domain.zone_id != zone_id:
                db_domain.zone_id = zone_id
                changed = True
            if db_domain.account_id != account_id:
                db_domain.account_id = account_id
                changed = True
            if changed:
                session.add(db_domain)
                updated += 1
        else:
            session.add(CloudflareDomain(domain=name, zone_id=zone_id, account_id=account_id))
            created += 1

    session.commit()
    return {"ok": True, "created": created, "updated": updated}


# ── Domain management ─────────────────────────────────────────────────────────

@router.get("/domains", response_model=list[CloudflareDomainRead])
def list_domains(session: Session = Depends(get_session)):
    return session.exec(select(CloudflareDomain).order_by(CloudflareDomain.domain.asc())).all()


@router.post("/domains", response_model=CloudflareDomainRead)
def create_domain(payload: CloudflareDomainCreate, session: Session = Depends(get_session)):
    existing = session.exec(select(CloudflareDomain).where(CloudflareDomain.domain == payload.domain)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Domínio já cadastrado")
    zone_id = payload.zone_id or None
    if not zone_id:
        cfg = _get_or_create_config(session)
        if cfg.api_token:
            try:
                zone_id = cf_find_zone_id(cfg.api_token, payload.domain)
            except RuntimeError:
                zone_id = None
    db_domain = CloudflareDomain(domain=payload.domain, zone_id=zone_id)
    session.add(db_domain)
    session.commit()
    session.refresh(db_domain)
    return db_domain


@router.delete("/domains/{domain_id}", status_code=204)
def delete_domain(domain_id: int, session: Session = Depends(get_session)):
    db_domain = session.get(CloudflareDomain, domain_id)
    if not db_domain:
        raise HTTPException(status_code=404, detail="Domínio não encontrado")
    linked_node = session.exec(select(Node).where(Node.cloudflare_domain_id == domain_id)).first()
    if linked_node:
        raise HTTPException(status_code=400, detail="Domínio vinculado a uma VPS. Remova o vínculo antes de excluir.")
    session.delete(db_domain)
    session.commit()


@router.get("/zones")
def list_cloudflare_zones(session: Session = Depends(get_session)):
    cfg = _get_or_create_config(session)
    if not cfg.api_token:
        raise HTTPException(status_code=400, detail="Token não configurado")
    try:
        data = cf_api_request(cfg.api_token, "GET", "/zones?per_page=200")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    zones = []
    for z in data.get("result", []):
        zones.append({"id": z.get("id"), "name": z.get("name"), "status": z.get("status")})
    zones.sort(key=lambda x: (x["name"] or ""))
    return {"zones": zones}


@router.post("/domains/import")
def import_cloudflare_zones(session: Session = Depends(get_session)):
    cfg = _get_or_create_config(session)
    if not cfg.api_token:
        raise HTTPException(status_code=400, detail="Token não configurado")
    try:
        data = cf_api_request(cfg.api_token, "GET", "/zones?per_page=200")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    existing = {d.domain.lower(): d for d in session.exec(select(CloudflareDomain)).all()}
    created = 0
    updated = 0
    for z in data.get("result", []):
        name = (z.get("name") or "").strip().lower()
        zone_id = z.get("id")
        if not name or not zone_id:
            continue
        if name in existing:
            db_domain = existing[name]
            if db_domain.zone_id != zone_id:
                db_domain.zone_id = zone_id
                session.add(db_domain)
                updated += 1
            continue
        session.add(CloudflareDomain(domain=name, zone_id=zone_id))
        created += 1
    session.commit()
    return {"success": True, "created": created, "updated": updated, "total": len(data.get("result", []))}


@router.get("/domains/{domain_id}/records")
def list_domain_records(domain_id: int, session: Session = Depends(get_session)):
    db_domain = session.get(CloudflareDomain, domain_id)
    if not db_domain:
        raise HTTPException(status_code=404, detail="Domínio não encontrado")

    # Resolve which token to use: account-specific or legacy singleton
    token = None
    if db_domain.account_id:
        account = session.get(CloudflareAccount, db_domain.account_id)
        if account:
            token = account.api_token
    if not token:
        cfg = _get_or_create_config(session)
        token = cfg.api_token
    if not token:
        raise HTTPException(status_code=400, detail="Token não configurado")

    zone_id = db_domain.zone_id
    if not zone_id:
        try:
            zone_id = cf_find_zone_id(token, db_domain.domain)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))
        db_domain.zone_id = zone_id
        session.add(db_domain)
        session.commit()

    try:
        data = cf_api_request(token, "GET", f"/zones/{zone_id}/dns_records?per_page=500")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    records = []
    for rec in data.get("result", []):
        records.append({
            "id": rec.get("id"),
            "type": rec.get("type"),
            "name": rec.get("name"),
            "content": rec.get("content"),
            "ttl": rec.get("ttl"),
            "proxied": rec.get("proxied"),
            "priority": rec.get("priority"),
        })
    records.sort(key=lambda r: (r["name"] or "", r["type"] or ""))
    return {"domain": db_domain.domain, "zone_id": zone_id, "records": records}


@router.delete("/domains/{domain_id}/records/{record_id}", status_code=204)
def delete_domain_record(domain_id: int, record_id: str, session: Session = Depends(get_session)):
    db_domain = session.get(CloudflareDomain, domain_id)
    if not db_domain:
        raise HTTPException(status_code=404, detail="Domínio não encontrado")

    token = None
    if db_domain.account_id:
        account = session.get(CloudflareAccount, db_domain.account_id)
        if account:
            token = account.api_token
    if not token:
        cfg = _get_or_create_config(session)
        token = cfg.api_token
    if not token:
        raise HTTPException(status_code=400, detail="Token não configurado")

    zone_id = db_domain.zone_id
    if not zone_id:
        try:
            zone_id = cf_find_zone_id(token, db_domain.domain)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))
        db_domain.zone_id = zone_id
        session.add(db_domain)
        session.commit()

    try:
        cf_delete_record(token, zone_id, record_id)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
