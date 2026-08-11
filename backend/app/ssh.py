import io
import os
import random
import tarfile
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from typing import Optional

import asyncssh

from app.models import Node

AGENT_SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "agent")


def _connect_kwargs(node: Node) -> dict:
    kwargs = dict(
        host=node.ip,
        port=node.ssh_port,
        username=node.ssh_user,
        known_hosts=None,
    )
    if node.auth_method == "key":
        kwargs["client_keys"] = [asyncssh.import_private_key(node.ssh_private_key)]
    else:
        kwargs["password"] = node.ssh_password
    return kwargs


def _generate_protocol() -> str:
    digits = list("0123456789")
    random.SystemRandom().shuffle(digits)
    return "".join(digits)


def _replace_tags(value: str, *, to_address: str, cta_url: Optional[str], unsubscribe_url: Optional[str], protocol: str, subject: Optional[str] = None) -> str:
    if not value:
        return value
    domain = to_address.split("@", 1)[1] if "@" in to_address else ""

    # Fuso horário de Brasília (UTC-3)
    brt = timezone(timedelta(hours=-3))
    now = datetime.now(brt)
    data_str = now.strftime("%d/%m/%Y")
    hora_str = now.strftime("%H:%M:%S")
    hora_curta_str = now.strftime("%H:%M")

    cta_resolved = cta_url or ""
    if cta_resolved:
        cta_resolved = cta_resolved.replace("{{email}}", to_address)
        cta_resolved = cta_resolved.replace("{{domain}}", domain)
        cta_resolved = cta_resolved.replace("{{protocol}}", protocol)
        cta_resolved = cta_resolved.replace("{{data}}", data_str)
        cta_resolved = cta_resolved.replace("{{date}}", data_str)
        cta_resolved = cta_resolved.replace("{{hora}}", hora_str)
        cta_resolved = cta_resolved.replace("{{time}}", hora_str)
        if subject is not None:
            cta_resolved = cta_resolved.replace("{{subject}}", subject)

    unsub_resolved = unsubscribe_url or ""
    if unsub_resolved:
        unsub_resolved = unsub_resolved.replace("{{email}}", to_address)
        unsub_resolved = unsub_resolved.replace("{{domain}}", domain)
        unsub_resolved = unsub_resolved.replace("{{protocol}}", protocol)

    if cta_resolved:
        value = value.replace("{{cta_url}}", cta_resolved)
    if unsub_resolved:
        value = value.replace("{{unsubscribe_url}}", unsub_resolved)
    value = value.replace("{{email}}", to_address)
    value = value.replace("{{domain}}", domain)
    value = value.replace("{{protocol}}", protocol)
    value = value.replace("{{data}}", data_str)
    value = value.replace("{{date}}", data_str)
    value = value.replace("{{hora}}", hora_str)
    value = value.replace("{{hora_curta}}", hora_curta_str)
    value = value.replace("{{time}}", hora_str)
    if subject is not None:
        value = value.replace("{{subject}}", subject)
    return value


async def get_agent_logs(node: Node, lines: int = 150) -> dict:
    """Fetch the last N lines from the smtpagent systemd service log."""
    try:
        async with asyncssh.connect(**_connect_kwargs(node)) as conn:
            result = await conn.run(
                f"journalctl -u smtpagent -n {lines} --no-pager 2>&1 || "
                f"journalctl -u smtp-agent -n {lines} --no-pager 2>&1 || "
                f"systemctl status smtpagent 2>&1",
                check=False,
            )
            output = (result.stdout or "") + (result.stderr or "")
            return {"success": True, "output": output.strip()}
    except (asyncssh.Error, OSError) as exc:
        return {"success": False, "output": str(exc)}


async def get_postfix_stats(node: Node, since=None, until: Optional[str] = None) -> dict:
    """
    SSH into a node and count sent/bounced/deferred from /var/log/mail.log using grep.
    If `since` is a datetime, only counts entries at or after that timestamp.
    Returns: {success, sent, bounced, deferred, top_reasons}
    """
    try:
        async with asyncssh.connect(**_connect_kwargs(node)) as conn:
            # Build awk date filter if since is provided
            if since is not None:
                # Format: "Aug 10 15:30" — mail.log uses syslog format "Mon DD HH:MM:SS"
                # We filter lines where the timestamp is >= since
                # awk compares month/day/time strings lexicographically with padding
                since_str = since.strftime("%b %_d %H:%M:%S").replace("  ", " ")
                # Use awk to filter: keep lines where timestamp >= since_str
                log_source = (
                    f"awk -v since='{since_str}' "
                    f"'{{ts=$1\" \"$2\" \"$3; if (ts >= since) print}}' /var/log/mail.log 2>/dev/null"
                )
            else:
                log_source = "cat /var/log/mail.log 2>/dev/null"

            script = (
                f"LOG_DATA=$({log_source}); "
                "SENT=$(echo \"$LOG_DATA\" | grep -c 'status=sent' || echo 0); "
                "BOUNCED=$(echo \"$LOG_DATA\" | grep -c 'status=bounced' || echo 0); "
                "DEFERRED=$(echo \"$LOG_DATA\" | grep -c 'status=deferred' || echo 0); "
                "echo SENT=$SENT; "
                "echo BOUNCED=$BOUNCED; "
                "echo DEFERRED=$DEFERRED; "
                # Top bounce reasons from DSN lines
                "echo \"$LOG_DATA\" | grep 'status=bounced' | grep -oP '\\(.*?\\)' | sort | uniq -c | sort -rn | head -5 | while read cnt reason; do echo \"REASON:$cnt $reason\"; done; "
                "true"
            )
            result = await conn.run(script, check=False)
            output = (result.stdout or "").strip()

            sent = bounced = deferred = 0
            reasons_list = []
            for line in output.splitlines():
                if line.startswith("SENT="):
                    try:
                        sent = int(line.split("=", 1)[1].strip())
                    except ValueError:
                        sent = 0
                elif line.startswith("BOUNCED="):
                    try:
                        bounced = int(line.split("=", 1)[1].strip())
                    except ValueError:
                        bounced = 0
                elif line.startswith("DEFERRED="):
                    try:
                        deferred = int(line.split("=", 1)[1].strip())
                    except ValueError:
                        deferred = 0
                elif line.startswith("REASON:"):
                    reasons_list.append(line[7:])

            return {
                "success": True,
                "node_id": node.id,
                "hostname": node.hostname,
                "sent": sent,
                "bounced": bounced,
                "deferred": deferred,
                "top_reasons": reasons_list,
            }
    except Exception as exc:
        return {
            "success": False,
            "node_id": node.id,
            "hostname": node.hostname,
            "sent": 0, "bounced": 0, "deferred": 0,
            "top_reasons": [],
            "error": str(exc),
        }


async def get_raw_postfix_log(node: Node) -> bytes:
    """
    SSH into a node and fetch the raw contents of /var/log/mail.log.
    Returns bytes so it can be assembled into a tar.gz.
    """
    try:
        async with asyncssh.connect(**_connect_kwargs(node)) as conn:
            result = await conn.run("cat /var/log/mail.log", check=False)
            if result.stdout:
                return result.stdout.encode('utf-8', errors='ignore') if isinstance(result.stdout, str) else result.stdout
            return b""
    except Exception:
        return b""


async def test_ssh_connection(node: Node) -> dict:
    if node.auth_method == "key" and not node.ssh_private_key:
        return {"success": False, "message": "Nenhuma chave privada cadastrada", "output": None}
    if node.auth_method == "password" and not node.ssh_password:
        return {"success": False, "message": "Nenhuma senha cadastrada", "output": None}

    try:
        async with asyncssh.connect(**_connect_kwargs(node)) as conn:
            result = await conn.run("uname -a", check=False)
            return {
                "success": True,
                "message": "Conexão bem-sucedida",
                "output": result.stdout.strip(),
            }
    except (asyncssh.Error, OSError) as exc:
        return {"success": False, "message": str(exc), "output": None}


def _opendkim_conf() -> str:
    return """AutoRestart             Yes
AutoRestartRate         10/1h
UMask                   002
Syslog                  yes
SyslogSuccess           Yes
LogWhy                  Yes
Canonicalization        relaxed/simple
ExternalIgnoreList      refile:/etc/opendkim/TrustedHosts
InternalHosts           refile:/etc/opendkim/TrustedHosts
KeyTable                refile:/etc/opendkim/KeyTable
SigningTable            refile:/etc/opendkim/SigningTable
Mode                    sv
PidFile                 /var/run/opendkim/opendkim.pid
SignatureAlgorithm      rsa-sha256
UserID                  opendkim:opendkim
Socket                  inet:8891@localhost
"""


def _dmarc_record(domain: str) -> str:
    return f"v=DMARC1; p=none; rua=mailto:postmaster@{domain}; adkim=r; aspf=r"


def _parse_dkim_txt(raw_bind_record: str) -> str:
    """Extract just the TXT value from BIND zone file format, without quotes."""
    if not raw_bind_record:
        return None
    import re
    # BIND format wraps the key in quoted strings: "part1" "part2" ...
    # Extract all quoted segments and join them
    parts = re.findall(r'"([^"]*)"', raw_bind_record)
    if parts:
        return "".join(parts)
    return raw_bind_record


async def stream_bootstrap(node: Node):
    """Async generator that yields JSON-lines during bootstrap execution."""
    import json

    def event(type: str, label: str, **kwargs) -> str:
        return json.dumps({"type": type, "label": label, **kwargs}) + "\n"

    if not node.domain:
        yield event("error", "validação", message="Domínio não configurado")
        return
    if not node.email_from:
        yield event("error", "validação", message="Email remetente não configurado")
        return

    domain = node.domain
    selector = f"smtp{node.id}"
    key_dir = f"/etc/opendkim/keys/{domain}"
    mail_host = f"mail.{domain}"
    admin_email = node.email_from
    ssl_dir = "/etc/postfix/ssl"
    self_signed_cert = f"{ssl_dir}/{mail_host}.crt"
    self_signed_key = f"{ssl_dir}/{mail_host}.key"
    le_dir = f"/etc/letsencrypt/live/{mail_host}"

    log_lines = []

    STEP_LABELS = {
        "cleanup": "Limpando instalações antigas (Postfix/OpenDKIM)",
        "preseed": "Pré-configurando Postfix",
        "apt-update": "Atualizando pacotes",
        "install": "Instalando Postfix + OpenDKIM + Certbot",
        "hostname": "Configurando hostname",
        "dkim-genkey": "Gerando chave DKIM 2048 bits",
        "opendkim-conf": "Configurando OpenDKIM",
        "opendkim-trustedhosts": "Trusted hosts",
        "opendkim-keytable": "Key table",
        "opendkim-signingtable": "Signing table",
        "opendkim-default": "Socket OpenDKIM",
        "selfsigned-cert": "Gerando certificado self-signed",
        "certbot": "Obtendo certificado Let's Encrypt",
        "postfix-config": "Aplicando configurações Postfix",
        "restart-opendkim": "Reiniciando OpenDKIM",
        "restart-postfix": "Reiniciando Postfix",
        "read-dkim-record": "Lendo chave DKIM pública",
    }

    try:
        async with asyncssh.connect(**_connect_kwargs(node)) as conn:

            async def run_step(label: str, cmd: str):
                result = await conn.run(cmd, check=False)
                log_lines.append(f"--- {label} (exit {result.exit_status}) ---")
                if result.stdout:
                    log_lines.append(result.stdout.strip())
                if result.stderr:
                    log_lines.append(result.stderr.strip())
                return result

            critical_steps = [
                (
                    "cleanup",
                    "systemctl stop postfix opendkim >/dev/null 2>&1 || true; "
                    "apt-get purge -y postfix opendkim opendkim-tools >/dev/null 2>&1 || true; "
                    "rm -rf /etc/postfix /etc/opendkim /etc/opendkim.conf /etc/default/opendkim"
                ),
                (
                    "preseed",
                    f"echo 'postfix postfix/main_mailer_type select Internet Site' | debconf-set-selections && "
                    f"echo 'postfix postfix/mailname string {domain}' | debconf-set-selections",
                ),
                ("apt-update", "apt-get update -y"),
                (
                    "install",
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y "
                    "postfix opendkim opendkim-tools certbot openssl",
                ),
                (
                    "hostname",
                    f"hostnamectl set-hostname {mail_host} && "
                    f"(grep -qxF '127.0.1.1 {mail_host} mail' /etc/hosts || "
                    f"echo '127.0.1.1 {mail_host} mail' >> /etc/hosts)",
                ),
                (
                    "dkim-genkey",
                    f"mkdir -p {key_dir} && opendkim-genkey -b 2048 -d {domain} -D {key_dir} -s {selector} -v && "
                    f"chown -R opendkim:opendkim {key_dir}",
                ),
                (
                    "opendkim-conf",
                    f"cat <<'EOF' > /etc/opendkim.conf\n{_opendkim_conf()}EOF",
                ),
                (
                    "opendkim-trustedhosts",
                    f"cat <<EOF > /etc/opendkim/TrustedHosts\n127.0.0.1\nlocalhost\n{domain}\nEOF",
                ),
                (
                    "opendkim-keytable",
                    f"cat <<EOF > /etc/opendkim/KeyTable\n{selector}._domainkey.{domain} {domain}:{selector}:{key_dir}/{selector}.private\nEOF",
                ),
                (
                    "opendkim-signingtable",
                    f"cat <<EOF > /etc/opendkim/SigningTable\n*@{domain} {selector}._domainkey.{domain}\nEOF",
                ),
                (
                    "opendkim-default",
                    "cat <<'EOF' > /etc/default/opendkim\nSOCKET=\"inet:8891@localhost\"\nEOF",
                ),
                (
                    "selfsigned-cert",
                    f"mkdir -p {ssl_dir} && openssl req -new -x509 -days 3650 -nodes "
                    f"-out {self_signed_cert} -keyout {self_signed_key} -subj '/CN={mail_host}'",
                ),
            ]

            for label, cmd in critical_steps:
                friendly = STEP_LABELS.get(label, label)
                yield event("running", label, message=friendly)
                result = await run_step(label, cmd)
                if result.exit_status != 0:
                    yield event("error", label, message=f"Falhou: {friendly}")
                    yield event("done", "bootstrap", success=False, message=f"Falhou no passo: {label}", log="\n".join(log_lines))
                    return
                yield event("ok", label, message=friendly)

            # TLS — não crítico
            friendly_certbot = STEP_LABELS["certbot"]
            yield event("running", "certbot", message=friendly_certbot)
            certbot_cmd = (
                f"certbot certonly --standalone --non-interactive --agree-tos "
                f"-m {admin_email} -d {mail_host} --no-eff-email"
            )
            certbot_result = await run_step("certbot", certbot_cmd)
            if certbot_result.exit_status == 0:
                cert_file, key_file, tls_provider = f"{le_dir}/fullchain.pem", f"{le_dir}/privkey.pem", "letsencrypt"
                yield event("ok", "certbot", message="Let's Encrypt obtido")
            else:
                cert_file, key_file, tls_provider = self_signed_cert, self_signed_key, "self-signed"
                yield event("warn", "certbot", message="Let's Encrypt falhou — usando self-signed")

            yield event("running", "postfix-config", message=STEP_LABELS["postfix-config"])
            postconf_cmd = (
                f"postconf -e 'myhostname={mail_host}' 'myorigin={domain}' 'mydestination=localhost' "
                "'smtpd_milters=inet:localhost:8891' 'non_smtpd_milters=inet:localhost:8891' "
                "'milter_default_action=accept' 'milter_protocol=6' "
                f"'smtpd_tls_cert_file={cert_file}' 'smtpd_tls_key_file={key_file}' "
                "'smtpd_tls_security_level=may' 'smtp_tls_security_level=may' "
                "'smtpd_tls_protocols=!SSLv2,!SSLv3,!TLSv1,!TLSv1.1' "
                "'smtp_tls_protocols=!SSLv2,!SSLv3,!TLSv1,!TLSv1.1' "
                "'mynetworks=127.0.0.0/8 [::1]/128' "
                "'smtpd_relay_restrictions=permit_mynetworks,reject_unauth_destination' "
                "'smtpd_banner=$myhostname ESMTP' 'disable_vrfy_command=yes'"
            )
            result = await run_step("postfix-config", postconf_cmd)
            if result.exit_status != 0:
                yield event("error", "postfix-config", message="Falhou: configuração Postfix")
                yield event("done", "bootstrap", success=False, message="Falhou no passo: postfix-config", log="\n".join(log_lines))
                return
            yield event("ok", "postfix-config", message=STEP_LABELS["postfix-config"])

            for label, cmd in [
                ("restart-opendkim", "systemctl restart opendkim"),
                ("restart-postfix", "systemctl restart postfix"),
            ]:
                yield event("running", label, message=STEP_LABELS[label])
                result = await run_step(label, cmd)
                if result.exit_status != 0:
                    yield event("error", label, message=f"Falhou: {STEP_LABELS[label]}")
                    yield event("done", "bootstrap", success=False, message=f"Falhou no passo: {label}", log="\n".join(log_lines))
                    return
                yield event("ok", label, message=STEP_LABELS[label])

            yield event("running", "read-dkim-record", message=STEP_LABELS["read-dkim-record"])
            dkim_result = await run_step("read-dkim-record", f"cat {key_dir}/{selector}.txt")
            raw_dkim_record = dkim_result.stdout.strip() if dkim_result.exit_status == 0 else None
            dkim_dns_record = _parse_dkim_txt(raw_dkim_record) if raw_dkim_record else None
            yield event("ok", "read-dkim-record", message=STEP_LABELS["read-dkim-record"])

            yield event(
                "done", "bootstrap",
                success=True,
                message="Bootstrap concluído",
                log="\n".join(log_lines),
                dkim_selector=selector,
                dkim_dns_record=dkim_dns_record,
                dmarc_dns_record=_dmarc_record(domain),
                tls_provider=tls_provider,
            )

    except (asyncssh.Error, OSError) as exc:
        yield event("error", "ssh", message=str(exc))
        yield event("done", "bootstrap", success=False, message=str(exc), log="\n".join(log_lines))


async def send_test_email(
    node: Node,
    to_address: str,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    html: Optional[str] = None,
    cta_url: Optional[str] = None,
) -> dict:
    """Send a single test email from the node's Postfix via SSH."""
    if not node.email_from:
        return {"success": False, "message": "Email remetente não configurado"}
    if not node.domain:
        return {"success": False, "message": "Domínio não configurado"}

    from_addr = node.email_from
    subject = (subject or f"Teste SMTP — {node.hostname}").strip() or f"Teste SMTP — {node.hostname}"
    protocol = _generate_protocol()
    unsubscribe_url = f"https://{node.domain}/unsubscribe?email={to_address}" if node.domain else None
    subject = _replace_tags(subject, to_address=to_address, cta_url=cta_url, unsubscribe_url=unsubscribe_url, protocol=protocol)
    plain_text = (body or "").strip() or (
        f"Este é um email de teste enviado pelo SMTP Fleet Panel.\n\n"
        f"Servidor: {node.hostname} ({node.ip})\n"
        f"Domínio:  {node.domain}\n"
        f"Remetente: {from_addr}\n"
    )
    html_text = (html or "").strip()
    plain_text = _replace_tags(plain_text, to_address=to_address, cta_url=cta_url, unsubscribe_url=unsubscribe_url, protocol=protocol, subject=subject)
    html_text = _replace_tags(html_text, to_address=to_address, cta_url=cta_url, unsubscribe_url=unsubscribe_url, protocol=protocol, subject=subject)

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_address
    msg["Subject"] = subject

    if html_text:
        msg.set_content(plain_text, subtype="plain", charset="utf-8", cte="quoted-printable")
        msg.add_alternative(html_text, subtype="html", charset="utf-8", cte="quoted-printable")
    else:
        msg.set_content(plain_text, charset="utf-8", cte="quoted-printable")

    msg_bytes = msg.as_bytes(policy=policy.SMTP)
    temp_path = f"/tmp/smtp-fleet-test-{os.urandom(8).hex()}.eml"
    cmd = (
        "python3 -c "
        "\"import pathlib, smtplib; "
        f"data = pathlib.Path('{temp_path}').read_bytes(); "
        f"smtp = smtplib.SMTP('127.0.0.1', 25); "
        f"smtp.sendmail('{from_addr}', ['{to_address}'], data); "
        "smtp.quit()\""
    )

    try:
        async with asyncssh.connect(**_connect_kwargs(node)) as conn:
            try:
                async with conn.start_sftp_client() as sftp:
                    async with sftp.open(temp_path, "wb") as file_obj:
                        await file_obj.write(msg_bytes)

                result = await conn.run(cmd, check=False)
                if result.exit_status == 0:
                    return {"success": True, "message": f"Email enviado para {to_address}"}
                err = (result.stderr or "").strip() or f"exit code {result.exit_status}"
                return {"success": False, "message": f"Erro ao enviar: {err}"}
            finally:
                await conn.run(f"rm -f {temp_path}", check=False)
    except (asyncssh.Error, OSError) as exc:
        return {"success": False, "message": str(exc)}


async def stream_install_agent(node: Node, token: str, panel_url: str):
    """Upload Go source to VPS, compile, install as systemd service."""
    import json as _json

    def event(type: str, label: str, **kwargs) -> str:
        return _json.dumps({"type": type, "label": label, **kwargs}) + "\n"

    remote_dir = "/opt/smtpagent"
    binary_path = "/usr/local/bin/smtpagent"
    service_name = "smtpagent"

    systemd_unit = f"""[Unit]
Description=SMTP Fleet Agent
After=network.target postfix.service

[Service]
Type=simple
Environment="PANEL_URL={panel_url}"
Environment="NODE_TOKEN={token}"
ExecStart={binary_path} -panel {panel_url} -node-id {node.id} -token {token}
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

    try:
        agent_dir = os.path.abspath(AGENT_SRC_DIR)
        if not os.path.isdir(agent_dir):
            yield event("error", "agent-src", message="Pasta 'agent' não encontrada no painel. Atualize a VPS do painel.")
            yield event("done", "install-agent", success=False, message="Código do agente ausente.")
            return

        go_files = [f for f in os.listdir(agent_dir) if f.endswith(".go")]
        go_mod = os.path.join(agent_dir, "go.mod")

        async with asyncssh.connect(**_connect_kwargs(node)) as conn:

            async def run(label: str, cmd: str):
                result = await conn.run(cmd, check=False)
                return result

            # 1. Install Go if not present
            yield event("running", "go-check", message="Verificando Go na VPS")
            go_check = await run("go-check", "which go || echo MISSING")
            if "MISSING" in (go_check.stdout or ""):
                yield event("running", "go-install", message="Instalando Go (pode demorar ~30s)")
                r = await run("go-install",
                    "apt-get install -y golang-go 2>&1 | tail -5")
                if r.exit_status != 0:
                    yield event("error", "go-install", message="Falhou ao instalar Go")
                    yield event("done", "install-agent", success=False, message="Go não disponível")
                    return
                yield event("ok", "go-install", message="Go instalado")
            else:
                yield event("ok", "go-check", message=f"Go encontrado: {(go_check.stdout or '').strip()}")

            # 2. Upload source files via SFTP
            yield event("running", "upload", message="Enviando código-fonte do agente")
            async with conn.start_sftp_client() as sftp:
                try:
                    await sftp.mkdir(remote_dir)
                except asyncssh.SFTPError:
                    pass  # already exists

                for fname in go_files:
                    local_path = os.path.join(agent_dir, fname)
                    await sftp.put(local_path, f"{remote_dir}/{fname}")

                await sftp.put(go_mod, f"{remote_dir}/go.mod")
            yield event("ok", "upload", message=f"Arquivos enviados: {', '.join(go_files)}")

            # 3. Compile
            yield event("running", "compile", message="Compilando agente (pode demorar ~1 min)")
            r = await run("compile", f"cd {remote_dir} && go build -o {binary_path} . 2>&1")
            if r.exit_status != 0:
                err = (r.stdout or r.stderr or "").strip()
                yield event("error", "compile", message=f"Falha na compilação: {err[:200]}")
                yield event("done", "install-agent", success=False, message="Compilação falhou")
                return
            yield event("ok", "compile", message="Binário compilado em /usr/local/bin/smtpagent")

            # 4. Install systemd service
            yield event("running", "systemd", message="Instalando serviço systemd")
            unit_escaped = systemd_unit.replace("'", "'\\''")
            r = await run("systemd", f"cat <<'UNIT' > /etc/systemd/system/{service_name}.service\n{systemd_unit}UNIT")
            if r.exit_status != 0:
                yield event("error", "systemd", message="Falha ao criar unit file")
                yield event("done", "install-agent", success=False, message="Falha no systemd")
                return

            await run("reload", "systemctl daemon-reload")
            r = await run("enable", f"systemctl enable {service_name}")
            r = await run("start", f"systemctl restart {service_name}")
            if r.exit_status != 0:
                yield event("warn", "start", message="Serviço não iniciou (verifique panel_url)")
            else:
                yield event("ok", "systemd", message="smtpagent.service ativo e habilitado")

            # 5. Verify
            await conn.run("sleep 2", check=False)
            status = await run("status", f"systemctl is-active {service_name}")
            active = (status.stdout or "").strip() == "active"

            yield event("done", "install-agent", success=True,
                        message="Agente instalado com sucesso",
                        agent_running=active)

    except (asyncssh.Error, OSError) as exc:
        yield event("error", "ssh", message=str(exc))
        yield event("done", "install-agent", success=False, message=str(exc))


async def stream_restart_agent(node: Node, token: str, panel_url: str):
    """Stop agent, recompile with new URL, restart."""
    import json as _json

    def event(type: str, label: str, **kwargs) -> str:
        return _json.dumps({"type": type, "label": label, **kwargs}) + "\n"

    agent_dir = os.path.abspath(AGENT_SRC_DIR)
    go_files = [f for f in os.listdir(agent_dir) if f.endswith(".go")]
    go_mod = os.path.join(agent_dir, "go.mod")

    remote_dir = "/opt/smtpagent"
    binary_path = "/usr/local/bin/smtpagent"
    service_name = "smtpagent"

    systemd_unit = f"""[Unit]
Description=SMTP Fleet Agent
After=network.target postfix.service

[Service]
Type=simple
Environment="PANEL_URL={panel_url}"
Environment="NODE_TOKEN={token}"
ExecStart={binary_path} -panel {panel_url} -node-id {node.id} -token {token}
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

    try:
        async with asyncssh.connect(**_connect_kwargs(node)) as conn:

            async def run(label: str, cmd: str):
                result = await conn.run(cmd, check=False)
                return result

            # 1. Stop service
            yield event("running", "stop", message="Parando serviço")
            r = await run("stop", f"systemctl stop {service_name}")
            await conn.run("sleep 1", check=False)
            yield event("ok", "stop", message="Serviço parado")

            # 2. Upload updated source files
            yield event("running", "upload", message="Enviando código-fonte atualizado")
            async with conn.start_sftp_client() as sftp:
                for fname in go_files:
                    local_path = os.path.join(agent_dir, fname)
                    await sftp.put(local_path, f"{remote_dir}/{fname}")
                await sftp.put(go_mod, f"{remote_dir}/go.mod")
            yield event("ok", "upload", message="Arquivos atualizados")

            # 3. Compile
            yield event("running", "compile", message="Recompilando agente")
            r = await run("compile", f"cd {remote_dir} && go build -o {binary_path} . 2>&1")
            if r.exit_status != 0:
                err = (r.stdout or r.stderr or "").strip()
                yield event("error", "compile", message=f"Falha na compilação: {err[:200]}")
                yield event("done", "restart-agent", success=False, message="Compilação falhou")
                return
            yield event("ok", "compile", message="Binário recompilado")

            # 4. Update systemd unit with new URL
            yield event("running", "systemd", message="Atualizando configuração systemd")
            r = await run("systemd", f"cat <<'UNIT' > /etc/systemd/system/{service_name}.service\n{systemd_unit}UNIT")
            if r.exit_status != 0:
                yield event("error", "systemd", message="Falha ao atualizar unit file")
                yield event("done", "restart-agent", success=False, message="Falha no systemd")
                return
            yield event("ok", "systemd", message="Configuração atualizada")

            # 5. Restart service
            yield event("running", "start", message="Iniciando serviço")
            await run("daemon-reload", "systemctl daemon-reload")
            r = await run("start", f"systemctl restart {service_name}")
            await conn.run("sleep 2", check=False)

            status = await run("status", f"systemctl is-active {service_name}")
            active = (status.stdout or "").strip() == "active"

            if active:
                yield event("ok", "start", message="Serviço reiniciado com sucesso")
                yield event("done", "restart-agent", success=True, message="Agente reiniciado com nova URL")
            else:
                yield event("warn", "start", message="Serviço pode estar com problemas")
                yield event("done", "restart-agent", success=True, message="Recompilação concluída (verifique o agente)")

    except (asyncssh.Error, OSError) as exc:
        yield event("error", "ssh", message=str(exc))
        yield event("done", "restart-agent", success=False, message=str(exc))


async def stream_install_unsubscribe(node: Node, panel_url: str):
    """Install nginx on the VPS and create a dedicated /unsubscribe site."""
    import json as _json

    def event(type: str, label: str, **kwargs) -> str:
        return _json.dumps({"type": type, "label": label, **kwargs}) + "\n"

    from urllib.parse import urlparse
    panel_base = panel_url.rstrip("/")
    panel_host = urlparse(panel_base).netloc  # e.g. mind-doubt-semi-length.trycloudflare.com
    domain = node.domain
    mail_host = f"mail.{domain}"

    # Full nginx site config for the domain — proxies /unsubscribe to the panel
    nginx_site = f"""server {{
    listen 80;
    server_name {domain};

    location /unsubscribe {{
        proxy_pass {panel_base}/unsubscribe;
        proxy_set_header Host {panel_host};
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_ssl_server_name on;
    }}

    location / {{
        return 444;
    }}
}}
"""
    site_name = domain
    site_avail = f"/etc/nginx/sites-available/{site_name}"
    site_enabled = f"/etc/nginx/sites-enabled/{site_name}"

    try:
        async with asyncssh.connect(**_connect_kwargs(node)) as conn:
            async def run(label, cmd):
                return await conn.run(cmd, check=False)

            # 1. Install nginx if not present
            yield event("running", "nginx-install", message="Verificando nginx")
            r = await run("check-nginx", "which nginx 2>/dev/null && echo ok || echo missing")
            if "missing" in (r.stdout or ""):
                yield event("running", "nginx-install", message="Instalando nginx")
                r = await run("apt-nginx", "apt-get install -y nginx 2>&1")
                if r.exit_status != 0:
                    yield event("error", "nginx-install", message="Falha ao instalar nginx")
                    yield event("done", "install-unsubscribe", success=False, message="Falha ao instalar nginx")
                    return
            yield event("ok", "nginx-install", message="nginx disponível")

            # 2. Remove old config if exists
            yield event("running", "nginx-cleanup", message="Removendo configuração antiga")
            await run("rm-avail", f"rm -f {site_avail}")
            await run("rm-enabled", f"rm -f {site_enabled}")
            await run("certbot-delete", f"certbot delete --cert-name {domain} --non-interactive 2>&1 || true")
            yield event("ok", "nginx-cleanup", message="Configuração anterior removida")

            # 3. Write site config via heredoc (avoids quoting issues)
            yield event("running", "nginx-site", message=f"Criando site {site_name}")
            write_cmd = f"cat > {site_avail} <<'NGINXEOF'\n{nginx_site}NGINXEOF"
            r = await run("write-site", write_cmd)
            if r.exit_status != 0:
                yield event("error", "nginx-site", message="Falha ao criar arquivo de site")
                yield event("done", "install-unsubscribe", success=False, message="Falha ao criar site nginx")
                return
            yield event("ok", "nginx-site", message=f"Site salvo em {site_avail}")

            # 3. Enable site
            yield event("running", "nginx-enable", message="Ativando site")
            await run("enable", f"ln -sf {site_avail} {site_enabled}")

            # 4. Remove default site if it conflicts
            await run("rm-default", "rm -f /etc/nginx/sites-enabled/default")
            yield event("ok", "nginx-enable", message="Site ativado")

            # 5. Test config
            yield event("running", "nginx-test", message="Testando configuração nginx")
            r = await run("nginx-test", "nginx -t 2>&1")
            output = (r.stdout or r.stderr or "").strip()
            if r.exit_status != 0:
                yield event("error", "nginx-test", message=f"nginx -t: {output[:300]}")
                yield event("done", "install-unsubscribe", success=False, message=f"nginx -t falhou: {output[:200]}")
                return
            yield event("ok", "nginx-test", message="Configuração válida")

            # 6. Open firewall ports 80 and 443
            yield event("running", "firewall", message="Abrindo portas 80 e 443")
            await run("ufw-80", "ufw allow 80/tcp 2>&1 || true")
            await run("ufw-443", "ufw allow 443/tcp 2>&1 || true")
            await run("iptables-80", "iptables -I INPUT -p tcp --dport 80 -j ACCEPT 2>&1 || true")
            await run("iptables-443", "iptables -I INPUT -p tcp --dport 443 -j ACCEPT 2>&1 || true")
            yield event("ok", "firewall", message="Portas 80/443 liberadas")

            # 7. Reload / start nginx
            yield event("running", "nginx-reload", message="Iniciando nginx")
            await run("enable-service", "systemctl enable nginx")
            r = await run("reload", "systemctl reload nginx 2>&1 || systemctl start nginx 2>&1")
            if r.exit_status != 0:
                yield event("warn", "nginx-reload", message="nginx reload com aviso (pode estar ok)")
            else:
                yield event("ok", "nginx-reload", message="nginx recarregado")

            # 8. Install certbot and obtain SSL certificate
            yield event("running", "certbot", message="Instalando certbot")
            r = await run("apt-certbot", "apt-get install -y certbot python3-certbot-nginx 2>&1")
            if r.exit_status != 0:
                yield event("warn", "certbot", message="Falha ao instalar certbot — continuando sem HTTPS")
            else:
                yield event("ok", "certbot", message="Certbot instalado")
                yield event("running", "certbot-run", message=f"Obtendo certificado SSL para {domain}")
                cert_cmd = (
                    f"certbot --nginx -d {domain} "
                    f"--non-interactive --agree-tos --email admin@{domain} "
                    f"--redirect 2>&1"
                )
                r = await run("certbot-run", cert_cmd)
                output = (r.stdout or "").strip()
                if r.exit_status != 0:
                    yield event("warn", "certbot-run", message=f"certbot: {output[-200:] or 'erro'} — verifique DNS e tente reinstalar")
                else:
                    yield event("ok", "certbot-run", message="Certificado SSL emitido e nginx atualizado")

            unsub_url = f"https://{domain}/unsubscribe"
            yield event("done", "install-unsubscribe", success=True,
                        message=f"✓ Rota /unsubscribe instalada — {unsub_url}",
                        unsub_url=unsub_url)

    except (asyncssh.Error, OSError) as exc:
        yield event("error", "ssh", message=str(exc))
        yield event("done", "install-unsubscribe", success=False, message=str(exc))
