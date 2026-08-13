import logging
import os
from typing import List

import dns.resolver
import dns.exception

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.cloudflare_api import cf_find_zone_id, cf_upsert_record
from app.db import get_session
from app.models import CloudflareAccount, CloudflareConfig, CloudflareDomain, Node, NodeCreate, NodeRead, NodeUpdate, Task, TaskCreate, TaskRead, generate_token
from app.ssh import stream_bootstrap, test_ssh_connection, send_test_email, stream_install_agent, stream_restart_agent, stream_install_unsubscribe, get_agent_logs

router = APIRouter(prefix="/api/nodes", tags=["nodes"])
logger = logging.getLogger(__name__)


def _check_dns(domain: str, node_ip: str, dkim_selector: str, dkim_record: str, dmarc_record: str) -> list:
    """Check DNS records for a node and return list of results."""
    results = []
    resolver = dns.resolver.Resolver()
    resolver.timeout = 5
    resolver.lifetime = 5

    def check(label: str, record_type: str, name: str, expect_fn):
        try:
            answers = resolver.resolve(name, record_type)
            values = [r.to_text().strip('"') for r in answers]
            ok, detail = expect_fn(values)
            results.append({"label": label, "name": name, "type": record_type, "ok": ok, "detail": detail, "values": values})
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            results.append({"label": label, "name": name, "type": record_type, "ok": False, "detail": "Registro não encontrado", "values": []})
        except dns.exception.Timeout:
            results.append({"label": label, "name": name, "type": record_type, "ok": False, "detail": "Timeout ao consultar DNS", "values": []})
        except Exception as e:
            results.append({"label": label, "name": name, "type": record_type, "ok": False, "detail": str(e), "values": []})

    # A record
    check("A (mail)", "A", f"mail.{domain}",
          lambda vals: (any(v == node_ip for v in vals), f"Encontrado: {', '.join(vals)}" if vals else "Não encontrado"))

    # MX record
    check("MX", "MX", domain,
          lambda vals: (any(f"mail.{domain}" in v for v in vals), f"Encontrado: {', '.join(vals)}" if vals else "Não encontrado"))

    # SPF
    check("TXT (SPF)", "TXT", domain,
          lambda vals: (any(v.startswith("v=spf1") for v in vals),
                        next((v for v in vals if v.startswith("v=spf1")), "SPF não encontrado")))

    # DKIM
    if dkim_selector:
        expected_key = None
        if dkim_record:
            import re
            m = re.search(r'p=([A-Za-z0-9+/=]+)', dkim_record)
            if m:
                expected_key = m.group(1)[:20]  # primeiros 20 chars pra comparar

        def check_dkim(vals):
            joined = " ".join(vals)
            if not any("v=DKIM1" in v for v in vals):
                return False, "DKIM não encontrado"
            if expected_key and expected_key not in joined:
                return False, "DKIM encontrado mas chave diferente (DNS desatualizado?)"
            return True, "DKIM válido"

        check("TXT (DKIM)", "TXT", f"{dkim_selector}._domainkey.{domain}", check_dkim)

    # DMARC
    check("TXT (DMARC)", "TXT", f"_dmarc.{domain}",
          lambda vals: (any("v=DMARC1" in v for v in vals),
                        next((v for v in vals if "v=DMARC1" in v), "DMARC não encontrado")))

    return results


@router.get("/{node_id}/verify-dns")
async def verify_dns(node_id: int, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    linked_domain = session.get(CloudflareDomain, node.cloudflare_domain_id) if node.cloudflare_domain_id else None
    domain_name = linked_domain.domain if linked_domain else node.domain
    if not domain_name:
        raise HTTPException(status_code=400, detail="Domínio não configurado")
    results = _check_dns(domain_name, node.ip, node.dkim_selector, node.dkim_dns_record, node.dmarc_dns_record)
    return {"results": results}


@router.post("/{node_id}/provision-cloudflare")
def provision_cloudflare_dns(node_id: int, stage: str = "initial", session: Session = Depends(get_session)):
    try:
        node = session.get(Node, node_id)
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")
        if not node.domain:
            raise HTTPException(status_code=400, detail="Domínio não configurado")

        linked_domain = session.get(CloudflareDomain, node.cloudflare_domain_id) if node.cloudflare_domain_id else None
        domain_name = linked_domain.domain if linked_domain else node.domain

        token = ""
        if linked_domain and linked_domain.account_id:
            account = session.get(CloudflareAccount, linked_domain.account_id)
            if account and account.api_token:
                token = account.api_token.strip()

        if not token:
            cfg = session.get(CloudflareConfig, 1)
            token = (cfg.api_token if cfg and cfg.api_token else os.getenv("CLOUDFLARE_API_TOKEN", "")).strip()

        if not token:
            raise HTTPException(status_code=400, detail="Token Cloudflare não configurado (aba Cloudflare)")

        zone_id = linked_domain.zone_id.strip() if linked_domain and linked_domain.zone_id else (cfg.zone_id.strip() if cfg and cfg.zone_id else None)
        try:
            if not zone_id:
                zone_id = cf_find_zone_id(token, domain_name)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        ttl = 1
        if stage not in {"initial", "post_bootstrap"}:
            raise HTTPException(status_code=400, detail="Stage inválido")

        base_records = [
            {"type": "A", "name": f"mail.{domain_name}", "content": node.ip, "ttl": ttl, "proxied": False},
            {"type": "A", "name": domain_name, "content": node.ip, "ttl": ttl, "proxied": False},
        ]

        if stage == "post_bootstrap":
            base_records.extend(
                [
                    {"type": "MX", "name": domain_name, "content": f"mail.{domain_name}", "priority": 10, "ttl": ttl},
                    {"type": "TXT", "name": domain_name, "content": f"v=spf1 mx ip4:{node.ip} ~all", "ttl": ttl},
                ]
            )
            if node.dkim_selector and node.dkim_dns_record:
                base_records.append(
                    {
                        "type": "TXT",
                        "name": f"{node.dkim_selector}._domainkey.{domain_name}",
                        "content": node.dkim_dns_record,
                        "ttl": ttl,
                    }
                )
            if node.dmarc_dns_record:
                base_records.append(
                    {
                        "type": "TXT",
                        "name": f"_dmarc.{domain_name}",
                        "content": node.dmarc_dns_record,
                        "ttl": ttl,
                    }
                )

        results = []
        for record in base_records:
            try:
                upsert = cf_upsert_record(token, zone_id, record)
            except RuntimeError as e:
                raise HTTPException(status_code=502, detail=str(e))
            results.append(
                {
                    "type": record["type"],
                    "name": record["name"],
                    "content": record["content"],
                    "status": upsert["status"],
                }
            )
        return {"success": True, "zone_id": zone_id, "records": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error provisioning cloudflare dns for node %s", node_id)
        raise HTTPException(status_code=500, detail="Erro inesperado ao provisionar DNS") from e



@router.post("", response_model=NodeRead)
def create_node(node: NodeCreate, session: Session = Depends(get_session)):
    if node.cloudflare_domain_id:
        linked_domain = session.get(CloudflareDomain, node.cloudflare_domain_id)
        if not linked_domain:
            raise HTTPException(status_code=400, detail="Domínio Cloudflare vinculado não encontrado")
        node.domain = linked_domain.domain
    db_node = Node.model_validate(node)
    session.add(db_node)
    session.commit()
    session.refresh(db_node)
    return db_node


@router.get("", response_model=List[NodeRead])
def list_nodes(session: Session = Depends(get_session)):
    return session.exec(select(Node)).all()


@router.get("/{node_id}", response_model=NodeRead)
def get_node(node_id: int, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


@router.delete("/{node_id}", status_code=204)
def delete_node(node_id: int, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    session.delete(node)
    session.commit()


@router.patch("/{node_id}", response_model=NodeRead)
def update_node(node_id: int, patch: NodeUpdate, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    patch_data = patch.model_dump(exclude_unset=True)
    if "cloudflare_domain_id" in patch_data:
        domain_id = patch_data["cloudflare_domain_id"]
        if domain_id is None:
            patch_data["domain"] = None
        else:
            linked_domain = session.get(CloudflareDomain, domain_id)
            if not linked_domain:
                raise HTTPException(status_code=400, detail="Domínio Cloudflare vinculado não encontrado")
            patch_data["domain"] = linked_domain.domain
    for field, value in patch_data.items():
        setattr(node, field, value)
    session.add(node)
    session.commit()
    session.refresh(node)
    return node


@router.post("/{node_id}/test-ssh")
async def test_ssh(node_id: int, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return await test_ssh_connection(node)


@router.post("/{node_id}/bootstrap")
async def bootstrap(node_id: int, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if not node.domain:
        raise HTTPException(status_code=400, detail="Domínio não configurado para este nó")
    if not node.email_from:
        raise HTTPException(status_code=400, detail="Email remetente não configurado para este nó")

    async def generate():
        import json as _json
        final_result = None
        async for line in stream_bootstrap(node):
            yield line
            try:
                evt = _json.loads(line)
                if evt.get("type") == "done":
                    final_result = evt
            except Exception:
                pass
        # persist result after stream ends
        if final_result:
            with Session(session.get_bind()) as s:
                db_node = s.get(Node, node_id)
                if db_node:
                    db_node.bootstrap_status = "success" if final_result.get("success") else "failed"
                    db_node.bootstrap_log = final_result.get("log", "")
                    db_node.dkim_selector = final_result.get("dkim_selector")
                    db_node.dkim_dns_record = final_result.get("dkim_dns_record")
                    db_node.dmarc_dns_record = final_result.get("dmarc_dns_record")
                    s.add(db_node)
                    s.commit()

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.post("/{node_id}/generate-token", response_model=NodeRead)
def generate_agent_token(node_id: int, session: Session = Depends(get_session)):
    """Generate (or regenerate) the agent auth token for a node."""
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    node.agent_token = generate_token()
    session.add(node)
    session.commit()
    session.refresh(node)
    return node


@router.post("/{node_id}/send-test")
async def send_test(node_id: int, payload: dict, session: Session = Depends(get_session)):
    """Send a test email via the node's local Postfix over SSH."""
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    to_address = payload.get("to")
    if not to_address:
        raise HTTPException(status_code=400, detail="Campo 'to' obrigatório")
    return await send_test_email(node, to_address)


@router.post("/{node_id}/install-agent")
async def install_agent(node_id: int, payload: dict, session: Session = Depends(get_session)):
    """Install the Go agent on the node via SSH."""
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    panel_url = payload.get("panel_url", "").strip()
    if not panel_url:
        raise HTTPException(status_code=400, detail="Campo 'panel_url' obrigatório")

    # Generate token if not exists
    if not node.agent_token:
        node.agent_token = generate_token()
        session.add(node)
        session.commit()
        session.refresh(node)

    token = node.agent_token

    async def generate():
        import json as _json
        async for line in stream_install_agent(node, token, panel_url):
            yield line
            try:
                evt = _json.loads(line)
                if evt.get("type") == "done" and evt.get("success"):
                    with Session(session.get_bind()) as s:
                        db_node = s.get(Node, node_id)
                        if db_node:
                            db_node.agent_status = "online" if evt.get("agent_running") else "installing"
                            db_node.agent_panel_url = panel_url
                            s.add(db_node)
                            s.commit()
            except Exception:
                pass

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.post("/{node_id}/restart-agent")
async def restart_agent(node_id: int, payload: dict, session: Session = Depends(get_session)):
    """Restart the Go agent with new panel URL via SSH."""
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    panel_url = payload.get("panel_url", "").strip()
    if not panel_url:
        raise HTTPException(status_code=400, detail="Campo 'panel_url' obrigatório")

    if not node.agent_token:
        raise HTTPException(status_code=400, detail="Agent not installed yet")

    token = node.agent_token

    async def generate():
        import json as _json
        async for line in stream_restart_agent(node, token, panel_url):
            yield line
            try:
                evt = _json.loads(line)
                if evt.get("type") == "done" and evt.get("success"):
                    with Session(session.get_bind()) as s:
                        db_node = s.get(Node, node_id)
                        if db_node:
                            db_node.agent_status = "online"
                            db_node.agent_panel_url = panel_url
                            s.add(db_node)
                            s.commit()
            except Exception:
                pass

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.post("/{node_id}/install-unsubscribe")
async def install_unsubscribe(node_id: int, payload: dict, session: Session = Depends(get_session)):
    """Configure nginx on the VPS to proxy /unsubscribe to the panel."""
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    panel_url = payload.get("panel_url", "").strip()
    if not panel_url:
        raise HTTPException(status_code=400, detail="Campo 'panel_url' obrigatório")

    if not node.domain:
        raise HTTPException(status_code=400, detail="VPS sem domínio configurado")

    async def generate():
        async for line in stream_install_unsubscribe(node, panel_url):
            yield line

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.post("/{node_id}/tasks", response_model=TaskRead)
def create_task(node_id: int, task: TaskCreate, session: Session = Depends(get_session)):
    import json as _json
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    db_task = Task(
        node_id=node_id,
        subject=task.subject,
        body=task.body,
        html=task.html,
        plain_text=task.plain_text,
        from_address=task.from_address,
        recipients=_json.dumps(task.recipients),
        rate_per_hour=task.rate_per_hour,
        unsubscribe_url=task.unsubscribe_url,
        feedback_id=task.feedback_id,
        cta_url=task.cta_url,
    )
    session.add(db_task)
    session.commit()
    session.refresh(db_task)
    return db_task


@router.get("/{node_id}/tasks", response_model=List[TaskRead])
def list_tasks(node_id: int, session: Session = Depends(get_session)):
    return session.exec(select(Task).where(Task.node_id == node_id).order_by(Task.created_at.desc())).all()


@router.get("/{node_id}/agent-logs")
async def agent_logs(node_id: int, lines: int = 150, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    result = await get_agent_logs(node, lines=lines)
    return result
