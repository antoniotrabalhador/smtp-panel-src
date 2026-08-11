import json
import math
from collections import defaultdict
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlmodel import Session, select

from app.db import get_session, engine
from app.models import Campaign, CampaignCreate, CampaignShard, EmailTemplate, Node, RecipientList, Task
from app.ssh import send_test_email, get_postfix_stats, get_raw_postfix_log

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def _chunk_recipients(recipients: List[str], bucket_count: int) -> List[List[str]]:
    buckets = [[] for _ in range(bucket_count)]
    for index, recipient in enumerate(recipients):
        buckets[index % bucket_count].append(recipient)
    return buckets


def _status_for_tasks(tasks: List[Task]) -> str:
    if not tasks:
        return "empty"
    statuses = {task.status for task in tasks}
    if "running" in statuses:
        return "running"
    if statuses == {"done"}:
        return "done"
    if statuses <= {"pending"}:
        return "pending"
    if "failed" in statuses and len(statuses) == 1:
        return "failed"
    if "failed" in statuses:
        return "partial"
    return sorted(statuses)[0]


def _serialize_campaign(campaign: Campaign, tasks: List[Task], nodes_by_id: dict[int, Node]) -> dict:
    node_summaries = []
    for task in tasks:
        node = nodes_by_id.get(task.node_id)
        node_summaries.append(
            {
                "task_id": task.id,
                "node_id": task.node_id,
                "node_name": node.hostname if node else f"Node {task.node_id}",
                "status": task.status,
                "sent_count": task.sent_count,
                "error_count": task.error_count,
                "recipient_count": len(json.loads(task.recipients or "[]")),
                "from_address": task.from_address,
                "created_at": task.created_at,
                "finished_at": task.finished_at,
            }
        )

    # For shard-based campaigns (launched via /launch), campaign.status is authoritative.
    # Only fall back to task-derived status for legacy direct-task campaigns.
    shard_statuses = {"running", "paused", "done", "scheduled"}
    if campaign.status in shard_statuses or (campaign.status not in ("draft", None) and not tasks):
        effective_status = campaign.status
    else:
        effective_status = campaign.status if campaign.status in ("scheduled", "draft") else _status_for_tasks(tasks)

    return {
        "id": campaign.id,
        "name": campaign.name,
        "parent_campaign_id": campaign.parent_campaign_id,
        "template_id": campaign.template_id,
        "subject": campaign.subject,
        "cta_url": campaign.cta_url,
        "rate_per_hour": campaign.rate_per_hour,
        "scheduled_at": getattr(campaign, "scheduled_at", None),
        "window_start": getattr(campaign, "window_start", None),
        "window_end": getattr(campaign, "window_end", None),
        "test_recipient": campaign.test_recipient,
        "is_test": campaign.is_test,
        "is_draft": campaign.is_draft,
        "total_recipients": campaign.total_recipients,
        "created_at": campaign.created_at,
        "status": effective_status,
        "sent_count": sum(task.sent_count for task in tasks),
        "error_count": sum(task.error_count for task in tasks),
        "nodes": node_summaries,
    }


def _serialize_child_campaign(campaign: Campaign) -> dict:
    return {
        "id": campaign.id,
        "name": campaign.name,
        "parent_campaign_id": campaign.parent_campaign_id,
        "template_id": campaign.template_id,
        "subject": campaign.subject,
        "cta_url": campaign.cta_url,
        "rate_per_hour": campaign.rate_per_hour,
        "scheduled_at": getattr(campaign, "scheduled_at", None),
        "window_start": getattr(campaign, "window_start", None),
        "window_end": getattr(campaign, "window_end", None),
        "test_recipient": campaign.test_recipient,
        "is_test": campaign.is_test,
        "is_draft": campaign.is_draft,
        "total_recipients": campaign.total_recipients,
        "created_at": campaign.created_at,
        "status": "draft" if campaign.is_draft else "pending",
        "sent_count": 0,
        "error_count": 0,
        "nodes": [],
        "children": [],
    }


def _load_nodes(node_ids: List[int], session: Session) -> List[Node]:
    nodes = session.exec(select(Node).where(Node.id.in_(node_ids))).all()
    if len(nodes) != len(set(node_ids)):
        raise HTTPException(status_code=400, detail="Uma ou mais VPS nao foram encontradas")
    invalid = [node.hostname for node in nodes if not node.email_from]
    if invalid:
        raise HTTPException(status_code=400, detail=f"VPS sem email_from configurado: {', '.join(invalid)}")
    return nodes


def _load_template(template_id: int, session: Session) -> EmailTemplate:
    template = session.get(EmailTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template nao encontrado")
    return template


@router.get("")
def list_campaigns(session: Session = Depends(get_session)):
    campaigns = session.exec(select(Campaign).order_by(Campaign.created_at.desc())).all()
    if not campaigns:
        return []

    roots = [campaign for campaign in campaigns if campaign.parent_campaign_id is None]
    children = [campaign for campaign in campaigns if campaign.parent_campaign_id is not None]
    campaign_ids = [campaign.id for campaign in campaigns if campaign.id is not None]
    tasks = session.exec(select(Task).where(Task.campaign_id.in_(campaign_ids)).order_by(Task.created_at.desc())).all()
    tasks_by_campaign: dict[int, List[Task]] = defaultdict(list)
    node_ids = {task.node_id for task in tasks}
    for task in tasks:
        if task.campaign_id is not None:
            tasks_by_campaign[task.campaign_id].append(task)

    nodes_by_id = {node.id: node for node in session.exec(select(Node).where(Node.id.in_(node_ids))).all()}
    children_by_parent: dict[int, List[dict]] = defaultdict(list)
    for child in children:
        if child.parent_campaign_id is not None:
            children_by_parent[child.parent_campaign_id].append(
                _serialize_campaign(child, tasks_by_campaign.get(child.id, []), nodes_by_id)
            )

    roots_serialized = []
    for campaign in roots:
        data = _serialize_campaign(campaign, tasks_by_campaign.get(campaign.id, []), nodes_by_id)
        data["children"] = children_by_parent.get(campaign.id, [])
        roots_serialized.append(data)
    return roots_serialized


@router.post("")
def create_campaign(payload: CampaignCreate, session: Session = Depends(get_session)):
    try:
        if payload.template_id is None:
            raise HTTPException(status_code=400, detail="Selecione um template")

        template = _load_template(payload.template_id, session)
        # Build subjects list: use payload.subjects if provided, else fall back to subject field, then template
        subjects_list = [s.strip() for s in payload.subjects if s.strip()] if payload.subjects else []
        if not subjects_list:
            fallback = (payload.subject or template.subject or "").strip()
            if fallback:
                subjects_list = [fallback]
        subject = subjects_list[0] if subjects_list else ""
        if not payload.is_draft and not subject:
            raise HTTPException(status_code=400, detail="Assunto obrigatorio")

        subjects_json = json.dumps(subjects_list, ensure_ascii=False)

        if payload.is_draft:
            recipients = []
            nodes = []
        else:
            if not payload.node_ids:
                raise HTTPException(status_code=400, detail="Selecione pelo menos uma VPS")
            recipients = [item.strip() for item in payload.recipients if item and item.strip()]
            if not recipients:
                raise HTTPException(status_code=400, detail="Lista de destinatarios vazia")
            nodes = _load_nodes(payload.node_ids, session)

        sched = payload.scheduled_at.replace(tzinfo=None) if (payload.scheduled_at and payload.scheduled_at.tzinfo) else payload.scheduled_at
        is_scheduled = bool(sched and sched > datetime.utcnow())
        initial_status = "draft" if payload.is_draft else ("scheduled" if is_scheduled else "ready")

        campaign = Campaign(
            name=payload.name.strip() or "Campanha",
            parent_campaign_id=payload.parent_campaign_id,
            template_id=template.id,
            subject=subject,
            subjects=subjects_json,
            sender_name=(payload.sender_name or "").strip() or None,
            cta_url=(payload.cta_url or "").strip() or None,
            rate_per_hour=payload.rate_per_hour,
            scheduled_at=payload.scheduled_at,
            window_start=(payload.window_start or "").strip() or None,
            window_end=(payload.window_end or "").strip() or None,
            total_recipients=len(recipients),
            is_test=False,
            is_draft=payload.is_draft,
            status=initial_status,
        )
        session.add(campaign)
        session.commit()
        session.refresh(campaign)

        if not payload.is_draft:
            for node, node_recipients in zip(nodes, _chunk_recipients(recipients, len(nodes))):
                if not node_recipients:
                    continue
                session.add(
                    Task(
                        node_id=node.id,
                        campaign_id=campaign.id,
                        is_test=False,
                        subject=subject,
                        subjects=subjects_json,
                        sender_name=campaign.sender_name,
                        body=template.plain_text or "",
                        html=template.html,
                        plain_text=template.plain_text,
                        from_address=node.email_from,
                        recipients=json.dumps(node_recipients),
                        rate_per_hour=payload.rate_per_hour,
                        cta_url=campaign.cta_url,
                        scheduled_at=campaign.scheduled_at,
                        window_start=campaign.window_start,
                        window_end=campaign.window_end,
                    )
                )
            session.commit()

        return {"ok": True, "campaign_id": campaign.id, "is_draft": payload.is_draft}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Erro ao criar campanha: {str(exc)}")


@router.put("/{campaign_id}")
def update_campaign(campaign_id: int, payload: CampaignCreate, session: Session = Depends(get_session)):
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha nao encontrada")
    if payload.template_id is None:
        raise HTTPException(status_code=400, detail="Selecione um template")

    template = _load_template(payload.template_id, session)
    subjects_list = [s.strip() for s in payload.subjects if s.strip()] if payload.subjects else []
    if not subjects_list:
        fallback = (payload.subject or template.subject or "").strip()
        if fallback:
            subjects_list = [fallback]
    subject = subjects_list[0] if subjects_list else ""
    if not payload.is_draft and not subject:
        raise HTTPException(status_code=400, detail="Assunto obrigatorio")

    subjects_json = json.dumps(subjects_list, ensure_ascii=False)
    
    sched = payload.scheduled_at.replace(tzinfo=None) if (payload.scheduled_at and payload.scheduled_at.tzinfo) else payload.scheduled_at
    is_scheduled = bool(sched and sched > datetime.utcnow())
    new_status = "draft" if payload.is_draft else ("scheduled" if is_scheduled else "ready")

    campaign.name = payload.name.strip() or campaign.name or "Campanha"
    campaign.parent_campaign_id = payload.parent_campaign_id
    campaign.template_id = template.id
    campaign.subject = subject
    campaign.subjects = subjects_json
    campaign.sender_name = (payload.sender_name or "").strip() or None
    campaign.cta_url = (payload.cta_url or "").strip() or None
    campaign.rate_per_hour = payload.rate_per_hour
    campaign.scheduled_at = payload.scheduled_at
    campaign.window_start = (payload.window_start or "").strip() or None
    campaign.window_end = (payload.window_end or "").strip() or None
    campaign.is_draft = payload.is_draft
    campaign.status = new_status
    if payload.is_draft:
        campaign.total_recipients = 0

    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    return {"ok": True, "campaign_id": campaign.id, "is_draft": campaign.is_draft}


@router.post("/test")
async def test_campaign(payload: CampaignCreate, session: Session = Depends(get_session)):
    if not payload.node_ids:
        raise HTTPException(status_code=400, detail="Selecione pelo menos uma VPS")
    test_recipient = (payload.test_recipient or "").strip()
    if not test_recipient:
        raise HTTPException(status_code=400, detail="Email de teste obrigatorio")

    nodes = _load_nodes(payload.node_ids, session)
    template = _load_template(payload.template_id, session)
    subjects_list = [s.strip() for s in payload.subjects if s.strip()] if payload.subjects else []
    if not subjects_list:
        fallback = (payload.subject or template.subject or "").strip()
        if fallback:
            subjects_list = [fallback]
    subject = subjects_list[0] if subjects_list else ""
    if not subject:
        raise HTTPException(status_code=400, detail="Assunto obrigatorio")
    subjects_json = json.dumps(subjects_list, ensure_ascii=False)

    campaign = Campaign(
        name=(payload.name.strip() or "Teste SMTP") + " [teste]",
        template_id=template.id,
        subject=subject,
        subjects=subjects_json,
        sender_name=(payload.sender_name or "").strip() or None,
        cta_url=(payload.cta_url or "").strip() or None,
        rate_per_hour=payload.rate_per_hour,
        test_recipient=test_recipient,
        total_recipients=len(nodes),
        is_test=True,
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    results = []
    for node in nodes:
        task = Task(
            node_id=node.id,
            campaign_id=campaign.id,
            is_test=True,
            subject=subject,
            subjects=subjects_json,
            sender_name=(payload.sender_name or "").strip() or None,
            body=template.plain_text or "",
            html=template.html,
            plain_text=template.plain_text,
            from_address=node.email_from,
            recipients=json.dumps([test_recipient]),
            rate_per_hour=payload.rate_per_hour,
            cta_url=campaign.cta_url,
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        try:
            direct_result = await send_test_email(
                node,
                test_recipient,
                subject=subject,
                body=template.plain_text or "",
                html=template.html,
                cta_url=campaign.cta_url,
            )
            task.status = "done" if direct_result.get("success") else "failed"
            task.sent_count = 1 if direct_result.get("success") else 0
            task.error_count = 0 if direct_result.get("success") else 1
            task.task_log = direct_result.get("message", "")
            task.finished_at = datetime.utcnow()
            session.add(task)
            session.commit()
            results.append({
                "node_id": node.id,
                "node_name": node.hostname,
                "success": direct_result.get("success", False),
                "message": direct_result.get("message", ""),
            })
        except Exception as exc:
            task.status = "failed"
            task.sent_count = 0
            task.error_count = 1
            task.task_log = str(exc)
            task.finished_at = datetime.utcnow()
            session.add(task)
            session.commit()
            results.append({
                "node_id": node.id,
                "node_name": node.hostname,
                "success": False,
                "message": str(exc),
            })

    return {
        "ok": True,
        "campaign_id": campaign.id,
        "results": results,
        "node_names": [node.hostname for node in nodes],
    }


# ── Campaign launch (real mass send via shards) ───────────────────────────────

@router.post("/{campaign_id}/launch")
def launch_campaign(campaign_id: int, payload: dict, session: Session = Depends(get_session)):
    """Shard the recipient list across selected VPS and start the campaign."""
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")
    if campaign.status == "running":
        raise HTTPException(status_code=400, detail="Campanha já está em execução")

    node_ids = payload.get("node_ids", [])
    list_id = payload.get("list_id") or campaign.list_id
    chunk_size = int(payload.get("chunk_size") or campaign.chunk_size or 2000)

    if not node_ids:
        raise HTTPException(status_code=400, detail="Selecione pelo menos uma VPS")
    if not list_id:
        raise HTTPException(status_code=400, detail="Selecione uma lista de destinatários")

    nodes = _load_nodes(node_ids, session)
    rl = session.get(RecipientList, list_id)
    if not rl:
        raise HTTPException(status_code=404, detail="Lista não encontrada")

    total_active = rl.active_count or rl.total_count
    if total_active == 0:
        raise HTTPException(status_code=400, detail="Lista sem destinatários ativos")

    # Delete any existing shards for this campaign (re-launch scenario)
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM campaignshard WHERE campaign_id = {campaign_id}"))
        conn.commit()

    n = len(nodes)
    per_vps = math.ceil(total_active / n)

    shards = []
    for i, node in enumerate(nodes):
        start = i * per_vps
        end = min(start + per_vps, total_active)
        if start >= total_active:
            break
        shard = CampaignShard(
            campaign_id=campaign_id,
            node_id=node.id,
            list_id=list_id,
            offset_start=start,
            offset_end=end,
            chunk_size=chunk_size,
            next_row_index=start,
            status="pending",
        )
        session.add(shard)
        shards.append(shard)

    sched = campaign.scheduled_at.replace(tzinfo=None) if (campaign.scheduled_at and campaign.scheduled_at.tzinfo) else campaign.scheduled_at
    is_scheduled = bool(sched and sched > datetime.utcnow())

    campaign.list_id = list_id
    campaign.chunk_size = chunk_size
    campaign.status = "scheduled" if is_scheduled else "running"
    campaign.total_recipients = total_active
    campaign.is_draft = False
    campaign.started_at = datetime.utcnow()
    session.add(campaign)
    session.commit()

    return {
        "ok": True,
        "campaign_id": campaign_id,
        "shards": len(shards),
        "total_recipients": total_active,
        "chunk_size": chunk_size,
        "per_vps": per_vps,
    }


@router.get("/{campaign_id}/progress")
def campaign_progress(campaign_id: int, session: Session = Depends(get_session)):
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")

    shards = session.exec(
        select(CampaignShard).where(CampaignShard.campaign_id == campaign_id)
    ).all()

    if not shards:
        # Fall back to task-based progress (test/legacy campaigns)
        tasks = session.exec(select(Task).where(Task.campaign_id == campaign_id)).all()
        total = campaign.total_recipients or sum(len(json.loads(t.recipients or "[]")) for t in tasks)
        sent = sum(t.sent_count for t in tasks)
        errors = sum(t.error_count for t in tasks)
        return {
            "type": "task",
            "status": campaign.status,
            "total": total,
            "sent": sent,
            "errors": errors,
            "pct": round(sent / total * 100, 1) if total else 0,
            "shards": [],
        }

    node_ids = [s.node_id for s in shards]
    nodes_by_id = {n.id: n for n in session.exec(select(Node).where(Node.id.in_(node_ids))).all()}

    total = sum(s.offset_end - s.offset_start for s in shards)
    sent = sum(s.sent_count for s in shards)
    errors = sum(s.error_count for s in shards)

    # Refresh campaign status based on shards
    all_done = all(s.status == "done" for s in shards)
    any_running = any(s.status in ("running", "pending") for s in shards)
    if all_done and campaign.status != "done":
        campaign.status = "done"
        session.add(campaign)
        session.commit()

    return {
        "type": "sharded",
        "status": campaign.status,
        "total": total,
        "sent": sent,
        "errors": errors,
        "pct": round(sent / total * 100, 1) if total else 0,
        "shards": [
            {
                "shard_id": s.id,
                "node_id": s.node_id,
                "node_name": nodes_by_id.get(s.node_id, Node(hostname=f"Node {s.node_id}")).hostname,
                "status": s.status,
                "sent": s.sent_count,
                "errors": s.error_count,
                "total": s.offset_end - s.offset_start,
                "pct": round(s.sent_count / (s.offset_end - s.offset_start) * 100, 1) if (s.offset_end - s.offset_start) else 0,
                "current_chunk": (s.next_row_index - s.offset_start) // s.chunk_size if s.chunk_size else 0,
                "total_chunks": math.ceil((s.offset_end - s.offset_start) / s.chunk_size) if s.chunk_size else 0,
            }
            for s in shards
        ],
    }


@router.post("/{campaign_id}/pause")
def pause_campaign(campaign_id: int, session: Session = Depends(get_session)):
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")
    shards = session.exec(
        select(CampaignShard).where(
            CampaignShard.campaign_id == campaign_id,
            CampaignShard.status.in_(["pending", "running"]),
        )
    ).all()
    for shard in shards:
        shard.status = "paused"
        session.add(shard)
    campaign.status = "paused"
    session.add(campaign)
    session.commit()
    return {"ok": True, "paused_shards": len(shards)}


@router.post("/{campaign_id}/resume")
def resume_campaign(campaign_id: int, session: Session = Depends(get_session)):
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")
    shards = session.exec(
        select(CampaignShard).where(
            CampaignShard.campaign_id == campaign_id,
            CampaignShard.status == "paused",
        )
    ).all()
    for shard in shards:
        shard.status = "pending"
        session.add(shard)
    campaign.status = "running"
    session.add(campaign)
    session.commit()
    return {"ok": True, "resumed_shards": len(shards)}


@router.delete("/{campaign_id}")
def delete_campaign(campaign_id: int, session: Session = Depends(get_session)):
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM campaignshard WHERE campaign_id = {campaign_id}"))
        conn.execute(text(f"DELETE FROM task WHERE campaign_id = {campaign_id}"))
        conn.commit()
    session.delete(campaign)
    session.commit()
    return {"ok": True}


@router.get("/active")
def list_active_campaigns(session: Session = Depends(get_session)):
    """Return all running/paused/scheduled campaigns with full shard progress — used by the monitor."""
    now = datetime.utcnow()
    campaigns = session.exec(
        select(Campaign).where(Campaign.status.in_(["running", "paused", "scheduled"]))
    ).all()

    if not campaigns:
        return []

    result = []
    for campaign in campaigns:
        # Auto-transition scheduled → running if scheduled_at has passed
        if campaign.status == "scheduled":
            sched = campaign.scheduled_at
            if sched:
                sched_naive = sched.replace(tzinfo=None) if sched.tzinfo else sched
                if sched_naive <= now:
                    campaign.status = "running"
                    session.add(campaign)
                    session.commit()

        shards = session.exec(
            select(CampaignShard).where(CampaignShard.campaign_id == campaign.id)
        ).all()

        node_ids = [s.node_id for s in shards]
        nodes_by_id = {n.id: n for n in session.exec(select(Node).where(Node.id.in_(node_ids))).all()} if node_ids else {}

        total = sum(s.offset_end - s.offset_start for s in shards) or campaign.total_recipients
        sent = sum(s.sent_count for s in shards)
        errors = sum(s.error_count for s in shards)
        pct = round(sent / total * 100, 1) if total else 0

        # Detect if all shards are done
        if shards and all(s.status == "done" for s in shards) and campaign.status != "done":
            campaign.status = "done"
            session.add(campaign)
            session.commit()
            continue  # skip — no longer active

        shard_data = [
            {
                "shard_id": s.id,
                "node_id": s.node_id,
                "node_name": nodes_by_id.get(s.node_id, Node(hostname=f"Node {s.node_id}")).hostname,
                "status": s.status,
                "sent": s.sent_count,
                "errors": s.error_count,
                "total": s.offset_end - s.offset_start,
                "pct": round(s.sent_count / (s.offset_end - s.offset_start) * 100, 1) if (s.offset_end - s.offset_start) else 0,
                "next_row_index": s.next_row_index,
                "offset_start": s.offset_start,
                "offset_end": s.offset_end,
                "chunk_size": s.chunk_size,
                "current_chunk": (s.next_row_index - s.offset_start) // s.chunk_size if s.chunk_size else 0,
                "total_chunks": math.ceil((s.offset_end - s.offset_start) / s.chunk_size) if s.chunk_size else 0,
            }
            for s in shards
        ]

        result.append({
            "id": campaign.id,
            "name": campaign.name,
            "subject": campaign.subject,
            "subjects": campaign.subjects,
            "sender_name": campaign.sender_name,
            "status": campaign.status,
            "rate_per_hour": campaign.rate_per_hour,
            "chunk_size": campaign.chunk_size,
            "total": total,
            "sent": sent,
            "errors": errors,
            "pct": pct,
            "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
            "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
            "scheduled_at": campaign.scheduled_at.isoformat() if campaign.scheduled_at else None,
            "shards": shard_data,
        })

    return result


@router.post("/{campaign_id}/sync-postfix-stats")
async def sync_postfix_stats(campaign_id: int, session: Session = Depends(get_session)):
    """
    SSH into each node assigned to this campaign and count sent/bounced/deferred
    emails from /var/log/mail.log. Updates the shard sent_count so the monitor
    reflects real delivery numbers even when report_task was missed.
    """
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")

    shards = session.exec(
        select(CampaignShard).where(CampaignShard.campaign_id == campaign_id)
    ).all()
    if not shards:
        raise HTTPException(status_code=400, detail="Campanha sem shards (não foi lançada via /launch)")

    node_ids = list({s.node_id for s in shards})
    nodes = session.exec(select(Node).where(Node.id.in_(node_ids))).all()
    nodes_by_id = {n.id: n for n in nodes}

    results = []
    total_sent_all = 0
    total_bounced_all = 0

    import asyncio
    since = campaign.started_at  # Only count emails sent after campaign started
    stats_list = await asyncio.gather(*[
        get_postfix_stats(nodes_by_id[node_id], since=since)
        for node_id in node_ids
        if node_id in nodes_by_id
    ])

    stats_by_node = {s["node_id"]: s for s in stats_list}

    for shard in shards:
        node_stats = stats_by_node.get(shard.node_id, {})
        if not node_stats.get("success"):
            results.append({
                "node_id": shard.node_id,
                "shard_id": shard.id,
                "error": node_stats.get("error", "SSH falhou"),
                "sent": 0,
            })
            continue

        node_sent = node_stats.get("sent", 0)
        node_bounced = node_stats.get("bounced", 0)

        # Only update if Postfix reports MORE than what agent already reported
        # (agent is the primary source; postfix sync is a reconciliation fallback)
        if node_sent > shard.sent_count:
            shard.sent_count = node_sent
        if node_bounced > shard.error_count:
            shard.error_count = node_bounced
        session.add(shard)

        total_sent_all += node_sent
        total_bounced_all += node_bounced

        results.append({
            "node_id": shard.node_id,
            "shard_id": shard.id,
            "hostname": node_stats.get("hostname"),
            "sent": node_sent,
            "bounced": node_bounced,
            "deferred": node_stats.get("deferred", 0),
            "top_reasons": node_stats.get("top_reasons", []),
        })

    # Update campaign totals (no campaign.sent_count field — it's computed from shards)
    session.commit()

    return {
        "ok": True,
        "campaign_id": campaign_id,
        "total_sent": total_sent_all,
        "total_bounced": total_bounced_all,
        "nodes": results,
    }


@router.get("/{campaign_id}/download-logs")
async def download_campaign_logs(campaign_id: int, session: Session = Depends(get_session)):
    """
    Downloads the raw /var/log/mail.log from every VPS assigned to this campaign
    and bundles them into a single .tar.gz file, streamed directly to the client.
    """
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")

    shards = session.exec(
        select(CampaignShard).where(CampaignShard.campaign_id == campaign_id)
    ).all()
    if not shards:
        raise HTTPException(status_code=400, detail="Campanha sem shards (não foi lançada via /launch)")

    node_ids = list({s.node_id for s in shards})
    nodes = session.exec(select(Node).where(Node.id.in_(node_ids))).all()

    import asyncio
    import io
    import tarfile

    # Fetch logs concurrently
    log_contents = await asyncio.gather(*[get_raw_postfix_log(node) for node in nodes])

    # Build tar.gz in memory
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w:gz") as tar:
        for node, content in zip(nodes, log_contents):
            if not content:
                continue
            
            # Create a TarInfo object for this file
            tarinfo = tarfile.TarInfo(name=f"mail_log_{node.hostname}_{node.id}.log")
            tarinfo.size = len(content)
            
            # Write file content to tar
            tar.addfile(tarinfo, io.BytesIO(content))

    tar_stream.seek(0)
    
    filename = f"campaign_{campaign.id}_logs.tar.gz"
    return StreamingResponse(
        tar_stream,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
